from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.onboarding_runtime import OnboardingRuntime
from app.models.workspace import Workspace
from app.api.routes.onboarding import _runtime_stream_fingerprint


async def test_onboarding_progress_returns_completed_when_workspace_done(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
):
    workspace.onboarding_completed = True
    await db_session.flush()

    res = await client.get("/api/onboarding/progress", headers=auth_headers)

    assert res.status_code == 200
    assert res.json() == {
        "phase": "done",
        "percent": 100,
        "completed": True,
        "is_running": False,
    }


async def test_onboarding_progress_returns_not_started_when_workspace_not_done(
    client: AsyncClient,
    auth_headers: dict[str, str],
):
    expected = {
        "phase": "not_started",
        "percent": 0,
        "completed": False,
        "is_running": False,
    }
    with patch("app.api.routes.onboarding.get_progress_response", new=AsyncMock(return_value=expected)):
        res = await client.get("/api/onboarding/progress", headers=auth_headers)

    assert res.status_code == 200
    assert res.json() == expected


async def test_onboarding_runtime_returns_idle_projection_when_missing(
    client: AsyncClient,
    auth_headers: dict[str, str],
    workspace: Workspace,
):
    expected_progress = {
        "workspace_id": workspace.id,
        "phase": "not_started",
        "percent": 0,
        "completed": False,
        "errors": [],
    }
    with patch("app.api.routes.onboarding.load_progress", new=AsyncMock(return_value=None)):
        res = await client.get("/api/onboarding/runtime", headers=auth_headers)

    assert res.status_code == 200
    data = res.json()
    assert data["schema_version"] == "onboarding_runtime.v1"
    assert data["workspace_id"] == workspace.id
    assert data["state"] == "idle"
    assert data["phase"] == "not_started"
    assert data["percent"] == 0
    assert data["current_stage_id"] == "auth_linked"
    assert {stage["id"] for stage in data["stages"]} >= {
        "auth_linked",
        "dialogs_scanned",
        "contacts_classified",
        "voice_profile_ready",
        "completed",
    }
    assert data["is_running"] is False
    assert data["can_requeue"] is False
    for key, value in expected_progress.items():
        assert data["progress"][key] == value


async def test_onboarding_runtime_endpoint_persists_reconciled_progress_floor(
    client: AsyncClient,
    auth_headers: dict[str, str],
    workspace: Workspace,
):
    stale_progress = {
        "workspace_id": workspace.id,
        "phase": "reading_dialogs",
        "percent": 35,
        "completed": False,
        "contacts_found": 0,
        "customers_identified": 0,
        "errors": [],
    }
    reconciled_progress = {
        **stale_progress,
        "phase": "classifying_contacts",
        "percent": 45,
        "contacts_found": 1,
        "customers_identified": 1,
    }
    store_progress = AsyncMock()

    with patch("app.api.routes.onboarding.load_progress", new=AsyncMock(return_value=stale_progress)), \
         patch("app.api.routes.onboarding.reconcile_progress_with_db", new=AsyncMock(return_value=reconciled_progress)), \
         patch("app.api.routes.onboarding.store_progress", new=store_progress):
        res = await client.get("/api/onboarding/runtime", headers=auth_headers)

    assert res.status_code == 200
    data = res.json()
    assert data["progress"]["contacts_found"] == 1
    assert data["progress"]["customers_identified"] == 1
    assert next(
        stage for stage in data["stages"] if stage["id"] == "contacts_classified"
    )["status"] == "completed"
    store_progress.assert_awaited_once_with(workspace.id, reconciled_progress)


async def test_onboarding_runtime_returns_durable_failed_projection(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
):
    runtime = OnboardingRuntime(
        workspace_id=workspace.id,
        state="failed",
        phase="classifying_contacts",
        percent=45,
        attempt_count=2,
        max_attempts=3,
        last_error="gemini quota",
        progress_snapshot={
            "workspace_id": workspace.id,
            "phase": "classifying_contacts",
            "percent": 45,
            "completed": False,
            "errors": ["gemini quota"],
        },
    )
    db_session.add(runtime)
    await db_session.flush()

    with patch("app.api.routes.onboarding.load_progress", new=AsyncMock(return_value=None)):
        res = await client.get("/api/onboarding/runtime", headers=auth_headers)

    assert res.status_code == 200
    data = res.json()
    assert data["state"] == "failed"
    assert data["phase"] == "classifying_contacts"
    assert data["percent"] == 45
    assert data["attempt_count"] == 2
    assert data["max_attempts"] == 3
    assert data["last_error"] == "gemini quota"
    assert data["is_retryable"] is True
    assert data["can_requeue"] is True
    assert data["current_stage_id"] == "contacts_classified"
    contacts_stage = next(stage for stage in data["stages"] if stage["id"] == "contacts_classified")
    assert contacts_stage["status"] == "failed"
    assert contacts_stage["retryable"] is True
    assert contacts_stage["error"] == "gemini quota"
    catalog_stage = next(stage for stage in data["stages"] if stage["id"] == "catalog_extracted")
    assert catalog_stage["status"] == "not_applicable"


