"""index_state drives the automatic brain indexer (see
docs/superpowers/specs/2026-05-25-automatic-fact-indexing-design.md)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.base import utc_now
from app.models.commercial_spine import BusinessBrainFactRecord
from app.models.workspace import Workspace
from app.modules.business_brain.memory import SEARCHABLE_STRUCTURED_FACT_TYPES
from app.modules.commercial_spine.contracts import BusinessBrainFact
from app.modules.commercial_spine.repository import CommercialSpineRepository

pytestmark = pytest.mark.asyncio


async def test_fact_record_defaults_to_skipped(db_session, workspace: Workspace):
    rec = BusinessBrainFactRecord(
        fact_id="catalog_product:t1",
        workspace_id=workspace.id,
        fact_type="catalog_product",
        entity_ref="catalog:product:t1",
        value={},
        confidence=0.9,
        status="active",
        risk_tier="low",
        valid_from=utc_now(),
        source_refs=[],
        idempotency_key="idem:t1",
        raw_fact={},
    )
    db_session.add(rec)
    await db_session.flush()
    assert rec.index_state == "skipped"  # default applies on INSERT (classic mapping)
    assert rec.indexed_at is None


def _fact(workspace_id: int, *, fact_id: str, fact_type: str) -> BusinessBrainFact:
    return BusinessBrainFact(
        fact_id=fact_id,
        workspace_id=workspace_id,
        fact_type=fact_type,
        entity_ref=f"e:{fact_id}",
        value={"title": "x"},
        confidence=0.9,
        status="active",
        risk_tier="low",
        valid_from=utc_now(),
        source_refs=[f"src:{fact_id}"],
        idempotency_key=f"idem:{fact_id}",
    )


async def _state(db_session, workspace, fact_id: str) -> str:
    return await db_session.scalar(
        select(BusinessBrainFactRecord.index_state).where(
            BusinessBrainFactRecord.workspace_id == workspace.id,
            BusinessBrainFactRecord.fact_id == fact_id,
        )
    )


async def test_persist_searchable_fact_is_pending(db_session, workspace: Workspace):
    repo = CommercialSpineRepository(db_session)
    assert "catalog_product" in SEARCHABLE_STRUCTURED_FACT_TYPES
    await repo.persist_fact(_fact(workspace.id, fact_id="catalog_product:a", fact_type="catalog_product"))
    await db_session.flush()
    assert await _state(db_session, workspace, "catalog_product:a") == "pending"


async def test_persist_non_searchable_fact_is_skipped(db_session, workspace: Workspace):
    repo = CommercialSpineRepository(db_session)
    await repo.persist_fact(_fact(workspace.id, fact_id="autocrm_customer_state:a", fact_type="autocrm_customer_state"))
    await db_session.flush()
    assert await _state(db_session, workspace, "autocrm_customer_state:a") == "skipped"


async def test_supersede_flips_back_to_pending(db_session, workspace: Workspace):
    repo = CommercialSpineRepository(db_session)
    await repo.persist_fact(_fact(workspace.id, fact_id="catalog_product:b", fact_type="catalog_product"))
    await db_session.flush()
    # Pretend the reconciler already indexed it.
    rec = await db_session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace.id,
            BusinessBrainFactRecord.fact_id == "catalog_product:b",
        )
    )
    rec.index_state = "indexed"
    await db_session.flush()
    await repo.mark_fact_status(workspace_id=workspace.id, fact_id="catalog_product:b", status="superseded", valid_until=utc_now())
    await db_session.flush()
    assert await _state(db_session, workspace, "catalog_product:b") == "pending"


async def test_update_fact_state_requeues_searchable_fact(db_session, workspace: Workspace):
    repo = CommercialSpineRepository(db_session)
    await repo.persist_fact(_fact(workspace.id, fact_id="catalog_product:u", fact_type="catalog_product"))
    await db_session.flush()
    rec = await db_session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace.id,
            BusinessBrainFactRecord.fact_id == "catalog_product:u",
        )
    )
    rec.index_state = "indexed"
    await db_session.flush()
    await repo.update_fact_state(workspace_id=workspace.id, fact_id="catalog_product:u", value={"title": "new"})
    await db_session.flush()
    assert await _state(db_session, workspace, "catalog_product:u") == "pending"


async def test_update_fact_state_leaves_non_searchable_skipped(db_session, workspace: Workspace):
    repo = CommercialSpineRepository(db_session)
    await repo.persist_fact(_fact(workspace.id, fact_id="autocrm_customer_state:u", fact_type="autocrm_customer_state"))
    await db_session.flush()
    await repo.update_fact_state(workspace_id=workspace.id, fact_id="autocrm_customer_state:u", confidence=0.5)
    await db_session.flush()
    assert await _state(db_session, workspace, "autocrm_customer_state:u") == "skipped"


async def test_supersede_non_searchable_stays_skipped(db_session, workspace: Workspace):
    repo = CommercialSpineRepository(db_session)
    await repo.persist_fact(_fact(workspace.id, fact_id="autocrm_customer_state:s", fact_type="autocrm_customer_state"))
    await db_session.flush()
    assert await _state(db_session, workspace, "autocrm_customer_state:s") == "skipped"
    await repo.mark_fact_status(
        workspace_id=workspace.id, fact_id="autocrm_customer_state:s", status="superseded", valid_until=utc_now()
    )
    await db_session.flush()
    assert await _state(db_session, workspace, "autocrm_customer_state:s") == "skipped"
