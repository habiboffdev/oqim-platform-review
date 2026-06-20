from __future__ import annotations

from datetime import datetime, timezone

from app.modules.business_brain.contracts import (
    BusinessBrainFactUpdateInput,
    BusinessBrainWriteResult,
)
from app.modules.commercial_spine.contracts import BusinessBrainFact, BusinessBrainProjection
from app.modules.commercial_spine.repository import CommercialSpineRepository


class BusinessBrainWriteService:
    def __init__(self, *, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def apply(
        self,
        request: BusinessBrainFactUpdateInput,
    ) -> BusinessBrainWriteResult:
        if request.supersedes_fact_id:
            await self._repository.mark_fact_status(
                workspace_id=request.workspace_id,
                fact_id=request.supersedes_fact_id,
                status="superseded",
                valid_until=request.valid_from or datetime.now(timezone.utc),
            )
        fact = request.to_fact()
        update = request.to_update()
        update_created = await self._repository.persist_update(update)
        fact_created = await self._repository.persist_fact(fact)
        return BusinessBrainWriteResult(
            fact=fact,
            update=update,
            fact_created=fact_created,
            update_created=update_created,
        )

    async def rebuild_projection(
        self,
        *,
        workspace_id: int,
        projection_ref: str,
        projection_type: str,
        entity_ref: str,
    ) -> BusinessBrainProjection:
        return await self._repository.rebuild_projection_from_facts(
            workspace_id=workspace_id,
            projection_ref=projection_ref,
            projection_type=projection_type,
            entity_ref=entity_ref,
        )

    async def fact_at(
        self,
        *,
        workspace_id: int,
        entity_ref: str,
        fact_type: str,
        at: datetime,
    ) -> BusinessBrainFact | None:
        facts = await self._repository.list_facts(
            workspace_id=workspace_id,
            entity_ref=entity_ref,
            fact_type=fact_type,
            statuses=("active", "confirmed", "historical", "superseded"),
            limit=250,
        )
        for fact in sorted(facts, key=lambda item: item.valid_from, reverse=True):
            starts_before = fact.valid_from <= at
            ends_after = fact.valid_until is None or fact.valid_until > at
            if starts_before and ends_after:
                return fact
        return None
