from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial_spine import (
    BusinessBrainFactRecord,
    BusinessBrainIndexRecord,
    BusinessBrainProjectionRecord,
    BusinessBrainUpdateRecord,
)
from app.models.workspace import Workspace
from app.modules.business_brain.contracts import (
    BusinessBrainFactUpdateInput,
)
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import SourceUnitRebuildRequest
from app.modules.business_brain.write_service import BusinessBrainWriteService
from app.modules.commercial_spine.repository import CommercialSpineRepository


def _update_input(
    *,
    workspace: Workspace,
    update_id: str,
    fact_id: str,
    value: dict[str, Any],
    status: str = "active",
    approval_state: str = "confirmed",
    source: str = "manual",
    entity_ref: str = "business:delivery",
    fact_type: str = "delivery_policy",
    source_refs: list[str] | None = None,
    valid_from: datetime | None = None,
    supersedes_fact_id: str | None = None,
) -> BusinessBrainFactUpdateInput:
    return BusinessBrainFactUpdateInput(
        update_id=update_id,
        fact_id=fact_id,
        workspace_id=workspace.id,
        fact_type=fact_type,
        entity_ref=entity_ref,
        value=value,
        confidence=0.92,
        status=status,
        risk_tier="low",
        source=source,
        approval_state=approval_state,
        source_refs=source_refs or ["owner_message:phase2"],
        idempotency_key=f"{update_id}:idem",
        valid_from=valid_from,
        supersedes_fact_id=supersedes_fact_id,
        actor_type="owner",
        actor_ref=f"workspace:{workspace.id}",
        correlation_id=f"corr:{update_id}",
    )


