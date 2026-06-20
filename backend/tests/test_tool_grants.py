"""Tests for ToolGrant: workspace-scoped capability grants used by Trigger
Runtime and Action Runtime to gate scoped MCP/integration calls.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.tool_grant import ToolGrant
from app.models.workspace import Workspace
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantNotFoundError, ToolGrantService


pytestmark = pytest.mark.asyncio


class TestToolGrantService:
    async def test_grant_creates_active_row(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        agent: Agent,
    ) -> None:
        service = ToolGrantService(db_session)
        grant = await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id,
                scope="telegram.send_message",
                grant_reason="seller agent needs to reply",
            ),
        )
        assert grant.active is True
        assert grant.scope == "telegram.send_message"
        assert grant.use_count == 0
        assert grant.last_used_at is None

    async def test_grant_is_idempotent_for_same_scope(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        agent: Agent,
    ) -> None:
        service = ToolGrantService(db_session)
        first = await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id, scope="telegram.read_messages"
            ),
        )
        second = await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id,
                scope="telegram.read_messages",
                grant_reason="rationale updated",
            ),
        )
        assert first.id == second.id
        assert second.grant_reason == "rationale updated"

        rows = await db_session.scalars(
            select(ToolGrant).where(
                ToolGrant.workspace_id == workspace.id,
                ToolGrant.scope == "telegram.read_messages",
            )
        )
        assert len(rows.all()) == 1

    async def test_grant_rejects_agent_from_other_workspace(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        foreign_agent = Agent(workspace_id=workspace_b.id, name="Other")
        db_session.add(foreign_agent)
        await db_session.flush()

        service = ToolGrantService(db_session)
        with pytest.raises(ValueError, match="does not belong"):
            await service.grant(
                workspace_id=workspace.id,
                payload=ToolGrantInput(
                    agent_id=foreign_agent.id, scope="telegram.send_message"
                ),
            )

    async def test_check_grant_returns_true_only_for_active(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        agent: Agent,
    ) -> None:
        service = ToolGrantService(db_session)
        await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id, scope="telegram.fetch_media"
            ),
        )
        assert (
            await service.check_grant(
                workspace_id=workspace.id,
                agent_id=agent.id,
                scope="telegram.fetch_media",
            )
            is True
        )
        assert (
            await service.check_grant(
                workspace_id=workspace.id,
                agent_id=agent.id,
                scope="telegram.send_message",
            )
            is False
        )

    async def test_revoke_flips_active_and_check_returns_false(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        agent: Agent,
    ) -> None:
        service = ToolGrantService(db_session)
        await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id, scope="telegram.watch_channel"
            ),
        )
        revoked = await service.revoke(
            workspace_id=workspace.id,
            agent_id=agent.id,
            scope="telegram.watch_channel",
        )
        assert revoked.active is False
        assert revoked.revoked_at is not None
        assert revoked.audit_metadata["revoked_by"] == "owner"
        assert (
            await service.check_grant(
                workspace_id=workspace.id,
                agent_id=agent.id,
                scope="telegram.watch_channel",
            )
            is False
        )

    async def test_grant_after_revoke_reactivates_same_row(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        agent: Agent,
    ) -> None:
        service = ToolGrantService(db_session)
        created = await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id, scope="telegram.sync_history"
            ),
        )
        await service.revoke(
            workspace_id=workspace.id,
            agent_id=agent.id,
            scope="telegram.sync_history",
        )
        reactivated = await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id,
                scope="telegram.sync_history",
                grant_reason="reauthorized after audit",
            ),
        )
        assert reactivated.id == created.id
        assert reactivated.active is True
        assert reactivated.revoked_at is None

    async def test_revoke_unknown_grant_raises(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        agent: Agent,
    ) -> None:
        service = ToolGrantService(db_session)
        with pytest.raises(ToolGrantNotFoundError):
            await service.revoke(
                workspace_id=workspace.id,
                agent_id=agent.id,
                scope="telegram.never_granted",
            )

    async def test_record_use_increments_counter_and_timestamp(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        agent: Agent,
    ) -> None:
        service = ToolGrantService(db_session)
        await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id, scope="telegram.send_message"
            ),
        )
        await service.record_use(
            workspace_id=workspace.id,
            agent_id=agent.id,
            scope="telegram.send_message",
        )
        await service.record_use(
            workspace_id=workspace.id,
            agent_id=agent.id,
            scope="telegram.send_message",
        )
        row = await db_session.scalar(
            select(ToolGrant).where(
                ToolGrant.workspace_id == workspace.id,
                ToolGrant.scope == "telegram.send_message",
            )
        )
        assert row is not None
        assert row.use_count == 2
        assert row.last_used_at is not None

    async def test_record_use_is_noop_for_revoked_grant(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        agent: Agent,
    ) -> None:
        service = ToolGrantService(db_session)
        await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent.id, scope="telegram.fetch_media"
            ),
        )
        await service.revoke(
            workspace_id=workspace.id,
            agent_id=agent.id,
            scope="telegram.fetch_media",
        )
        await service.record_use(
            workspace_id=workspace.id,
            agent_id=agent.id,
            scope="telegram.fetch_media",
        )
        row = await db_session.scalar(
            select(ToolGrant).where(
                ToolGrant.workspace_id == workspace.id,
                ToolGrant.scope == "telegram.fetch_media",
            )
        )
        assert row is not None
        assert row.use_count == 0

    async def test_list_returns_only_target_workspace(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        agent_a = Agent(workspace_id=workspace.id, name="A")
        agent_b = Agent(workspace_id=workspace_b.id, name="B")
        db_session.add_all([agent_a, agent_b])
        await db_session.flush()

        service = ToolGrantService(db_session)
        await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent_a.id, scope="telegram.send_message"
            ),
        )
        await service.grant(
            workspace_id=workspace_b.id,
            payload=ToolGrantInput(
                agent_id=agent_b.id, scope="telegram.send_message"
            ),
        )
        listing = await service.list_for_workspace(workspace_id=workspace.id)
        assert len(listing) == 1
        assert listing[0].agent_id == agent_a.id

    async def test_check_grant_isolated_across_workspaces(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        workspace_b: Workspace,
    ) -> None:
        agent_a = Agent(workspace_id=workspace.id, name="A")
        agent_b = Agent(workspace_id=workspace_b.id, name="B")
        db_session.add_all([agent_a, agent_b])
        await db_session.flush()

        service = ToolGrantService(db_session)
        await service.grant(
            workspace_id=workspace.id,
            payload=ToolGrantInput(
                agent_id=agent_a.id, scope="telegram.send_message"
            ),
        )
        assert (
            await service.check_grant(
                workspace_id=workspace_b.id,
                agent_id=agent_a.id,
                scope="telegram.send_message",
            )
            is False
        )
