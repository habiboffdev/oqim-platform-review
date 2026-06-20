"""Tests for onboarding_documents_progress Redis store."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.models.agent import Agent
from app.modules.brain.agent_document import AgentDocumentBuilderService
from app.modules.brain.business_document import BusinessDocumentService
from app.modules.brain.contracts import (
    AGENT_SECTIONS,
    AgentSectionDraft,
    BusinessSectionDraft,
    SkillLearnReport,
)
from app.modules.brain.onboarding_documents import OnboardingDocumentsService
from app.services.onboarding_documents_progress import (
    default_docgen_progress,
    load_docgen_progress,
    store_docgen_progress,
)

pytestmark = pytest.mark.asyncio


def _make_redis_store() -> tuple[AsyncMock, dict]:
    """Return a mock Redis client backed by an in-memory dict.

    The mock supports get / setex / aclose, routing through a shared store
    dict so store_docgen_progress and load_docgen_progress interact correctly.
    """
    store: dict = {}

    async def _get(key: str):
        return store.get(key)

    async def _setex(key: str, ttl: int, value: str):
        store[key] = value

    async def _aclose():
        pass

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=_get)
    redis.setex = AsyncMock(side_effect=_setex)
    redis.aclose = AsyncMock(side_effect=_aclose)
    return redis, store


class TestDocgenProgress:
    async def test_round_trip_stores_and_loads_values(self):
        redis, _store = _make_redis_store()
        payload = {
            "running": True,
            "current_doc": "business",
            "current_section": "overview",
            "error": None,
            "skill_status": "running",
            "skill_candidates": 3,
        }

        with patch(
            "app.services.onboarding_documents_progress.get_redis",
            new=AsyncMock(return_value=redis),
        ):
            await store_docgen_progress(1, payload)
            result = await load_docgen_progress(1)

        assert result["running"] is True
        assert result["current_doc"] == "business"
        assert result["current_section"] == "overview"
        assert result["skill_status"] == "running"
        assert result["skill_candidates"] == 3

    async def test_missing_key_returns_defaults(self):
        redis, _store = _make_redis_store()

        with patch(
            "app.services.onboarding_documents_progress.get_redis",
            new=AsyncMock(return_value=redis),
        ):
            result = await load_docgen_progress(999)

        assert result == default_docgen_progress()

    async def test_partial_stored_value_merges_defaults(self):
        """Stored dict with a subset of keys falls back to defaults for missing ones."""
        redis, _store = _make_redis_store()
        partial = {"running": True, "current_doc": "agent"}

        with patch(
            "app.services.onboarding_documents_progress.get_redis",
            new=AsyncMock(return_value=redis),
        ):
            await store_docgen_progress(2, partial)
            result = await load_docgen_progress(2)

        assert result["running"] is True
        assert result["current_doc"] == "agent"
        # Missing keys must fall back to defaults
        assert result["current_section"] is None
        assert result["error"] is None
        assert result["skill_status"] == "pending"
        assert result["skill_candidates"] == 0

    async def test_store_uses_correct_key_and_ttl(self):
        redis, _store = _make_redis_store()

        with patch(
            "app.services.onboarding_documents_progress.get_redis",
            new=AsyncMock(return_value=redis),
        ):
            await store_docgen_progress(42, {"running": True})

        redis.setex.assert_awaited_once()
        call_args = redis.setex.await_args
        key, ttl, value = call_args.args
        assert key == "onboarding:docgen:42"
        assert ttl == 3600
        assert json.loads(value) == {"running": True}

    async def test_workspace_isolation(self):
        """Two workspaces do not share doc-gen progress."""
        redis_a, _store_a = _make_redis_store()
        redis_b, store_b = _make_redis_store()

        with patch(
            "app.services.onboarding_documents_progress.get_redis",
            new=AsyncMock(side_effect=[redis_a, redis_b, redis_a]),
        ):
            await store_docgen_progress(10, {"running": True, "current_doc": "business"})
            await store_docgen_progress(20, {"running": False, "current_doc": "agent"})
            result_10 = await load_docgen_progress(10)

        assert result_10["current_doc"] == "business"
        assert store_b.get("onboarding:docgen:10") is None


async def test_build_projection_reflects_persisted_and_generating(db_session, workspace, monkeypatch):
    bsvc = BusinessDocumentService(db_session)
    await bsvc.persist_section(workspace_id=workspace.id, draft=BusinessSectionDraft(section_key="overview", body="X"))
    await db_session.flush()
    monkeypatch.setattr(
        "app.modules.brain.onboarding_documents.load_docgen_progress",
        AsyncMock(return_value={"running": True, "current_doc": "business", "current_section": "what_we_sell", "error": None, "skill_status": "pending", "skill_candidates": 0}),
    )
    proj = await OnboardingDocumentsService(db_session).build_documents_projection(workspace_id=workspace.id)
    biz = {s["key"]: s["status"] for s in proj["documents"]["business"]["sections"]}
    assert biz["overview"] == "proposed"
    assert biz["what_we_sell"] == "generating"
    assert biz["catalog_sku_rules"] == "pending"
    assert proj["running"] is True
    assert proj["documents"]["business"]["total"] == 10
    assert proj["documents"]["agent"]["total"] == 6
    assert proj["schema_version"] == "onboarding_documents.v1"


async def test_generate_all_streams_sections_and_continues_on_section_failure(db_session, workspace, monkeypatch):
    async def flaky_section(self, *, workspace_id, section_key, fact_context):
        _ = workspace_id, fact_context
        if section_key == "voice_style":
            raise RuntimeError("provider down")
        return BusinessSectionDraft(section_key=section_key, body="ok")
    monkeypatch.setattr(BusinessDocumentService, "synthesize_section", flaky_section)
    monkeypatch.setattr("app.modules.brain.onboarding_documents.SkillLearnerService.learn", AsyncMock(return_value=SkillLearnReport(pairs_used=0, clusters=0, candidates=0)))
    monkeypatch.setattr("app.modules.brain.onboarding_documents.store_docgen_progress", AsyncMock())
    svc = OnboardingDocumentsService(db_session)
    await svc.generate_all(workspace_id=workspace.id)
    await db_session.flush()
    rows = {r.section_key: r.body for r in await BusinessDocumentService(db_session)._load_sections(workspace_id=workspace.id)}
    assert len(rows) == 10  # all sections persisted despite the failure
    assert "voice_style" in rows and "yetishmadi" in rows["voice_style"].lower()  # honest placeholder


async def test_generate_all_writes_agent_md_for_every_agent(db_session, workspace, monkeypatch):
    """Every workspace agent gets AGENT.md, not just the first (focus) agent."""

    async def fake_biz_section(self, *, workspace_id, section_key, fact_context):
        _ = workspace_id, fact_context
        return BusinessSectionDraft(section_key=section_key, body="ok")

    async def fake_agent_section(self, *, workspace_id, agent_context, business_context, section_key):
        _ = workspace_id, agent_context, business_context
        return AgentSectionDraft(section_key=section_key, body=f"agent-{section_key}")

    agents = [Agent(workspace_id=workspace.id, name=n) for n in ("Sotuvchi", "Yordam", "Katalog")]
    for agent in agents:
        db_session.add(agent)
    await db_session.flush()

    monkeypatch.setattr(BusinessDocumentService, "synthesize_section", fake_biz_section)
    monkeypatch.setattr(AgentDocumentBuilderService, "synthesize_section", fake_agent_section)
    monkeypatch.setattr(
        "app.modules.brain.onboarding_documents.SkillLearnerService.learn",
        AsyncMock(return_value=SkillLearnReport(pairs_used=0, clusters=0, candidates=0)),
    )
    monkeypatch.setattr("app.modules.brain.onboarding_documents.store_docgen_progress", AsyncMock())

    await OnboardingDocumentsService(db_session).generate_all(workspace_id=workspace.id)
    await db_session.flush()

    asvc = AgentDocumentBuilderService(db_session)
    expected = {s.key for s in AGENT_SECTIONS}
    for agent in agents:
        rows = await asvc._load_sections(workspace_id=workspace.id, agent_id=agent.id)
        assert {r.section_key for r in rows} == expected, f"agent {agent.name} missing AGENT.md sections"
