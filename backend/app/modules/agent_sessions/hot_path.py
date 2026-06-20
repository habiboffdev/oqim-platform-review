from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.modules.agent_sessions.service import AgentSessionService


@dataclass(frozen=True)
class AgentSessionHotPathResult:
    agent_id: int
    agent_session_id: int
    hermes_session_id: str
    event_id: int
    latest_sequence: int
    source_refs: list[str]


class AgentSessionHotPathService:
    """Owns the minimal agent-visible intake record for live channel messages."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._sessions = AgentSessionService(db)

    async def record_customer_message_and_prepare_run(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        customer_id: int | None,
        channel: str,
        message_id: int,
        text: str,
        trigger_telemetry: dict[str, float] | None = None,
        agent_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentSessionHotPathResult | None:
        resolved_agent_id = agent_id or await self._resolve_default_agent_id(workspace_id)
        if resolved_agent_id is None:
            return None

        agent_session = await self._sessions.get_or_create(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            agent_id=resolved_agent_id,
            channel=channel,
        )
        event_payload = {
            "message_id": message_id,
            "channel": channel,
            **(payload or {}),
        }
        telemetry = _clean_trigger_telemetry(trigger_telemetry)
        if telemetry:
            event_payload["trigger_telemetry"] = telemetry
        event = await self._sessions.append_event(
            agent_session_id=agent_session.id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=resolved_agent_id,
            event_type="customer_message",
            direction="inbound",
            message_id=message_id,
            text=text,
            payload=event_payload,
            idempotency_key=f"message:{message_id}:customer_message:agent:{resolved_agent_id}",
        )
        return AgentSessionHotPathResult(
            agent_id=resolved_agent_id,
            agent_session_id=agent_session.id,
            hermes_session_id=agent_session.hermes_session_id,
            event_id=event.id,
            latest_sequence=event.sequence,
            source_refs=[
                f"agent_session:{agent_session.id}",
                f"agent_session_event:{event.id}",
                f"message:{message_id}",
                f"conversation:{conversation_id}",
            ],
        )

    async def _resolve_default_agent_id(self, workspace_id: int) -> int | None:
        return await self._db.scalar(
            select(Agent.id)
            .where(
                Agent.workspace_id == workspace_id,
                Agent.is_active.is_(True),
            )
            .order_by(Agent.is_default.desc(), Agent.id.asc())
            .limit(1)
        )


def _clean_trigger_telemetry(value: dict[str, float] | None) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        try:
            cleaned[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return cleaned
