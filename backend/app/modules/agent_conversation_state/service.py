from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.agent_conversation_state import AgentConversationStateSnapshot
from app.models.agent_session import AgentSession
from app.modules.agent_conversation_state.reducer import (
    TurnSignals,
    reduce_facts,
    stage_label,
)
from app.modules.agent_sessions.service import AgentSessionService
from app.modules.hermes_runtime.contracts import HermesRunEventInput, HermesRunEventKind
from app.modules.hermes_runtime.service import HermesRunService


@dataclass(frozen=True)
class AgentConversationStateResult:
    snapshot_id: int
    stage: str
    active_intent: str | None
    summary: str
    state: dict[str, Any]
    source_refs: list[str] = field(default_factory=list)

    def compact_state(self) -> dict[str, Any]:
        payload = {
            "snapshot_id": self.snapshot_id,
            "stage": self.stage,
            "active_intent": self.active_intent,
            "summary": self.summary,
            **self.state,
            "source_refs": self.source_refs,
        }
        return _compact_jsonable(payload)


class AgentConversationStateService:
    """Persist compact Hermes-authored state for one Agent Session.

    This is not catalog authority or a CRM projection. It is the durable,
    idempotent state packet Hermes can update after a turn so the next turn can
    avoid replaying the full transcript.
    """

    snapshot_model = AgentConversationStateSnapshot

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def set_state(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None = None,
        hermes_run_id: str | None = None,
        summary: str = "",
        stage: str = "unknown",
        active_intent: str | None = None,
        selected_items: list[dict[str, Any]] | None = None,
        shown_prices: list[dict[str, Any]] | None = None,
        customer_details: dict[str, Any] | None = None,
        payment: dict[str, Any] | None = None,
        fulfillment: dict[str, Any] | None = None,
        missing_authority: list[str] | None = None,
        next_best_action: str | None = None,
        risk_flags: list[str] | None = None,
        source_refs: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> AgentConversationStateResult:
        await self._validate_scope(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
        )
        state = _compact_jsonable(
            {
                "selected_items": selected_items or [],
                "shown_prices": shown_prices or [],
                "customer_details": customer_details or {},
                "payment": payment or {},
                "fulfillment": fulfillment or {},
                "missing_authority": _string_list(missing_authority),
                "next_best_action": _clean_optional(next_best_action),
                "risk_flags": _string_list(risk_flags),
            }
        )
        source_refs = _string_list(source_refs)
        key = idempotency_key or self._idempotency_key(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            stage=stage,
            active_intent=active_intent,
            summary=summary,
            state=state,
            source_refs=source_refs,
        )
        existing = await self._load_by_idempotency(
            workspace_id=workspace_id,
            idempotency_key=key,
        )
        if existing is not None:
            return _to_result(existing)

        snapshot = AgentConversationStateSnapshot(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            agent_id=agent_id,
            hermes_run_id=hermes_run_id,
            stage=_clean_label(stage, default="unknown", max_len=80),
            active_intent=_clean_optional(active_intent, max_len=120),
            summary=(summary or "").strip(),
            state=state,
            source_refs=source_refs,
            idempotency_key=key,
            created_at=utc_now(),
        )
        self._db.add(snapshot)
        await self._db.flush()
        await self._update_agent_session(
            agent_session_id=agent_session_id,
            summary=snapshot.summary,
        )
        await self._append_session_event(snapshot=snapshot)
        await self._record_hermes_event(snapshot=snapshot)
        await self._db.flush()
        return _to_result(snapshot)

    async def apply_turn_facts(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None = None,
        hermes_run_id: str | None = None,
        signals: TurnSignals,
    ) -> AgentConversationStateResult | None:
        """Derive conversation-state facts from what committedly happened.

        Host bookkeeping only (spec 2026-06-10): the reducer RECORDS facts —
        it never initiates a business action. Returns ``None`` when the turn
        changed nothing (no snapshot spam on quiet turns).
        """
        await self._validate_scope(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
        )
        key = f"facts:{agent_session_id}:{hermes_run_id}"
        existing = await self._load_by_idempotency(
            workspace_id=workspace_id,
            idempotency_key=key,
        )
        if existing is not None:
            return _to_result(existing)

        latest = await self._latest_facts_snapshot(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
        )
        previous_facts: dict[str, Any] = {}
        previous_summary = ""
        previous_intent: str | None = None
        if latest is not None:
            previous_facts = dict((latest.state or {}).get("facts") or {})
            previous_summary = latest.summary or ""
            previous_intent = latest.active_intent
        facts = reduce_facts(previous_facts, signals)
        if facts == previous_facts:
            return None

        summary = previous_summary
        active_intent = previous_intent
        if signals.intelligence:
            last = signals.intelligence[-1]
            notes = [str(note).strip() for note in (last.get("owner_notes") or []) if str(note).strip()]
            if notes:
                summary = "; ".join(notes)
            next_action = str(last.get("next_best_action") or "").strip()
            if next_action:
                active_intent = next_action

        snapshot = AgentConversationStateSnapshot(
            workspace_id=workspace_id,
            agent_session_id=agent_session_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            agent_id=agent_id,
            hermes_run_id=hermes_run_id,
            stage=_clean_label(stage_label(facts), default="unknown", max_len=80),
            active_intent=_clean_optional(active_intent, max_len=120),
            summary=(summary or "").strip(),
            state=_compact_jsonable({"facts": facts}),
            source_refs=_string_list([f"hermes_run:{hermes_run_id}"] if hermes_run_id else []),
            idempotency_key=key,
            created_at=utc_now(),
        )
        self._db.add(snapshot)
        await self._db.flush()
        await self._update_agent_session(
            agent_session_id=agent_session_id,
            summary=snapshot.summary,
        )
        await self._append_session_event(snapshot=snapshot)
        await self._db.flush()
        return _to_result(snapshot)

    async def _latest_facts_snapshot(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
    ) -> AgentConversationStateSnapshot | None:
        """Latest snapshot that actually CARRIES facts.

        Two shapes share this table: ``apply_turn_facts`` writes
        ``state={"facts": {...}}`` while ``conversation.set_state`` writes a
        commercial packet with no ``facts`` key. A naive "newest row" read lets a
        later ``set_state`` packet hide the accrued facts (the lead silently
        resets to 'new'), so the previous-facts read-back must filter to
        facts-carrying rows. (#423)
        """
        stmt = (
            select(AgentConversationStateSnapshot)
            .where(
                AgentConversationStateSnapshot.workspace_id == workspace_id,
                AgentConversationStateSnapshot.agent_session_id == agent_session_id,
                AgentConversationStateSnapshot.state.has_key("facts"),
            )
            .order_by(AgentConversationStateSnapshot.created_at.desc(), AgentConversationStateSnapshot.id.desc())
            .limit(1)
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def latest_compact_state(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
    ) -> dict[str, Any]:
        stmt = (
            select(AgentConversationStateSnapshot)
            .where(
                AgentConversationStateSnapshot.workspace_id == workspace_id,
                AgentConversationStateSnapshot.agent_session_id == agent_session_id,
            )
            .order_by(AgentConversationStateSnapshot.created_at.desc(), AgentConversationStateSnapshot.id.desc())
            .limit(1)
        )
        snapshot = (await self._db.execute(stmt)).scalar_one_or_none()
        if snapshot is None:
            return {}
        return _to_result(snapshot).compact_state()

    async def _validate_scope(
        self,
        *,
        workspace_id: int,
        agent_session_id: int,
        agent_id: int,
        conversation_id: int,
        customer_id: int | None,
    ) -> None:
        session = await self._db.get(AgentSession, agent_session_id)
        if session is None:
            raise ValueError("agent_session_not_found")
        if (
            session.workspace_id != workspace_id
            or session.agent_id != agent_id
            or session.conversation_id != conversation_id
        ):
            raise ValueError("agent_session_scope_mismatch")
        if customer_id is not None and session.customer_id is not None and session.customer_id != customer_id:
            raise ValueError("agent_session_customer_mismatch")

    async def _load_by_idempotency(
        self,
        *,
        workspace_id: int,
        idempotency_key: str,
    ) -> AgentConversationStateSnapshot | None:
        return (
            await self._db.execute(
                select(AgentConversationStateSnapshot).where(
                    AgentConversationStateSnapshot.workspace_id == workspace_id,
                    AgentConversationStateSnapshot.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()

    async def _update_agent_session(
        self,
        *,
        agent_session_id: int,
        summary: str,
    ) -> None:
        session = await self._db.get(AgentSession, agent_session_id, populate_existing=True)
        if session is None:
            return
        if summary.strip():
            session.summary = summary.strip()
        session.updated_at = utc_now()

    async def _append_session_event(self, *, snapshot: AgentConversationStateSnapshot) -> None:
        try:
            await AgentSessionService(self._db).append_event(
                agent_session_id=snapshot.agent_session_id,
                workspace_id=snapshot.workspace_id,
                conversation_id=snapshot.conversation_id,
                agent_id=snapshot.agent_id,
                event_type="conversation_state",
                direction="internal",
                hermes_run_id=snapshot.hermes_run_id,
                text=snapshot.summary,
                payload={
                    "snapshot_id": snapshot.id,
                    "stage": snapshot.stage,
                    "active_intent": snapshot.active_intent,
                    "state": snapshot.state,
                    "source_refs": snapshot.source_refs,
                },
                idempotency_key=f"{snapshot.idempotency_key}:agent-session-event",
            )
        except Exception:
            return

    async def _record_hermes_event(self, *, snapshot: AgentConversationStateSnapshot) -> None:
        if not snapshot.hermes_run_id:
            return
        try:
            await HermesRunService(self._db).record_event(
                HermesRunEventInput(
                    run_id=snapshot.hermes_run_id,
                    workspace_id=snapshot.workspace_id,
                    kind=HermesRunEventKind.TOOL_CALLED,
                    visibility="internal",
                    tool_name="conversation.set_state",
                    tool_state="ok",
                    payload={
                        "snapshot_id": snapshot.id,
                        "stage": snapshot.stage,
                        "active_intent": snapshot.active_intent,
                        "summary_chars": len(snapshot.summary or ""),
                        "state_keys": sorted(snapshot.state.keys()),
                        "source_refs": snapshot.source_refs,
                    },
                    correlation_id=f"conversation-state:{snapshot.workspace_id}:{snapshot.agent_session_id}",
                    idempotency_key=f"{snapshot.idempotency_key}:hermes-event",
                )
            )
        except Exception:
            return

    @staticmethod
    def _idempotency_key(
        *,
        workspace_id: int,
        agent_session_id: int,
        stage: str,
        active_intent: str | None,
        summary: str,
        state: dict[str, Any],
        source_refs: list[str],
    ) -> str:
        raw = json.dumps(
            {
                "workspace_id": workspace_id,
                "agent_session_id": agent_session_id,
                "stage": stage,
                "active_intent": active_intent,
                "summary": summary,
                "state": state,
                "source_refs": source_refs,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"conversation-state:{workspace_id}:{agent_session_id}:{digest}"


def _to_result(snapshot: AgentConversationStateSnapshot) -> AgentConversationStateResult:
    return AgentConversationStateResult(
        snapshot_id=snapshot.id,
        stage=snapshot.stage,
        active_intent=snapshot.active_intent,
        summary=snapshot.summary,
        state=dict(snapshot.state or {}),
        source_refs=_string_list(snapshot.source_refs),
    )


def _clean_label(value: str | None, *, default: str, max_len: int) -> str:
    text = (value or "").strip() or default
    return text[:max_len]


def _clean_optional(value: str | None, *, max_len: int | None = None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return text[:max_len] if max_len is not None else text


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _compact_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
