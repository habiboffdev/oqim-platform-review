"""The reconciler drains index_state='pending' facts: embed visible ones, prune
invisible ones, mark failed on embed error.
See docs/superpowers/specs/2026-05-25-automatic-fact-indexing-design.md."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.base import utc_now
from app.models.commercial_spine import BusinessBrainFactRecord, BusinessBrainIndexRecord
from app.models.workspace import Workspace
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.services.brain_index_reconciler import BrainIndexReconciler

pytestmark = pytest.mark.asyncio

_TEST_RECONCILER_BATCH_SIZE = 5_000


class _FakeEmbeddingService:
    async def embed_texts_batch(self, texts):
        return [[0.02] * 3072 for _t in texts]

    async def embed_text(self, text):
        return [0.02] * 3072


async def _index_count(db_session, workspace, fact_id: str) -> int:
    return await db_session.scalar(
        select(func.count())
        .select_from(BusinessBrainIndexRecord)
        .where(
            BusinessBrainIndexRecord.workspace_id == workspace.id,
            BusinessBrainIndexRecord.fact_id == fact_id,
        )
    )


async def _state(db_session, fact_id: str) -> str:
    return await db_session.scalar(
        select(BusinessBrainFactRecord.index_state).where(BusinessBrainFactRecord.fact_id == fact_id)
    )


async def test_reconcile_embeds_pending_visible_fact(monkeypatch, db_session, workspace: Workspace):
    monkeypatch.setattr("app.brain.embedding_service.EmbeddingService", _FakeEmbeddingService)
    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(db_session))
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="catalog_product:p1",
            fact_type="catalog_product",
            entity_ref="catalog:product:p1",
            value={"title": "SAT Prep", "description": "Practice tests."},
            source_refs=["src:p1"],
            source="onboarding",
            status="active",
            approval_state="confirmed",
            confidence=0.95,
            risk_tier="low",
            correlation_id="corr:p1",
            idempotency_key="idem:p1",
        )
    )
    await db_session.flush()
    assert await _state(db_session, "catalog_product:p1") == "pending"
    assert await _index_count(db_session, workspace, "catalog_product:p1") == 0

    reconciler = BrainIndexReconciler(db_factory=None, batch_size=_TEST_RECONCILER_BATCH_SIZE)
    processed = await reconciler._reconcile_once(db_session)

    assert processed >= 1
    assert await _state(db_session, "catalog_product:p1") == "indexed"
    assert await _index_count(db_session, workspace, "catalog_product:p1") >= 1


async def test_reconcile_prunes_superseded_pending_fact(monkeypatch, db_session, workspace: Workspace):
    monkeypatch.setattr("app.brain.embedding_service.EmbeddingService", _FakeEmbeddingService)
    repo = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repo)
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id, fact_id="catalog_product:p2", fact_type="catalog_product",
            entity_ref="catalog:product:p2", value={"title": "Old"}, source_refs=["src:p2"],
            source="onboarding", status="active", approval_state="confirmed", confidence=0.9,
            risk_tier="low", correlation_id="corr:p2", idempotency_key="idem:p2",
        )
    )
    await db_session.flush()
    reconciler = BrainIndexReconciler(db_factory=None, batch_size=_TEST_RECONCILER_BATCH_SIZE)
    await reconciler._reconcile_once(db_session)  # embeds p2
    assert await _index_count(db_session, workspace, "catalog_product:p2") >= 1

    await repo.mark_fact_status(
        workspace_id=workspace.id, fact_id="catalog_product:p2", status="superseded", valid_until=utc_now()
    )
    await db_session.flush()
    await reconciler._reconcile_once(db_session)  # prunes p2

    assert await _index_count(db_session, workspace, "catalog_product:p2") == 0
    assert await _state(db_session, "catalog_product:p2") == "indexed"


async def test_reconcile_marks_failed_when_embed_raises(monkeypatch, db_session, workspace: Workspace):
    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(db_session))
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id, fact_id="catalog_product:fail", fact_type="catalog_product",
            entity_ref="catalog:product:fail", value={"title": "Boom"}, source_refs=["src:fail"],
            source="onboarding", status="active", approval_state="confirmed", confidence=0.9,
            risk_tier="low", correlation_id="corr:fail", idempotency_key="idem:fail",
        )
    )
    await db_session.flush()

    async def _boom(self, *, workspace_id, fact_ids=None):
        raise RuntimeError("embedding backend down")

    monkeypatch.setattr(BusinessBrainMemoryService, "index_structured_facts_for_search", _boom)

    await BrainIndexReconciler(
        db_factory=None,
        batch_size=_TEST_RECONCILER_BATCH_SIZE,
    )._reconcile_once(db_session)

    assert await _state(db_session, "catalog_product:fail") == "failed"
    assert await _index_count(db_session, workspace, "catalog_product:fail") == 0


async def test_written_catalog_becomes_semantically_retrievable(monkeypatch, db_session, workspace: Workspace):
    """End-to-end: a catalog fact written through the normal write path — with NO
    manual index call anywhere — becomes indexed purely by the reconciler. This is
    the loop the manual ws1 backfill proved by hand during the Bug-2 fix."""
    monkeypatch.setattr("app.brain.embedding_service.EmbeddingService", _FakeEmbeddingService)
    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(db_session))
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id, fact_id="catalog_product:retr", fact_type="catalog_product",
            entity_ref="catalog:product:retr", value={"title": "SAT Platform", "description": "Tests."},
            source_refs=["src:retr"], source="onboarding", status="active", approval_state="confirmed",
            confidence=0.95, risk_tier="low", correlation_id="corr:retr", idempotency_key="idem:retr",
        )
    )
    await db_session.flush()
    # No manual index_structured_facts_for_search call — only the reconciler.
    await BrainIndexReconciler(
        db_factory=None,
        batch_size=_TEST_RECONCILER_BATCH_SIZE,
    )._reconcile_once(db_session)
    assert await _index_count(db_session, workspace, "catalog_product:retr") >= 1


class _DegradedEmbeddingService:
    """Provider outage: both paths raise, so RetrievalIndexEmbeddingService returns
    'degraded' results (it swallows embed errors) rather than raising."""
    async def embed_texts_batch(self, texts):
        raise RuntimeError("embedding provider down")

    async def embed_text(self, text):
        raise RuntimeError("embedding provider down")


async def test_reconcile_marks_failed_when_embedding_degrades(monkeypatch, db_session, workspace: Workspace):
    monkeypatch.setattr("app.brain.embedding_service.EmbeddingService", _DegradedEmbeddingService)
    memory = BusinessBrainMemoryService(repository=CommercialSpineRepository(db_session))
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id, fact_id="catalog_product:deg", fact_type="catalog_product",
            entity_ref="catalog:product:deg", value={"title": "Deg"}, source_refs=["src:deg"],
            source="onboarding", status="active", approval_state="confirmed", confidence=0.9,
            risk_tier="low", correlation_id="corr:deg", idempotency_key="idem:deg",
        )
    )
    await db_session.flush()
    # index_structured_facts_for_search returns normally (degraded units), does NOT raise.
    await BrainIndexReconciler(
        db_factory=None,
        batch_size=_TEST_RECONCILER_BATCH_SIZE,
    )._reconcile_once(db_session)
    # The fact embedded to a degraded (unsearchable) record, so it is failed, not indexed.
    assert await _state(db_session, "catalog_product:deg") == "failed"
