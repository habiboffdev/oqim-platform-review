from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.agent_session import AgentSession, AgentSessionEvent


class AgentSessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_or_create(
        self,
        *,
        workspace_id: int,
        conversation_id: int | None,
        customer_id: int | None,
        agent_id: int,
        channel: str,
        owner_chat_id: int | None = None,
    ) -> AgentSession:
        # Owner/setup turns have no Conversation: key the session by owner_chat_id
        # (conversation_id IS NULL). Customer turns keep keying by conversation_id.
        if conversation_id is None:
            if owner_chat_id is None:
                raise ValueError("owner_chat_id required when conversation_id is None")
            stmt = select(AgentSession).where(
                AgentSession.workspace_id == workspace_id,
                AgentSession.conversation_id.is_(None),
                AgentSession.owner_chat_id == owner_chat_id,
                AgentSession.agent_id == agent_id,
            )
            session_key = (
                f"workspace:{workspace_id}:owner:{owner_chat_id}:agent:{agent_id}"
            )
        else:
            stmt = select(AgentSession).where(
                AgentSession.workspace_id == workspace_id,
                AgentSession.conversation_id == conversation_id,
                AgentSession.agent_id == agent_id,
            )
            session_key = (
                f"workspace:{workspace_id}:conversation:{conversation_id}:agent:{agent_id}"
            )
        existing = (await self._db.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing

        session = AgentSession(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            owner_chat_id=owner_chat_id,
            customer_id=customer_id,
            agent_id=agent_id,
            channel=channel,
            session_key=session_key,
            hermes_session_id="pending",
            state="active",
            summary="",
            event_count=0,
        )
        self._db.add(session)
        await self._db.flush()
        session.hermes_session_id = f"oqim:agent-session:{session.id}"
        await self._db.flush()
        return session

    async def append_event(
        self,
        *,
        agent_session_id: int,
        workspace_id: int,
        conversation_id: int,
        agent_id: int,
        event_type: str,
        direction: str,
        idempotency_key: str,
        message_id: int | None = None,
        hermes_run_id: str | None = None,
        text: str = "",
        payload: dict[str, Any] | None = None,
    ) -> AgentSessionEvent:
        existing = (
            await self._db.execute(
                select(AgentSessionEvent).where(
                    AgentSessionEvent.workspace_id == workspace_id,
                    AgentSessionEvent.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        session = await self._db.scalar(
            select(AgentSession)
            .where(AgentSession.id == agent_session_id)
            .with_for_update()
        )
        if session is None:
            raise ValueError(f"agent_session_not_found:{agent_session_id}")

        existing = (
            await self._db.execute(
                select(AgentSessionEvent).where(
                    AgentSessionEvent.workspace_id == workspace_id,
                    AgentSessionEvent.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        sequence = int(
            await self._db.scalar(
                select(func.coalesce(func.max(AgentSessionEvent.sequence), 0)).where(
                    AgentSessionEvent.agent_session_id == agent_session_id
                )
            )
            or 0
        ) + 1
        event = AgentSessionEvent(
            agent_session_id=agent_session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            sequence=sequence,
            event_type=event_type,
            direction=direction,
            message_id=message_id,
            hermes_run_id=hermes_run_id,
            text=(text or "").strip(),
            payload=payload or {},
            idempotency_key=idempotency_key,
            created_at=utc_now(),
        )
        self._db.add(event)
        await self._db.flush()

        session.event_count = sequence
        if direction == "inbound":
            session.last_customer_event_id = event.id
        if direction == "outbound":
            session.last_agent_event_id = event.id
        session.updated_at = utc_now()
        await self._db.flush()
        return event

    async def load_recent_events(
        self,
        *,
        agent_session_id: int,
        limit: int = 20,
    ) -> list[AgentSessionEvent]:
        stmt = (
            select(AgentSessionEvent)
            .where(AgentSessionEvent.agent_session_id == agent_session_id)
            .order_by(AgentSessionEvent.sequence.desc())
            .limit(max(1, int(limit)))
        )
        rows = list((await self._db.execute(stmt)).scalars().all())
        return list(reversed(rows))
