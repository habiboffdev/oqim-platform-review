from __future__ import annotations

from datetime import datetime, timezone

from app.modules.business_brain.contracts import (
    BusinessBrainFactDetail,
    BusinessBrainFactReadModel,
    BusinessBrainIndexRecordContract,
)
from app.modules.commercial_spine.contracts import BusinessBrainFact
from app.modules.commercial_spine.repository import CommercialSpineRepository


class BusinessBrainReadService:
    def __init__(self, *, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def list_facts(
        self,
        *,
        workspace_id: int,
    ) -> tuple[BusinessBrainFactReadModel, ...]:
        facts = await self._repository.list_facts(workspace_id=workspace_id)
        return tuple(_fact_read_model(fact) for fact in facts)

    async def detail(
        self,
        *,
        workspace_id: int,
        fact_id: str,
    ) -> BusinessBrainFactDetail | None:
        fact = await self._repository.get_fact(
            workspace_id=workspace_id,
            fact_id=fact_id,
        )
        if fact is None:
            return None
        index_records = await self._repository.list_index_records(
            workspace_id=workspace_id,
            fact_id=fact_id,
        )
        updates = await self._repository.list_updates_for_fact(
            workspace_id=workspace_id,
            fact_id=fact_id,
        )
        return BusinessBrainFactDetail(
            fact=_fact_read_model(fact),
            updates=list(updates),
            index_state=_index_state(index_records),
            extraction_state="unavailable",
            index_records=list(index_records),
        )


def _fact_read_model(fact: BusinessBrainFact) -> BusinessBrainFactReadModel:
    return BusinessBrainFactReadModel(
        fact_id=fact.fact_id,
        workspace_id=fact.workspace_id,
        fact_type=fact.fact_type,
        entity_ref=fact.entity_ref,
        value=dict(fact.value),
        confidence=fact.confidence,
        status=fact.status,
        risk_tier=fact.risk_tier,
        source_refs=list(fact.source_refs),
        freshness=_freshness(fact.valid_from),
        supersedes_fact_id=fact.supersedes_fact_id,
        valid_from=fact.valid_from,
        valid_until=fact.valid_until,
    )


def _index_state(
    records: tuple[BusinessBrainIndexRecordContract, ...],
) -> str:
    if not records:
        return "unavailable"
    states = {record.state for record in records}
    if "degraded" in states:
        return "degraded"
    if states == {"ready"}:
        return "ready"
    return "pending"


def _freshness(valid_from: datetime) -> dict[str, object]:
    age_seconds = max(0, int((datetime.now(timezone.utc) - valid_from).total_seconds()))
    return {
        "state": "fresh" if age_seconds < 30 * 24 * 60 * 60 else "stale",
        "age_seconds": age_seconds,
    }
