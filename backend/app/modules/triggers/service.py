"""Workspace-scoped CRUD for Trigger.

The service rejects:
 - agent IDs that don't belong to the workspace
 - event_source values outside the EVENT_SOURCES set (enforced by contract)
 - duplicate (workspace, event_source, action_proposal_type, matching_scope)
   tuples — the second call reactivates / updates the existing row instead of
   creating a duplicate.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.agent import Agent
from app.models.trigger import Trigger
from app.modules.triggers.contracts import TriggerInput, TriggerRead


class TriggerNotFoundError(Exception):
    pass


class TriggerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_workspace(
        self,
        *,
        workspace_id: int,
        agent_id: int | None = None,
        active_only: bool = False,
    ) -> list[TriggerRead]:
        stmt = select(Trigger).where(Trigger.workspace_id == workspace_id)
        if agent_id is not None:
            stmt = stmt.where(Trigger.owner_agent_id == agent_id)
        if active_only:
            stmt = stmt.where(Trigger.active.is_(True))
        stmt = stmt.order_by(Trigger.id.asc())
        result = await self._session.scalars(stmt)
        return [TriggerRead.model_validate(row) for row in result.all()]

    async def create(
        self, *, workspace_id: int, payload: TriggerInput
    ) -> TriggerRead:
        agent = await self._session.get(Agent, payload.owner_agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("owner_agent_id does not belong to this workspace")

        idempotency_key = payload.compute_idempotency_key()
        existing = await self._session.scalar(
            select(Trigger).where(
                Trigger.workspace_id == workspace_id,
                Trigger.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            existing.permission_mode = payload.permission_mode
            existing.retry_policy = payload.retry_policy
            existing.notes = payload.notes
            existing.matching_scope = payload.matching_scope
            existing.active = True
            await self._session.flush()
            return TriggerRead.model_validate(existing)

        trigger = Trigger(
            workspace_id=workspace_id,
            owner_agent_id=payload.owner_agent_id,
            event_source=payload.event_source,
            matching_scope=payload.matching_scope,
            permission_mode=payload.permission_mode,
            action_proposal_type=payload.action_proposal_type,
            idempotency_key=idempotency_key,
            retry_policy=payload.retry_policy,
            notes=payload.notes,
        )
        self._session.add(trigger)
        await self._session.flush()
        return TriggerRead.model_validate(trigger)

    async def deactivate(
        self, *, workspace_id: int, trigger_id: int, reason: str = "owner"
    ) -> TriggerRead:
        trigger = await self._fetch(workspace_id=workspace_id, trigger_id=trigger_id)
        trigger.active = False
        trigger.audit_metadata = {**trigger.audit_metadata, "deactivated_by": reason}
        await self._session.flush()
        return TriggerRead.model_validate(trigger)

    async def record_run(
        self,
        *,
        workspace_id: int,
        trigger_id: int,
        status: str,
    ) -> TriggerRead:
        trigger = await self._fetch(workspace_id=workspace_id, trigger_id=trigger_id)
        trigger.last_run_status = status
        trigger.last_run_at = utc_now()
        trigger.run_count = trigger.run_count + 1
        await self._session.flush()
        return TriggerRead.model_validate(trigger)

    async def _fetch(
        self, *, workspace_id: int, trigger_id: int
    ) -> Trigger:
        trigger = await self._session.scalar(
            select(Trigger).where(
                Trigger.workspace_id == workspace_id, Trigger.id == trigger_id
            )
        )
        if trigger is None:
            raise TriggerNotFoundError(f"trigger {trigger_id} not in workspace {workspace_id}")
        return trigger
