"""Workspace-scoped CRUD + permission check for ToolGrant.

Every query filters by ``workspace_id``. Cross-workspace ``agent_id`` is
rejected before touching the DB. Revocation is a soft update — never delete
grant rows; permission audits read history.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.agent import Agent
from app.models.tool_grant import ToolGrant
from app.modules.tool_grants.contracts import ToolGrantInput, ToolGrantRead


class ToolGrantNotFoundError(Exception):
    """Raised when a grant lookup misses the workspace + agent + scope tuple."""


class ToolGrantService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_workspace(
        self, *, workspace_id: int, agent_id: int | None = None
    ) -> list[ToolGrantRead]:
        stmt = select(ToolGrant).where(ToolGrant.workspace_id == workspace_id)
        if agent_id is not None:
            stmt = stmt.where(ToolGrant.agent_id == agent_id)
        stmt = stmt.order_by(ToolGrant.scope.asc(), ToolGrant.id.asc())
        result = await self._session.scalars(stmt)
        return [ToolGrantRead.model_validate(row) for row in result.all()]

    async def grant(
        self, *, workspace_id: int, payload: ToolGrantInput
    ) -> ToolGrantRead:
        """Idempotent grant.

        If an active grant for the same (workspace, agent, scope) exists, return
        it. If a revoked grant exists, reactivate it (don't create a duplicate
        row — keeps the audit trail on one row).
        """

        agent = await self._session.get(Agent, payload.agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("agent_id does not belong to this workspace")

        existing = await self._session.scalar(
            select(ToolGrant).where(
                ToolGrant.workspace_id == workspace_id,
                ToolGrant.agent_id == payload.agent_id,
                ToolGrant.scope == payload.scope,
            )
        )
        if existing is not None:
            existing.active = True
            existing.revoked_at = None
            existing.granted_by = payload.granted_by
            existing.grant_reason = payload.grant_reason
            existing.audit_metadata = payload.audit_metadata
            existing.granted_at = utc_now()
            await self._session.flush()
            return ToolGrantRead.model_validate(existing)

        grant = ToolGrant(
            workspace_id=workspace_id,
            agent_id=payload.agent_id,
            scope=payload.scope,
            granted_by=payload.granted_by,
            grant_reason=payload.grant_reason,
            audit_metadata=payload.audit_metadata,
        )
        self._session.add(grant)
        await self._session.flush()
        return ToolGrantRead.model_validate(grant)

    async def revoke(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        scope: str,
        revoked_by: str = "owner",
    ) -> ToolGrantRead:
        grant = await self._fetch_grant(
            workspace_id=workspace_id, agent_id=agent_id, scope=scope
        )
        grant.active = False
        grant.revoked_at = utc_now()
        grant.audit_metadata = {
            **grant.audit_metadata,
            "revoked_by": revoked_by,
        }
        await self._session.flush()
        return ToolGrantRead.model_validate(grant)

    async def check_grant(
        self, *, workspace_id: int, agent_id: int, scope: str
    ) -> bool:
        """Permission check used at execution time."""

        existing = await self._session.scalar(
            select(ToolGrant).where(
                ToolGrant.workspace_id == workspace_id,
                ToolGrant.agent_id == agent_id,
                ToolGrant.scope == scope,
                ToolGrant.active.is_(True),
            )
        )
        return existing is not None

    async def record_use(
        self, *, workspace_id: int, agent_id: int, scope: str
    ) -> None:
        """Increment use counters atomically; no-op if grant is missing/revoked."""

        await self._session.execute(
            update(ToolGrant)
            .where(
                ToolGrant.workspace_id == workspace_id,
                ToolGrant.agent_id == agent_id,
                ToolGrant.scope == scope,
                ToolGrant.active.is_(True),
            )
            .values(last_used_at=utc_now(), use_count=ToolGrant.use_count + 1)
        )

    async def _fetch_grant(
        self, *, workspace_id: int, agent_id: int, scope: str
    ) -> ToolGrant:
        grant = await self._session.scalar(
            select(ToolGrant).where(
                ToolGrant.workspace_id == workspace_id,
                ToolGrant.agent_id == agent_id,
                ToolGrant.scope == scope,
            )
        )
        if grant is None:
            raise ToolGrantNotFoundError(
                f"no grant for agent={agent_id} scope={scope}"
            )
        return grant