async def test_manual_business_fact_update_api_confirms_and_rebuilds_projection(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    response = await client.post(
        "/api/business-brain/facts/manual",
        headers=auth_headers,
        json={
            "fact_id": "fact-manual-api-phase2",
            "update_id": "update-manual-api-phase2",
            "fact_type": "delivery_policy",
            "entity_ref": "business:delivery",
            "value": {"text": "Yetkazib berish 24 soat ichida."},
            "confidence": 1.0,
            "risk_tier": "low",
            "source_refs": ["owner_message:manual-api"],
            "idempotency_key": "manual-api-phase2",
            "correlation_id": "corr-manual-api-phase2",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fact"]["status"] == "active"
    assert payload["update"]["approval_state"] == "confirmed"
    assert payload["projection"]["state"] == {
        "delivery_policy": {"text": "Yetkazib berish 24 soat ichida."}
    }

    update = await db_session.scalar(
        select(BusinessBrainUpdateRecord).where(
            BusinessBrainUpdateRecord.workspace_id == workspace.id,
            BusinessBrainUpdateRecord.update_id == "update-manual-api-phase2",
        )
    )
    assert update is not None
    assert update.raw_update["actor_ref"] == f"workspace:{workspace.id}"
    assert update.raw_update["correlation_id"] == "corr-manual-api-phase2"


async def test_supersession_keeps_history_and_replay_chooses_current_fact(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = BusinessBrainWriteService(
        repository=CommercialSpineRepository(db_session),
    )
    past = datetime.now(timezone.utc) - timedelta(days=5)
    replacement_time = datetime.now(timezone.utc)
    old = _update_input(
        workspace=workspace,
        update_id="update-old-policy-phase2",
        fact_id="fact-old-policy-phase2",
        value={"text": "Eski yetkazib berish siyosati."},
        valid_from=past,
    )
    new = _update_input(
        workspace=workspace,
        update_id="update-new-policy-phase2",
        fact_id="fact-new-policy-phase2",
        value={"text": "Yangi yetkazib berish siyosati."},
        valid_from=replacement_time,
        supersedes_fact_id="fact-old-policy-phase2",
    )

    await service.apply(old)
    await service.apply(new)

    historical = await service.fact_at(
        workspace_id=workspace.id,
        entity_ref="business:delivery",
        fact_type="delivery_policy",
        at=past + timedelta(hours=1),
    )
    current = await service.rebuild_projection(
        workspace_id=workspace.id,
        projection_ref="business_brain:business:delivery",
        projection_type="business_brain",
        entity_ref="business:delivery",
    )

    assert historical is not None
    assert historical.fact_id == "fact-old-policy-phase2"
    assert current.state == {
        "delivery_policy": {"text": "Yangi yetkazib berish siyosati."}
    }
    old_row = await db_session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.fact_id == "fact-old-policy-phase2"
        )
    )
    assert old_row is not None
    assert old_row.status == "superseded"
    assert old_row.valid_until is not None


async def test_index_contract_records_per_unit_degraded_state_without_blocking_reads(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    write = BusinessBrainWriteService(
        repository=CommercialSpineRepository(db_session),
    )
    await write.apply(
        _update_input(
            workspace=workspace,
            update_id="update-index-phase2",
            fact_id="fact-index-phase2",
            fact_type="catalog_source",
            value={"text": "Post, rasm, hujjat va voice note manbalari."},
            source_refs=["post:1", "image:1", "document:1", "voice:1"],
        )
    )
    rebuilt = await BusinessBrainMemoryService(
        repository=CommercialSpineRepository(db_session),
    ).rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["catalog_source"],
            candidate_fact_ids=["fact-index-phase2"],
            degraded_units={"image:1": "embedding_provider_unavailable"},
        )
    )
    facts = await CommercialSpineRepository(db_session).list_facts(
        workspace_id=workspace.id,
        entity_ref="business:delivery",
    )

    assert len(rebuilt.source_units) == 4
    assert {record.source_refs[0] for record in rebuilt.source_units} == {
        "post:1",
        "image:1",
        "document:1",
        "voice:1",
    }
    assert any(record.state == "degraded" for record in rebuilt.source_units)
    assert facts
    assert await _count_index_records(db_session) == 4


async def test_business_brain_read_model_api_is_workspace_scoped_and_honest(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_headers_b: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = BusinessBrainWriteService(
        repository=CommercialSpineRepository(db_session),
    )
    await service.apply(
        _update_input(
            workspace=workspace,
            update_id="update-read-phase2",
            fact_id="fact-read-phase2",
            value={"text": "Read model siyosati."},
            source_refs=["owner_message:read"],
        )
    )
    await db_session.flush()

    own_list = await client.get("/api/business-brain/facts", headers=auth_headers)
    other_list = await client.get("/api/business-brain/facts", headers=auth_headers_b)
    own_detail = await client.get(
        "/api/business-brain/facts/fact-read-phase2",
        headers=auth_headers,
    )
    other_detail = await client.get(
        "/api/business-brain/facts/fact-read-phase2",
        headers=auth_headers_b,
    )

    assert own_list.status_code == 200
    assert own_list.json()["items"][0]["fact_id"] == "fact-read-phase2"
    assert own_list.json()["items"][0]["source_refs"] == ["owner_message:read"]
    assert other_list.status_code == 200
    assert other_list.json()["items"] == []
    assert own_detail.status_code == 200
    detail = own_detail.json()
    assert detail["fact"]["confidence"] == 0.92
    assert detail["fact"]["freshness"]["state"] == "fresh"
    assert detail["index_state"] == "unavailable"
    assert detail["extraction_state"] == "unavailable"
    assert other_detail.status_code == 404


async def test_business_brain_fact_review_action_edits_proposed_fact(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = BusinessBrainWriteService(
        repository=CommercialSpineRepository(db_session),
    )
    await service.apply(
        _update_input(
            workspace=workspace,
            update_id="update-review-fact",
            fact_id="fact-review-kb",
            fact_type="knowledge_fact",
            entity_ref="knowledge:mentor-sla",
            value={
                "topic": "Mentor javobi",
                "answer": "Mentorlar tez javob beradi.",
            },
            status="proposed",
            approval_state="proposed",
            source="ai_proposal",
            source_refs=["source_unit:support"],
        )
    )
    await db_session.flush()

    response = await client.post(
        "/api/business-brain/facts/review-actions",
        headers=auth_headers,
        json={
            "action": "edit",
            "target_ref": "fact-review-kb",
            "value_patch": {
                "answer": "Mentorlar 24 soat ichida javob beradi.",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_type"] == "fact"
    assert payload["target_ref"] == "fact-review-kb"
    assert payload["applied_count"] == 1
    assert payload["edited_count"] == 1

    fact = await db_session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace.id,
            BusinessBrainFactRecord.fact_id == "fact-review-kb",
        )
    )
    assert fact is not None
    assert fact.status == "active"
    assert fact.value["answer"] == "Mentorlar 24 soat ichida javob beradi."

    update = await db_session.scalar(
        select(BusinessBrainUpdateRecord).where(
            BusinessBrainUpdateRecord.workspace_id == workspace.id,
            BusinessBrainUpdateRecord.target_ref == "fact:fact-review-kb",
            BusinessBrainUpdateRecord.update_id.like("learned-review:edit:%"),
        )
    )
    assert update is not None
    assert update.approval_state == "confirmed"
    assert update.raw_update["actor_type"] == "owner"


async def test_business_brain_fact_review_action_merges_proposed_non_catalog_fact(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = BusinessBrainWriteService(
        repository=CommercialSpineRepository(db_session),
    )
    await service.apply(
        _update_input(
            workspace=workspace,
            update_id="update-review-fact-merge-target",
            fact_id="fact-review-kb-primary",
            fact_type="knowledge_fact",
            entity_ref="knowledge:mentor-sla",
            value={
                "topic": "Mentor javobi",
                "answer": "Mentorlar 24 soat ichida javob beradi.",
            },
            status="proposed",
            approval_state="proposed",
            source="ai_proposal",
            source_refs=["source_unit:support:primary"],
        )
    )
    await service.apply(
        _update_input(
            workspace=workspace,
            update_id="update-review-fact-merge-copy",
            fact_id="fact-review-kb-copy",
            fact_type="knowledge_fact",
            entity_ref="knowledge:mentor-sla-copy",
            value={
                "topic": "Mentor SLA",
                "answer": "Mentor javobi 24 ish soati ichida.",
            },
            status="proposed",
            approval_state="proposed",
            source="ai_proposal",
            source_refs=["source_unit:support:copy"],
        )
    )
    await db_session.flush()

    response = await client.post(
        "/api/business-brain/facts/review-actions",
        headers=auth_headers,
        json={
            "action": "merge",
            "target_ref": "fact-review-kb-copy",
            "merge_into_ref": "fact-review-kb-primary",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_type"] == "fact"
    assert payload["target_ref"] == "fact-review-kb-copy"
    assert payload["merged_count"] == 1

    duplicate = await db_session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace.id,
            BusinessBrainFactRecord.fact_id == "fact-review-kb-copy",
        )
    )
    assert duplicate is not None
    assert duplicate.status == "rejected"
    assert duplicate.value["merged_into_ref"] == "fact-review-kb-primary"
    assert duplicate.value["merge_state"] == "merged"


async def test_business_brain_source_api_queues_csv_for_learning(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    response = await client.post(
        "/api/business-brain/sources",
        headers=auth_headers,
        json={
            "kind": "file",
            "label": "price-list.csv",
            "file_name": "price-list.csv",
            "content_type": "text/csv",
            "content_base64": base64.b64encode(
                b"name,price,currency\nAtlas koylak,250000,UZS"
            ).decode(),
            "byte_size": 42,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_ref"].startswith("brain:source:")
    fact = await db_session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace.id,
            BusinessBrainFactRecord.fact_id == payload["fact"]["fact_id"],
        )
    )
    assert fact is not None
    assert fact.entity_ref.startswith("workspace:source:brain:source:")
    assert fact.raw_fact["fact_type"] == "business_source_fact"
    assert fact.raw_fact["value"]["kind"] == "file"
    assert fact.raw_fact["value"]["input"]["file_name"] == "price-list.csv"
    assert fact.raw_fact["value"]["processing"]["state"] == "queued"
    projection = await db_session.scalar(
        select(BusinessBrainProjectionRecord).where(
            BusinessBrainProjectionRecord.workspace_id == workspace.id,
            BusinessBrainProjectionRecord.projection_type == "business_source_learning",
            BusinessBrainProjectionRecord.entity_ref == f"workspace:source:{payload['source_ref']}",
        )
    )
    assert projection is not None
    assert projection.state["source_ref"] == payload["source_ref"]
    assert projection.state["status"] == "queued"
    assert projection.state["stage"] == "queued"

    sources_response = await client.get(
        "/api/business-brain/sources",
        headers=auth_headers,
    )
    assert sources_response.status_code == 200
    sources = sources_response.json()
    assert sources["summary"]["total"] == 1
    assert sources["sources"][0]["source_ref"] == payload["source_ref"]
    assert sources["sources"][0]["status"] == "learning"


async def test_business_brain_source_api_preserves_telegram_channel_date_window(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    response = await client.post(
        "/api/business-brain/sources",
        headers=auth_headers,
        json={
            "kind": "telegram_channel",
            "label": "SATStation kanal",
            "handle": "@satstation",
            "date_from": "2026-05-01",
            "date_to": "2026-05-18",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    fact = await db_session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace.id,
            BusinessBrainFactRecord.fact_id == payload["fact"]["fact_id"],
        )
    )
    assert fact is not None
    assert fact.raw_fact["value"]["input"] == {
        "handle": "@satstation",
        "date_from": "2026-05-01",
        "date_to": "2026-05-18",
        "purpose": "brain_data",
    }


async def test_business_brain_audio_transcript_api_returns_editable_text(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeGateway:
        def __init__(self, **_: Any) -> None:
            pass

        async def generate(self, request: Any, *, output_model: Any) -> Any:
            captured["request"] = request
            captured["output_model"] = output_model
            return SimpleNamespace(
                status="ok",
                parsed_output={"transcript": "Yetkazish so'ralsa, avval tuman so'rang."},
                model_used="fixture-gemini",
                trace_id="trace-audio",
            )

    monkeypatch.setattr("app.api.routes.business_brain.LLMGateway", FakeGateway)

    response = await client.post(
        "/api/business-brain/sources/audio-transcript",
        headers=auth_headers,
        json={
            "content_base64": base64.b64encode(b"voice-bytes").decode(),
            "content_type": "audio/ogg",
            "file_name": "rule.ogg",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "business_brain_audio_transcript.v1"
    assert payload["status"] == "ready"
    assert payload["transcript"] == "Yetkazish so'ralsa, avval tuman so'rang."
    assert payload["model_used"] == "fixture-gemini"

    request = captured["request"]
    assert request.prompt_id == "media.voice_transcription"
    assert request.workflow_name == "onboarding_audio_transcription"
    assert request.output_schema_name == "VoiceTranscriptOutput"
    assert request.content_parts == [
        {
            "kind": "inline_data",
            "mime_type": "audio/ogg",
            "data_base64": base64.b64encode(b"voice-bytes").decode(),
            "file_name": "rule.ogg",
        }
    ]


def test_business_brain_phase2_has_no_direct_provider_or_semantic_shortcuts() -> None:
    root = Path(__file__).resolve().parents[1] / "app/modules/business_brain"
    banned_tokens = (
        "genai.Client(",
        ".models.generate_content(",
        "client.aio.models.generate_content(",
        "re.compile(",
        "re.search(",
        "keyword",
        "heuristic",
    )
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in banned_tokens):
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


async def _count_index_records(db_session: AsyncSession) -> int:
    return int(
        await db_session.scalar(select(func.count()).select_from(BusinessBrainIndexRecord))
        or 0
    )
