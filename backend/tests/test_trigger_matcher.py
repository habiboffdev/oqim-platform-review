"""Phase 9 brutal — TriggerMatcher executor.

Tests prove that the fan-out function:
 - matches active workspace-scoped triggers on event_source + scope predicate
 - writes deterministic-id CommercialActionProposalRecord rows
 - is idempotent on event replay
 - blocks proposal creation when a required_tool_scope is missing or revoked
 - never fans out across workspace boundaries
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.trigger import Trigger
from app.models.workspace import Workspace
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.modules.triggers.contracts import TriggerInput
from app.modules.triggers.matcher import TriggerEvent, TriggerMatcher
from app.modules.triggers.service import TriggerService


pytestmark = pytest.mark.asyncio


class TestTriggerMatcher:
    async def test_matches_active_trigger_and_writes_proposal(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        agent: Agent,
    ) -> None:
        await TriggerService(db_session).create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="channel_message_received",
                action_proposal_type="catalog.update_product",
                matching_scope={"channel": "@mybiz"},
            ),
        )

        matcher = TriggerMatcher(db_session)
        matched = await matcher.fan_out(
            TriggerEvent(
                workspace_id=workspace.id,
                event_source="channel_message_received",
                payload={"channel": "@mybiz", "post_id": 42},
                correlation_id="evt-1",
            )
        )
        assert len(matched) == 1

        proposals = (
            await db_session.scalars(
                select(CommercialActionProposalRecord).where(
                    CommercialActionProposalRecord.workspace_id == workspace.id
                )
            )
        ).all()
        assert len(proposals) == 1
        assert proposals[0].action_type == "catalog.update_product"
        assert proposals[0].correlation_id == "evt-1"
        assert proposals[0].lifecycle_state == "waiting_approval"
        assert proposals[0].executor_runtime == "trigger_runtime"

    async def test_scope_mismatch_skips_trigger(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        await TriggerService(db_session).create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="channel_message_received",
                action_proposal_type="catalog.update_product",
                matching_scope={"channel": "@mybiz"},
            ),
        )

        matched = await TriggerMatcher(db_session).fan_out(
            TriggerEvent(
                workspace_id=workspace.id,
                event_source="channel_message_received",
                payload={"channel": "@other"},
            )
        )
        assert matched == []

    async def test_inactive_trigger_is_skipped(
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
        await service.deactivate(workspace_id=workspace.id, trigger_id=created.id)

        matched = await TriggerMatcher(db_session).fan_out(
            TriggerEvent(workspace_id=workspace.id, event_source="schedule")
        )
        assert matched == []

    async def test_replay_is_idempotent(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        await TriggerService(db_session).create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="schedule",
                action_proposal_type="task.daily_review",
            ),
        )
        matcher = TriggerMatcher(db_session)
        event = TriggerEvent(
            workspace_id=workspace.id,
            event_source="schedule",
            payload={"day": "monday"},
        )
        first = await matcher.fan_out(event)
        second = await matcher.fan_out(event)
        assert first[0].proposal_id == second[0].proposal_id
        rows = (
            await db_session.scalars(
                select(CommercialActionProposalRecord).where(
                    CommercialActionProposalRecord.workspace_id == workspace.id
                )
            )
        ).all()
        assert len(rows) == 1

    async def test_blocks_when_required_scope_grant_missing(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        await TriggerService(db_session).create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="channel_message_received",
                action_proposal_type="catalog.update_product",
                matching_scope={
                    "channel": "@mybiz",
                    "required_tool_scope": "telegram.read_messages",
                },
            ),
        )
        matched = await TriggerMatcher(db_session).fan_out(
            TriggerEvent(
                workspace_id=workspace.id,
                event_source="channel_message_received",
                payload={"channel": "@mybiz"},
            )
        )
        assert matched == []
        rows = (
            await db_session.scalars(
                select(CommercialActionProposalRecord).where(
                    CommercialActionProposalRecord.workspace_id == workspace.id
                )
            )
        ).all()
        assert rows == []

    async def test_fires_when_required_scope_grant_active(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        await ToolGrantService(db_session).grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id, scope="telegram.read_messages"
            ),
        )
        await TriggerService(db_session).create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="channel_message_received",
                action_proposal_type="catalog.update_product",
                matching_scope={
                    "channel": "@mybiz",
                    "required_tool_scope": "telegram.read_messages",
                },
            ),
        )
        matched = await TriggerMatcher(db_session).fan_out(
            TriggerEvent(
                workspace_id=workspace.id,
                event_source="channel_message_received",
                payload={"channel": "@mybiz"},
            )
        )
        assert len(matched) == 1

    async def test_internal_required_capability_uses_agent_config_not_tool_grant(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        agent.tools_config = {"tool_scopes": ["source.ingest"]}
        await TriggerService(db_session).create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="source_changed",
                action_proposal_type="catalog.propose_update",
                matching_scope={"required_tool_scope": "source.ingest"},
            ),
        )

        matched = await TriggerMatcher(db_session).fan_out(
            TriggerEvent(
                workspace_id=workspace.id,
                event_source="source_changed",
                payload={"source_ref": "telegram:@catalog"},
            )
        )

        assert len(matched) == 1

    async def test_internal_required_capability_blocks_when_agent_lacks_it(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        agent.tools_config = {"tool_scopes": ["brain.search"]}
        created = await TriggerService(db_session).create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="source_changed",
                action_proposal_type="catalog.propose_update",
                matching_scope={"required_tool_scope": "source.ingest"},
            ),
        )

        matched = await TriggerMatcher(db_session).fan_out(
            TriggerEvent(
                workspace_id=workspace.id,
                event_source="source_changed",
                payload={"source_ref": "telegram:@catalog"},
            )
        )

        assert matched == []
        trigger = await db_session.scalar(select(Trigger).where(Trigger.id == created.id))
        assert trigger is not None
        assert trigger.last_run_status == "blocked_missing_capability"

    async def test_workspace_isolation(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        agent_a = Agent(workspace_id=workspace.id, name="A")
        agent_b = Agent(workspace_id=workspace_b.id, name="B")
        db_session.add_all([agent_a, agent_b])
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
            workspace_id=workspace_b.id,
            payload=TriggerInput(
                owner_agent_id=agent_b.id,
                event_source="schedule",
                action_proposal_type="task.daily_review",
            ),
        )

        matcher = TriggerMatcher(db_session)
        # Workspace A event fans out only to workspace A proposals.
        await matcher.fan_out(
            TriggerEvent(workspace_id=workspace.id, event_source="schedule")
        )

        proposals_a = (
            await db_session.scalars(
                select(CommercialActionProposalRecord).where(
                    CommercialActionProposalRecord.workspace_id == workspace.id
                )
            )
        ).all()
        proposals_b = (
            await db_session.scalars(
                select(CommercialActionProposalRecord).where(
                    CommercialActionProposalRecord.workspace_id == workspace_b.id
                )
            )
        ).all()
        assert len(proposals_a) == 1
        assert len(proposals_b) == 0

    async def test_run_count_increments_on_fire(
        self, db_session: AsyncSession, workspace: Workspace, agent: Agent
    ) -> None:
        await TriggerService(db_session).create(
            workspace_id=workspace.id,
            payload=TriggerInput(
                owner_agent_id=agent.id,
                event_source="schedule",
                action_proposal_type="task.daily_review",
            ),
        )
        matcher = TriggerMatcher(db_session)
        await matcher.fan_out(
            TriggerEvent(workspace_id=workspace.id, event_source="schedule", payload={"day": "mon"})
        )
        await matcher.fan_out(
            TriggerEvent(workspace_id=workspace.id, event_source="schedule", payload={"day": "tue"})
        )
        triggers = await TriggerService(db_session).list_for_workspace(workspace_id=workspace.id)
        assert triggers[0].run_count == 2
        assert triggers[0].last_run_status == "proposal_created"