async def test_onboarding_runtime_exposes_durable_source_learning_queue(
    client: AsyncClient,
    auth_headers: dict[str, str],
    workspace: Workspace,
    monkeypatch,
):
    class _NoopSourceLearningRuntime:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def process_workspace_sources(self, **kwargs):
            return SimpleNamespace(
                processed_count=0,
                review_ready_count=0,
                retrying_count=0,
                failed_count=0,
            )

    monkeypatch.setattr(
        "app.api.routes.auth.OnboardingSourceLearningRuntimeService",
        _NoopSourceLearningRuntime,
    )

    payload = {
        "name": "Nafis Liboslar",
        "category": "fashion",
        "monthly_revenue_band": "from_10m_to_50m",
        "phone_number": "+998901234567",
        "password": "strongpass123",
        "business_profile": {
            "offer_summary": "Ayollar kiyimi va aksessuarlar",
            "message_volume": "10_50",
            "reply_team_size": "owner_only",
            "region": "Toshkent",
            "preferred_language": "uzbek_latin",
            "tone": "short_warm",
        },
        "preferences": {"reply_mode": "draft", "safe_autopilot": False},
        "sources": {
            "items": [
                {
                    "kind": "website",
                    "label": "Asosiy sayt",
                    "url": "https://nafis.example/shop",
                },
                {
                    "kind": "voice_note",
                    "label": "Ovozli qoida",
                    "transcript": "Chegirma faqat qaytgan mijozlarga.",
                },
            ],
        },
    }

    completed = await client.post(
        "/api/auth/complete-onboarding",
        json=payload,
        headers=auth_headers,
    )
    assert completed.status_code == 200

    with patch("app.api.routes.onboarding.load_progress", new=AsyncMock(return_value=None)):
        res = await client.get("/api/onboarding/runtime", headers=auth_headers)

    assert res.status_code == 200
    data = res.json()
    assert data["source_learning"]["schema_version"] == "onboarding_source_learning.v1"
    assert data["learned_review"]["schema_version"] == "onboarding_learned_review.v1"
    assert data["learned_review"]["status"] == "empty"
    assert data["source_learning"]["status"] == "learning"
    assert data["source_learning"]["summary"]["total"] == 2
    assert data["source_learning"]["summary"]["learning"] == 2
    assert [source["kind"] for source in data["source_learning"]["sources"]] == [
        "website",
        "voice_note",
    ]
    assert {source["status"] for source in data["source_learning"]["sources"]} == {"learning"}

    catalog_stage = next(stage for stage in data["stages"] if stage["id"] == "catalog_extracted")
    knowledge_stage = next(stage for stage in data["stages"] if stage["id"] == "knowledge_extracted")
    embeddings_stage = next(stage for stage in data["stages"] if stage["id"] == "embeddings_ready")
    assert catalog_stage["status"] == "running"
    assert knowledge_stage["status"] == "running"
    assert embeddings_stage["status"] == "running"


def test_onboarding_runtime_stream_fingerprint_tracks_learning_changes() -> None:
    base_payload = {
        "state": "running",
        "phase": "reading_sources",
        "percent": 40,
        "current_stage_id": "knowledge_extracted",
        "is_running": True,
        "is_retryable": False,
        "is_dlq": False,
        "attempt_count": 1,
        "source_learning": {
            "status": "learning",
            "percent": 35,
            "summary": {"total": 1, "learning": 1},
            "events": [
                {
                    "id": "source:1",
                    "title": "Satstation.io o‘qilmoqda",
                    "detail": "Saytdan katalog va bilim ajratilmoqda.",
                    "status": "running",
                }
            ],
            "sources": [
                {
                    "source_ref": "source:1",
                    "status": "learning",
                    "stage": "extracting",
                    "attempt_count": 1,
                    "updated_at": "2026-05-18T12:00:00Z",
                    "catalog_candidate_count": 0,
                    "memory_candidate_count": 0,
                    "source_unit_count": 1,
                    "source_media_count": 0,
                }
            ],
        },
        "learned_review": {"summary": {"total_review_items": 0}},
    }
    event_changed_payload = {
        **base_payload,
        "source_learning": {
            **base_payload["source_learning"],
            "events": [
                {
                    "id": "source:1",
                    "title": "Satstation.io o‘qildi",
                    "detail": "2 ta bilim taklifi topildi.",
                    "status": "done",
                }
            ],
        },
    }
    extracted_count_changed_payload = {
        **base_payload,
        "source_learning": {
            **base_payload["source_learning"],
            "sources": [
                {
                    **base_payload["source_learning"]["sources"][0],
                    "status": "review_ready",
                    "catalog_candidate_count": 2,
                    "memory_candidate_count": 3,
                }
            ],
        },
    }

    assert _runtime_stream_fingerprint(base_payload) != _runtime_stream_fingerprint(event_changed_payload)
    assert _runtime_stream_fingerprint(base_payload) != _runtime_stream_fingerprint(extracted_count_changed_payload)


async def test_onboarding_learned_review_action_rejects_proposed_fact(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    created = await client.post(
        "/api/business-brain/memory/facts",
        json={
            "fact_id": "knowledge:review:delivery",
            "fact_type": "knowledge_fact",
            "entity_ref": "business:delivery",
            "value": {
                "topic": "yetkazib berish",
                "answer": "Toshkent ichida yetkazib berish bor.",
            },
            "source_refs": ["source_unit:onboarding:delivery"],
            "source": "ai_proposal",
            "status": "proposed",
            "approval_state": "proposed",
            "confidence": 0.8,
            "risk_tier": "medium",
            "correlation_id": "corr-review-api-create",
            "idempotency_key": "review-api-create",
        },
        headers=auth_headers,
    )
    assert created.status_code == 200

    response = await client.post(
        "/api/onboarding/learned-review/actions",
        json={
            "action": "reject",
            "target_type": "fact",
            "target_ref": "knowledge:review:delivery",
            "correlation_id": "corr-review-api-reject",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["action"]["rejected_count"] == 1
    assert data["learned_review"]["summary"]["knowledge"] == 0
