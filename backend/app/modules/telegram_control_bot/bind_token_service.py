"""BindTokenService (#451): mint one-time deep-link bind tokens, redeem them
atomically (single-use, lane-scoped), and record bind-lifecycle audit events.

A token is scoped to one workspace AND must be presented on that workspace's
dedicated bot lane (``bound_workspace_id``) to bind — the global/shared lane
(``bound_workspace_id is None``) can never bind."""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.base import utc_now
from app.models.owner_bind_token import OwnerBindEvent, OwnerBindToken
from app.models.workspace import Workspace


class BindTokenService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _audit(
        self,
        *,
        workspace_id: int,
        event_type: str,
        chat_id: int | None = None,
        lane_workspace_id: int | None = None,
        token_id: int | None = None,
        reason: str | None = None,
    ) -> None:
        self._session.add(
            OwnerBindEvent(
                workspace_id=workspace_id,
                event_type=event_type,
                chat_id=chat_id,
                lane_workspace_id=lane_workspace_id,
                token_id=token_id,
                reason=reason,
            )
        )

    async def mint(self, *, workspace_id: int) -> str:
        """Mint a fresh token, revoking any prior unused token for the workspace
        (exactly one live link at a time)."""
        await self._session.execute(
            update(OwnerBindToken)
            .where(
                OwnerBindToken.workspace_id == workspace_id,
                OwnerBindToken.used_at.is_(None),
            )
            .values(used_at=utc_now())
        )
        token = secrets.token_urlsafe(32)
        ttl = get_settings().owner_bind_token_ttl_seconds
        row = OwnerBindToken(
            workspace_id=workspace_id,
            token=token,
            expires_at=utc_now() + timedelta(seconds=ttl),
        )
        self._session.add(row)
        await self._session.flush()
        await self._audit(workspace_id=workspace_id, event_type="mint", token_id=row.id)
        return token

    async def redeem(self, *, token: str, bound_workspace_id: int | None, chat_id: int) -> bool:
        """Atomically consume a valid, unused, unexpired token scoped to the
        lane's workspace, and bind the chat. Returns False (no bind) otherwise."""
        if bound_workspace_id is None:
            await self._audit(
                workspace_id=0, event_type="failed_bind", chat_id=chat_id,
                reason="no_lane_workspace",
            )
            return False
        stmt = (
            update(OwnerBindToken)
            .where(
                OwnerBindToken.token == token,
                OwnerBindToken.used_at.is_(None),
                OwnerBindToken.expires_at > utc_now(),
                OwnerBindToken.workspace_id == bound_workspace_id,
            )
            .values(used_at=utc_now(), bound_chat_id=chat_id)
            .returning(OwnerBindToken.id)
        )
        token_id = (await self._session.execute(stmt)).scalar_one_or_none()
        if token_id is None:
            await self._audit(
                workspace_id=bound_workspace_id, event_type="failed_bind", chat_id=chat_id,
                lane_workspace_id=bound_workspace_id, reason="invalid_or_used_or_expired",
            )
            return False
        ws = await self._session.get(Workspace, bound_workspace_id)
        prior = ws.owner_control_chat_id if ws else None
        if ws is not None:
            ws.owner_control_chat_id = chat_id
        is_rebind = prior not in (None, chat_id)
        if is_rebind:
            # A queued owner card must not deliver to the NEW chat (spec §6.3).
            await self._dispose_pending_owner_proposals(workspace_id=bound_workspace_id)
        await self._audit(
            workspace_id=bound_workspace_id,
            event_type="rebind" if is_rebind else "bind",
            chat_id=chat_id, lane_workspace_id=bound_workspace_id, token_id=token_id,
        )
        return True

    async def unbind(self, *, workspace_id: int) -> None:
        ws = await self._session.get(Workspace, workspace_id)
        if ws is not None:
            ws.owner_control_chat_id = None
        await self._session.execute(
            update(OwnerBindToken)
            .where(
                OwnerBindToken.workspace_id == workspace_id,
                OwnerBindToken.used_at.is_(None),
            )
            .values(used_at=utc_now())
        )
        await self._dispose_pending_owner_proposals(workspace_id=workspace_id)
        await self._audit(workspace_id=workspace_id, event_type="unbind")

    async def _dispose_pending_owner_proposals(self, *, workspace_id: int) -> None:
        """Expire still-pending owner-config approval cards so they never deliver
        to a re-bound chat or stall against an unbound workspace (spec §6.3)."""
        from app.models.commercial_action import CommercialActionProposalRecord

        await self._session.execute(
            update(CommercialActionProposalRecord)
            .where(
                CommercialActionProposalRecord.workspace_id == workspace_id,
                CommercialActionProposalRecord.action_type == "agent.update_owner_config",
                CommercialActionProposalRecord.lifecycle_state.in_(
                    ("proposed", "waiting_approval")
                ),
            )
            .values(lifecycle_state="expired")
        )
