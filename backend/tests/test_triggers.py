"""Phase 5 — Trigger model + TriggerService."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.workspace import Workspace
from app.modules.triggers.contracts import TriggerInput
from app.modules.triggers.service import TriggerNotFoundError, TriggerService


pytestmark = pytest.mark.asyncio


class TestTriggerService:
    async def test_create_persists_trigger_with_derived_idempotency(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        service = TriggerService(db_session)
        trigger = await service.create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="channel_message_received",
                action_proposal_type="catalog.update_product",
                matching_scope={"channel": "@mybiz"},
                permission_mode="ask_always",
            ),
        )
        assert trigger.active is True
        assert trigger.run_count == 0
        assert trigger.idempotency_key
        assert len(trigger.idempotency_key) == 32

    async def test_create_is_idempotent_for_same_scope(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        service = TriggerService(db_session)
        first = await service.create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="channel_message_received",
                action_proposal_type="catalog.update_product",
                matching_scope={"channel": "@mybiz"},
                notes="initial",
            ),
        )
        second = await service.create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="channel_message_received",
                action_proposal_type="catalog.update_product",
                matching_scope={"channel": "@mybiz"},
                notes="updated rationale",
            ),
        )
        assert first.id == second.id
        assert second.notes == "updated rationale"
        listed = await service.list_for_workspace(workspace_id=workspace.id)
        assert len(listed) == 1

    async def test_rejects_unknown_event_source(self) -> None:
        with pytest.raises(ValueError, match="event_source"):
            TriggerInput(
                owner_agent_id=1,
                event_source="bogus_source",
                action_proposal_type="catalog.update_product",
            )

    async def test_rejects_agent_from_other_workspace(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        foreign_agent = Agent(workspace_id=workspace_b.id, name="Other")
        db_session.add(foreign_agent)
        await db_session.flush()
        service = TriggerService(db_session)
        with pytest.raises(ValueError, match="does not belong"):
            await service.create(
                workspace_id=workspace.id,
                payload=TriggerInput(
                    owner_agent_id=foreign_agent.id,
                    event_source="channel_message_received",
                    action_proposal_type="catalog.update_product",
                ),
            )

    async def test_deactivate_flips_active(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        service = TriggerService(db_session)
        created = await service.create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="schedule",
                action_proposal_type="task.daily_review",
            ),
        )
        result = await service.deactivate(
            workspace_id=workspace.id, trigger_id=created.id
        )
        assert result.active is False
        assert result.audit_metadata["deactivated_by"] == "owner"

    async def test_deactivate_unknown_raises(
        self, db_session: AsyncSession, workspace: Workspace
    ) -> None:
        service = TriggerService(db_session)
        with pytest.raises(TriggerNotFoundError):
            await service.deactivate(workspace_id=workspace.id, trigger_id=999_999)

    async def test_record_run_increments_counters(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        service = TriggerService(db_session)
        created = await service.create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="schedule",
                action_proposal_type="task.daily_review",
            ),
        )
        await service.record_run(
            workspace_id=workspace.id, trigger_id=created.id, status="succeeded"
        )
        await service.record_run(
            workspace_id=workspace.id, trigger_id=created.id, status="succeeded"
        )
        listed = await service.list_for_workspace(workspace_id=workspace.id)
        assert listed[0].run_count == 2
        assert listed[0].last_run_status == "succeeded"

    async def test_list_filters_by_agent_and_workspace(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        agent_a = Agent(workspace_id=workspace.id, name="A")
        agent_b = Agent(workspace_id=workspace.id, name="B")
        foreign = Agent(workspace_id=workspace_b.id, name="Foreign")
        db_session.add_all([agent_a, agent_b, foreign])
        await db_session.flush()

        service = TriggerService(db_session)
        await service.create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent_a.id,
                event_source="schedule",
                action_proposal_type="task.daily_review",
            ),
        )
        await service.create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent_b.id,
                event_source="schedule",
                action_proposal_type="task.weekly_review",
            ),
        )
        await service.create(
            workspace_id=workspace_b.id,
            payload=TriggerInput(
                owner_agent_id=foreign.id,
                event_source="schedule",
                action_proposal_type="task.daily_review",
            ),
        )

        filtered = await service.list_for_workspace(
            workspace_id=workspace.id, agent_id=agent_a.id
        )
        assert len(filtered) == 1
        assert filtered[0].owner_agent_id == agent_a.id

        all_for_workspace_a = await service.list_for_workspace(workspace_id=workspace.id)
        assert len(all_for_workspace_a) == 2
