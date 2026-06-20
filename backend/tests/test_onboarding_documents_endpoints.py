"""Onboarding documents routes — generate / snapshot / stream.

Mirrors the seeding + isolation patterns from test_brain_documents_endpoints.py
and drives the OnboardingDocumentsService orchestrator directly for the
deterministic generate→snapshot integration test.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.commercial_spine import BusinessBrainFactRecord
from app.models.workspace import Workspace
from app.modules.brain.business_document import BusinessDocumentService
from app.modules.brain.contracts import BusinessSectionDraft, SkillLearnReport
from app.modules.brain.onboarding_documents import OnboardingDocumentsService
from app.modules.business_brain.memory import ACTIVE_STATUSES

pytestmark = pytest.mark.asyncio

_ACTIVE_STATUS = ACTIVE_STATUSES[0]


async def _seed_business_section(
    db_session: AsyncSession,
    workspace: Workspace,
    *,
    section_key: str = "overview",
    body: str = "Seeded section body",
) -> None:
    await BusinessDocumentService(db_session).persist_section(
        workspace_id=workspace.id,
        draft=BusinessSectionDraft(
            section_key=section_key,
            body=body,
            evidence_refs=[],
            confidence=0.7,
        ),
    )
    await db_session.flush()


class TestGetOnboardingDocuments:
    async def test_get_documents_returns_projection(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        r = await client.get("/api/onboarding/documents", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["schema_version"] == "onboarding_documents.v1"
        assert body["documents"]["business"]["total"] == 10
        assert body["documents"]["agent"]["total"] == 6

    async def test_get_documents_requires_auth(self, client: AsyncClient) -> None:
        r = await client.get("/api/onboarding/documents")
        assert r.status_code == 401

    async def test_get_documents_workspace_isolation(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        auth_headers_b: dict[str, str],
    ) -> None:
        # Seed a business section for workspace A only.
        await _seed_business_section(
            db_session, workspace, section_key="overview", body="A-only-secret"
        )
        # Workspace B sees only its own (empty) business sections — no leak.
        r = await client.get("/api/onboarding/documents", headers=auth_headers_b)
        assert r.status_code == 200
        body = r.json()
        sections = body["documents"]["business"]["sections"]
        assert all(s["status"] == "pending" for s in sections)
        assert body["documents"]["business"]["proposed"] == 0


class TestGenerateOnboardingDocuments:
    async def test_generate_returns_202_started(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Patch the background runner so no real generation runs and we don't
        # depend on create_task timing.
        monkeypatch.setattr(
            "app.api.routes.onboarding._run_document_generation",
            AsyncMock(),
        )
        r = await client.post(
            "/api/onboarding/documents/generate", headers=auth_headers
        )
        assert r.status_code == 202
        assert r.json() == {"status": "started"}

    async def test_generate_requires_auth(self, client: AsyncClient) -> None:
        r = await client.post("/api/onboarding/documents/generate")
        assert r.status_code == 401

    async def test_generate_bootstraps_default_agents_without_doc_sections(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Background generation is a no-op so only the synchronous bootstrap runs.
        monkeypatch.setattr(
            "app.api.routes.onboarding._run_document_generation",
            AsyncMock(),
        )
        r = await client.post(
            "/api/onboarding/documents/generate", headers=auth_headers
        )
        assert r.status_code == 202

        # The 5 default agents are created before generation is scheduled.
        agents = (
            await db_session.scalars(
                select(Agent).where(Agent.workspace_id == workspace.id)
            )
        ).all()
        assert {a.agent_type for a in agents} >= {
            "seller",
            "support",
            "catalog_update",
            "follow_up",
            "bi",
        }

        # The bootstrap step writes ZERO document sections — doc-gen owns those.
        section_count = await db_session.scalar(
            select(func.count())
            .select_from(AgentDocumentSection)
            .where(AgentDocumentSection.workspace_id == workspace.id)
        )
        assert section_count == 0

    async def test_generate_agent_bootstrap_is_workspace_isolated(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace_b: Workspace,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "app.api.routes.onboarding._run_document_generation",
            AsyncMock(),
        )
        r = await client.post(
            "/api/onboarding/documents/generate", headers=auth_headers
        )
        assert r.status_code == 202

        # Workspace A's generate call never created agents for workspace B.
        b_agents = await db_session.scalar(
            select(func.count())
            .select_from(Agent)
            .where(Agent.workspace_id == workspace_b.id)
        )
        assert b_agents == 0


async def _seed_active_fact(
    db_session: AsyncSession,
    workspace: Workspace,
    *,
    fact_type: str = "business_source_fact",
    entity_ref: str = "src:1",
    value: dict | None = None,
) -> None:
    db_session.add(
        BusinessBrainFactRecord(
            fact_id=f"{fact_type}:{entity_ref}",
            workspace_id=workspace.id,
            fact_type=fact_type,
            entity_ref=entity_ref,
            value=value or {"text": "Biz kofe sotamiz"},
            confidence=0.9,
            status=_ACTIVE_STATUS,
            risk_tier="low",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            idempotency_key=f"test:{fact_type}:{entity_ref}",
        )
    )
    await db_session.flush()


class TestOnboardingDocumentsIntegration:
    async def test_generate_then_snapshot_shows_proposed_sections(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seed a couple of active facts so synthesis has evidence context.
        await _seed_active_fact(
            db_session, workspace, entity_ref="src:1", value={"text": "Biz kofe sotamiz"}
        )
        await _seed_active_fact(
            db_session, workspace, entity_ref="src:2", value={"text": "Narx 25 ming"}
        )

        # Per-section LLM echo: synthesize_section overwrites section_key with the
        # spec key, so the echo's value does not matter — all 10 land under own key.
        monkeypatch.setattr(
            "app.modules.brain.business_document.generate_structured_json",
            AsyncMock(
                return_value={
                    "section_key": "overview",
                    "body": "matn",
                    "evidence_refs": [],
                    "confidence": 0.7,
                }
            ),
        )
        # No real skill learning / no real Redis during orchestration + projection.
        monkeypatch.setattr(
            "app.modules.brain.onboarding_documents.SkillLearnerService.learn",
            AsyncMock(
                return_value=SkillLearnReport(pairs_used=0, clusters=0, candidates=0)
            ),
        )
        monkeypatch.setattr(
            "app.modules.brain.onboarding_documents.store_docgen_progress",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "app.modules.brain.onboarding_documents.load_docgen_progress",
            AsyncMock(
                return_value={
                    "running": False,
                    "current_doc": None,
                    "current_section": None,
                    "error": None,
                    "skill_status": "pending",
                    "skill_candidates": 0,
                }
            ),
        )

        await OnboardingDocumentsService(db_session).generate_all(
            workspace_id=workspace.id
        )
        await db_session.flush()

        resp = await client.get("/api/onboarding/documents", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["documents"]["business"]["proposed"] == 10
        assert body["percent"] > 0
