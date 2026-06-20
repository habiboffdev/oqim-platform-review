from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.conversation import Conversation
from app.models.conversation_turn_session import ConversationTurnSession
from app.models.customer import Customer
from app.models.hermes_run import HermesRun
from app.models.message import Message
from app.modules.agent_runtime_v2.prompt_text import (
    message_prompt_text as _message_prompt_text,
)
from app.modules.agent_sessions.service import AgentSessionService
from app.modules.conversation_turns.active_runs import active_turn_run_registry
from app.modules.conversation_turns.contracts import TurnLease
from app.modules.conversation_turns.lifecycle import TurnLifecycle

# Non-terminal = every state that still owes the conversation a dispatch or is
# mid-flight. 'continued' belongs here: it is leasable (a successor run is
# owed), so it MUST also block new turn inserts. The 2026-06-11 crash-loop was
# a duplicate turn created while the only existing turn sat in 'continued' —
# invisible to _load_active, then fatal when lease_ready_turns flipped it into
# the active unique index where the duplicate already lived. The tuples are
# re-exported here so existing `from ...service import NON_TERMINAL_TURN_STATES`
# callers keep working; their members are TurnState (a StrEnum), so they compare
# and bind equal to their string values — the no-migration design's whole point.
from app.modules.conversation_turns.turn_state import (
    ACTIVE_TURN_STATES,
    NON_TERMINAL_TURN_STATES,
    TurnState,
)
from app.modules.hermes_runtime.contracts import (
    HermesRunEventInput,
    HermesRunEventKind,
    HermesRunState,
)
from app.modules.hermes_runtime.service import HermesRunService

# Re-export the state tuples so existing `from ...service import ...` callers keep
# working after the canonical definitions moved to turn_state.py. Declaring them in
# __all__ marks the re-export as intentional (otherwise the unused-import lint would
# fire on ACTIVE_TURN_STATES, which is consumed only via this re-export, not in-module).
__all__ = [
    "ACTIVE_TURN_STATES",
    "NON_TERMINAL_TURN_STATES",
    "ConversationTurnSessionService",
    "CustomerTurnSnapshot",
    "TurnFinalizationResult",
]

# Burst-coalescing window: wait this long after the customer's latest bubble
# before dispatching the turn, so multi-bubble bursts become ONE run.
# Typing-aware design (2026-06-10): a short message debounce is the latency
# floor, and the lease additionally HOLDS while the sidecar reports the
# customer typing ("yozmoqda…"), so salom+question bursts coalesce without
# taxing every reply with a long fixed window. A hard cap (measured from the
# last message) guarantees a drafting customer can never stall the agent.
TURN_COALESCE_SECONDS = 4.0  # covers the think-pause before typing starts
TYPING_HOLD_SECONDS = 5.0
TYPING_MAX_HOLD_SECONDS = 25.0

# Media hydration hold: a turn whose latest customer bubble is AI-relevant media
# is NOT leased until that media reaches a terminal hydration state. Otherwise the
# agent answers a bare "[voice]"/"[photo]" placeholder ("I can't hear it") before
# the transcript/native bytes exist — the media downloads + processes (~seconds)
# slower than the burst window (live failure 2026-06-13). The hydration worker
# re-touches the turn on completion (should_wake_agent_turn), and the cap (from
# the last message) guarantees stuck hydration can never stall a turn forever.
MEDIA_HYDRATION_HOLD_TYPES = ("voice", "audio", "photo", "sticker")
MEDIA_HYDRATION_TERMINAL_STATUSES = (
    "hydrated", "unavailable", "unsupported", "not_applicable", "failed", "expired",
)
MEDIA_HYDRATION_MAX_HOLD_SECONDS = 90.0


@dataclass(frozen=True)
class TurnFinalizationResult:
    can_deliver: bool
    needs_successor: bool
    turn_session_id: int
    turn_revision: int
    observed_revision: int
    finalized_revision: int | None
    latest_customer_message_id: int
    reason: str | None = None


@dataclass(frozen=True)
class CustomerTurnSnapshot:
    """Ordered customer bubbles that make up the current agent-visible turn."""

    turn_session_id: int
    turn_revision: int
    messages: tuple[Message, ...]

    @property
    def latest_message_id(self) -> int | None:
        if not self.messages:
            return None
        return int(self.messages[-1].id)

    @property
    def message_ids(self) -> list[int]:
        return [int(message.id) for message in self.messages]

    @property
    def query_text(self) -> str:
        return "\n".join(
            text
            for message in self.messages
            if (text := _message_prompt_text(message))
        ).strip()

    def prompt_text(self, *, instruction_override: str | None = None) -> str:
        override = (instruction_override or "").strip()
        if not self.messages:
            return override
        lines = [
            f"{index}. {_message_prompt_text(message)}"
            for index, message in enumerate(self.messages, start=1)
            if _message_prompt_text(message)
        ]
        if not lines:
            body = ""
        elif len(lines) == 1:
            body = lines[0].split(". ", 1)[1]
        else:
            body = "Mijozning joriy navbati (tartiblangan xabarlar):\n" + "\n".join(lines)
        if override:
            return f"{body}\n\n[Seller instruction: {override}]" if body else override
        return body


class ConversationTurnSessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._lifecycle = TurnLifecycle(db)

    async def mark_customer_typing(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
    ) -> int:
        """Record a transient "yozmoqda…" signal on the conversation's open turn.

        Best-effort dispatch-timing input only — never business truth and
        never an action trigger. Returns the number of turns touched.
        """
        result = await self._db.execute(
            update(ConversationTurnSession)
            .where(
                ConversationTurnSession.workspace_id == workspace_id,
                ConversationTurnSession.conversation_id == conversation_id,
                ConversationTurnSession.state.in_(("open", "continued")),
            )
            .values(latest_customer_typing_at=utc_now())
        )
        return int(result.rowcount or 0)

    async def append_customer_message(
        self,
        *,
        workspace_id: int,
        conversation: Conversation,
        customer: Customer | None,
        message: Message,
        agent_id: int | None = None,
    ) -> ConversationTurnSession:
        now = utc_now()
        observed_at = _message_observed_at(message)
        turn = await self._load_active(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            agent_id=agent_id,
        )
        if turn is None:
            created = ConversationTurnSession(
                workspace_id=workspace_id,
                conversation_id=conversation.id,
                agent_id=int(agent_id or 0),
                channel=_turn_channel(conversation, message),
                state="open",
                turn_key=f"conversation:{conversation.id}:agent:{int(agent_id or 0)}:{message.id}",
                turn_revision=1,
                first_customer_message_id=message.id,
                latest_customer_message_id=message.id,
                latest_customer_message_at=observed_at,
                created_at=now,
                updated_at=now,
            )
            try:
                async with self._db.begin_nested():
                    self._db.add(created)
                    await self._db.flush()
            except IntegrityError:
                # Concurrent inbound won the active-turn insert (rapid bubbles
                # racing through the persist consumer). Recover by appending to
                # the winner instead of crashing the turn pipeline.
                turn = await self._load_active(
                    workspace_id=workspace_id,
                    conversation_id=conversation.id,
                    agent_id=agent_id,
                )
                if turn is None:
                    raise
            else:
                await self._append_customer_session_event(
                    turn=created,
                    conversation=conversation,
                    customer=customer,
                    message=message,
                    agent_id=int(agent_id or created.agent_id or 0),
                )
                return created

        if agent_id is not None and int(turn.agent_id or 0) == 0:
            turn.agent_id = int(agent_id)
        was_finalizing = turn.state == "finalizing"
        appended_newer_message = False
        if int(turn.latest_customer_message_id) != int(message.id):
            if not await self._message_is_newer_than_turn_latest(turn=turn, message=message):
                return turn
            turn.turn_revision = int(turn.turn_revision or 1) + 1
            appended_newer_message = True
        turn.latest_customer_message_id = message.id
        turn.latest_customer_message_at = observed_at
        turn.updated_at = now
        if was_finalizing and appended_newer_message:
            # clears both active run-ids + sets stale_reason + state=continued
            await self._lifecycle.reopen_for_successor(turn)
        elif turn.state != "continued":
            # A fresh message on an open/starting/running turn supersedes any old
            # lease/reclaim reason; a 'continued' turn KEEPS its reason — it still
            # explains why the successor dispatch is owed.
            turn.stale_reason = None
        await self._db.flush()

        await self._append_customer_session_event(
            turn=turn,
            conversation=conversation,
            customer=customer,
            message=message,
            agent_id=int(agent_id or turn.agent_id or 0),
        )
        if turn.state == "running":
            await self._steer_active_run(turn=turn, message=message)
        return turn

    async def mark_running(
        self,
        *,
        turn_session_id: int,
        agent_id: int,
        hermes_run_id: str,
        engine_run_id: str | None = None,
    ) -> ConversationTurnSession:
        """Set a turn running with explicit run-ids (state via the coordinator).

        Retained for test setup + any direct caller. The LIVE dispatcher routes the
        run↔turn begin-run pairing through ``TurnLifecycle.begin_run`` (which marks a
        real HermesRun running); this lighter helper does not require a run row. (#427)
        """
        turn = await self._get(turn_session_id)
        turn.agent_id = int(agent_id)
        turn.active_hermes_run_id = hermes_run_id
        turn.active_engine_run_id = engine_run_id
        turn.started_at = turn.started_at or utc_now()
        await self._lifecycle.transition(turn, TurnState.RUNNING)
        return turn

    async def lease_ready_turns(
        self,
        *,
        limit: int,
        max_per_workspace: int,
        coalesce_seconds: float = TURN_COALESCE_SECONDS,
    ) -> list[TurnLease]:
        """Claim ready turn sessions for the future turn runner.

        The query ranks turns inside each workspace, then orders by that rank so
        every eligible workspace gets a first turn before a noisy workspace gets
        a second. `starting` plus `updated_at` is the stale-lease recovery
        contract.

        Turns whose latest customer message landed inside the coalescing
        window are NOT leased yet: Telegram customers send bursts ("salom" +
        the real question seconds apart), and dispatching on the first bubble
        wastes a full LLM run on a reply that finalize will discard (run 33,
        2026-06-09). Each new bubble restarts the window via
        ``latest_customer_message_at``; humans don't answer in under ~3s
        anyway, and the typing indicator covers the pause.
        """
        batch_limit = max(1, int(limit or 1))
        workspace_limit = max(1, int(max_per_workspace or 1))
        now = utc_now()
        coalesce_cutoff = now - timedelta(seconds=max(0.0, float(coalesce_seconds)))
        typing_cutoff = now - timedelta(seconds=TYPING_HOLD_SECONDS)
        typing_cap_cutoff = now - timedelta(seconds=TYPING_MAX_HOLD_SECONDS)
        media_cap_cutoff = now - timedelta(seconds=MEDIA_HYDRATION_MAX_HOLD_SECONDS)
        # A turn is held while its latest bubble is AI-relevant media still
        # hydrating (transcript/native bytes not yet available). Released by the
        # hydration worker re-touching the turn; bounded by media_cap_cutoff.
        media_pending = (
            select(Message.id)
            .where(
                Message.id == ConversationTurnSession.latest_customer_message_id,
                Message.media_type.in_(MEDIA_HYDRATION_HOLD_TYPES),
                func.coalesce(
                    Message.media_metadata.op("->>")("hydration_status"), "pending"
                ).notin_(MEDIA_HYDRATION_TERMINAL_STATUSES),
            )
            .correlate(ConversationTurnSession)
            .exists()
        )
        rank = func.row_number().over(
            partition_by=ConversationTurnSession.workspace_id,
            order_by=(ConversationTurnSession.updated_at.asc(), ConversationTurnSession.id.asc()),
        ).label("workspace_rank")
        ready = (
            select(
                ConversationTurnSession.id.label("turn_id"),
                ConversationTurnSession.updated_at.label("updated_at"),
                rank,
            )
            .where(
                ConversationTurnSession.state.in_(("open", "continued")),
                or_(
                    ConversationTurnSession.latest_customer_message_at.is_(None),
                    ConversationTurnSession.latest_customer_message_at <= coalesce_cutoff,
                ),
                # typing hold: wait while the customer is mid-thought, but the
                # cap (from the LAST MESSAGE) bounds the total extra wait
                or_(
                    ConversationTurnSession.latest_customer_typing_at.is_(None),
                    ConversationTurnSession.latest_customer_typing_at <= typing_cutoff,
                    ConversationTurnSession.latest_customer_message_at <= typing_cap_cutoff,
                ),
                # media hydration hold: not leased while media is still hydrating,
                # unless it has been pending past the cap (stuck-hydration guard)
                or_(
                    ~media_pending,
                    ConversationTurnSession.latest_customer_message_at <= media_cap_cutoff,
                ),
            )
            .subquery()
        )
        candidate_rows = list(
            (
                await self._db.execute(
                    select(ready.c.turn_id)
                    .where(ready.c.workspace_rank <= workspace_limit)
                    .order_by(ready.c.workspace_rank.asc(), ready.c.updated_at.asc(), ready.c.turn_id.asc())
                    .limit(batch_limit)
                )
            ).scalars().all()
        )
        if not candidate_rows:
            return []

        order = {int(turn_id): index for index, turn_id in enumerate(candidate_rows)}
        turns = list(
            (
                await self._db.execute(
                    select(ConversationTurnSession)
                    .where(ConversationTurnSession.id.in_(candidate_rows))
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
        )
        turns.sort(key=lambda turn: order.get(int(turn.id), batch_limit))
        leases: list[TurnLease] = []
        for turn in turns[:batch_limit]:
            if turn.state not in {"open", "continued"}:
                continue
            await self._lifecycle.lease(turn, flush=False)
            leases.append(
                TurnLease(
                    turn_session_id=int(turn.id),
                    workspace_id=int(turn.workspace_id),
                    conversation_id=int(turn.conversation_id),
                    agent_id=int(turn.agent_id or 0),
                    latest_customer_message_id=int(turn.latest_customer_message_id),
                    turn_revision=int(turn.turn_revision or 1),
                    generation=int(turn.generation or 1),
                )
            )
        await self._db.flush()
        return leases

    async def reclaim_stale_turn_leases(
        self,
        *,
        lease_ttl_seconds: int,
        limit: int,
    ) -> int:
        cutoff = utc_now() - timedelta(seconds=max(1, int(lease_ttl_seconds or 1)))
        rows = list(
            (
                await self._db.execute(
                    select(ConversationTurnSession)
                    .where(
                        ConversationTurnSession.state == "starting",
                        ConversationTurnSession.updated_at <= cutoff,
                    )
                    .order_by(ConversationTurnSession.updated_at.asc(), ConversationTurnSession.id.asc())
                    .limit(max(1, int(limit or 1)))
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
        )
        for turn in rows:
            await self._lifecycle.reclaim_lease(turn, flush=False)
        await self._db.flush()
        return len(rows)

    async def reconcile_failed_run_turns(self, *, limit: int) -> int:
        """Terminate turn-sessions orphaned by a failed run.

        A turn aborted between ``mark_running`` and completion (an oqim-api
        restart mid-turn, or a post-send error, #418) has its HermesRun
        reclaimed to 'failed' by ``reclaim_stale_running_runs`` -- but the
        turn-session itself stays non-terminal, because ``reclaim_stale_turn_
        leases`` only recovers 'starting' leases, never 'running'/'finalizing'.
        The orphan then swallows every new customer message (they coalesce into
        it via ``append_customer_message`` and never dispatch a fresh run), so
        the conversation goes silent until something clears it.

        Mark such turns terminal (the conversation unblocks; the next message
        starts a fresh turn). Scoped to turns whose active run is FAILED, so a
        genuinely live 'running' turn (its run is still 'running') is never
        touched -- no false-positive termination of in-flight work.
        """
        turns = list(
            (
                await self._db.execute(
                    select(ConversationTurnSession)
                    .where(ConversationTurnSession.state.in_(NON_TERMINAL_TURN_STATES))
                    .order_by(
                        ConversationTurnSession.updated_at.asc(),
                        ConversationTurnSession.id.asc(),
                    )
                    .limit(max(1, int(limit or 1)))
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        run_ids = {t.active_hermes_run_id for t in turns if t.active_hermes_run_id}
        if not run_ids:
            return 0
        failed_run_ids = set(
            (
                await self._db.execute(
                    select(HermesRun.run_id).where(
                        HermesRun.run_id.in_(run_ids),
                        HermesRun.state == str(HermesRunState.FAILED),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not failed_run_ids:
            return 0
        reconciled = 0
        for turn in turns:
            if turn.active_hermes_run_id not in failed_run_ids:
                continue
            # Same side-effect set as edge 5 (clear both run-ids + terminal complete
            # with a reason), routed through the coordinator so turn.state stays
            # single-owned (S8 #427).
            await self._lifecycle.complete_for_agent_message(
                turn, reason="run_failed_reclaimed", flush=False
            )
            reconciled += 1
        await self._db.flush()
        return reconciled

    async def complete_active_turns_for_agent_message(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
    ) -> int:
        rows = list(
            (
                await self._db.execute(
                    select(ConversationTurnSession)
                    .where(
                        ConversationTurnSession.workspace_id == workspace_id,
                        ConversationTurnSession.conversation_id == conversation_id,
                        ConversationTurnSession.state.in_(NON_TERMINAL_TURN_STATES),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
        )
        for turn in rows:
            await self._lifecycle.complete_for_agent_message(
                turn, reason="agent_message_sent", flush=False
            )
        await self._db.flush()
        return len(rows)

    async def complete_turn_for_trigger(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        trigger_message_id: int,
        reason: str,
    ) -> bool:
        turn = await self._db.scalar(
            select(ConversationTurnSession)
            .where(
                ConversationTurnSession.workspace_id == workspace_id,
                ConversationTurnSession.conversation_id == conversation_id,
                ConversationTurnSession.latest_customer_message_id == trigger_message_id,
                ConversationTurnSession.state.in_(("open", "starting", "continued", "finalizing")),
            )
            .order_by(ConversationTurnSession.id.desc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if turn is None:
            return False
        await self._lifecycle.complete_for_trigger(turn, reason=reason)
        return True

    async def load_customer_turn_snapshot(
        self,
        *,
        turn_session_id: int,
        max_messages: int = 12,
    ) -> CustomerTurnSnapshot:
        turn = await self._get(turn_session_id)
        first = await self._db.get(Message, int(turn.first_customer_message_id))
        latest = await self._db.get(Message, int(turn.latest_customer_message_id))
        if first is None or latest is None:
            return CustomerTurnSnapshot(
                turn_session_id=turn.id,
                turn_revision=int(turn.turn_revision or 1),
                messages=tuple(message for message in (latest, first) if message is not None),
            )

        stmt = select(Message).where(
            Message.conversation_id == turn.conversation_id,
            Message.sender_type == "customer",
            Message.is_deleted.is_(False),
        )
        first_tg = getattr(first, "telegram_message_id", None)
        latest_tg = getattr(latest, "telegram_message_id", None)
        if first_tg is not None and latest_tg is not None:
            low, high = sorted((int(first_tg), int(latest_tg)))
            stmt = stmt.where(
                Message.telegram_message_id >= low,
                Message.telegram_message_id <= high,
            )
        else:
            first_at = _message_observed_at(first)
            latest_at = _message_observed_at(latest)
            low_at, high_at = sorted((first_at, latest_at))
            observed_at = func.coalesce(Message.telegram_timestamp, Message.created_at)
            stmt = stmt.where(
                observed_at >= low_at,
                observed_at <= high_at,
            )

        rows = list((await self._db.execute(stmt)).scalars().all())
        if not rows:
            rows = [first] if first.id == latest.id else [first, latest]
        rows = sorted(rows, key=_message_order_key)
        if max_messages > 0 and len(rows) > max_messages:
            rows = rows[-max_messages:]
        return CustomerTurnSnapshot(
            turn_session_id=turn.id,
            turn_revision=int(turn.turn_revision or 1),
            messages=tuple(rows),
        )

    async def finalize_run_observation(
        self,
        *,
        turn_session_id: int,
        hermes_run_id: str,
        observed_revision: int,
        pending_steer_count: int = 0,
        force_successor: bool = False,
    ) -> TurnFinalizationResult:
        turn = await self._get(turn_session_id)
        latest_revision = int(turn.turn_revision or 1)
        observed = int(observed_revision or 0)
        pending = int(pending_steer_count or 0)
        needs_successor = force_successor or pending > 0 or observed < latest_revision
        turn.active_hermes_run_id = None
        turn.active_engine_run_id = None
        turn.last_model_observed_revision = observed
        if needs_successor:
            reason = "pending_steer_leftover" if pending > 0 else "turn_revision_not_observed"
            if force_successor and pending <= 0 and observed >= latest_revision:
                reason = "context_refresh_required"
            await self._lifecycle.transition(turn, TurnState.CONTINUED, stale_reason=reason)
            await self._record_turn_event(
                hermes_run_id=hermes_run_id,
                workspace_id=turn.workspace_id,
                event_name="turn.finalize",
                state="continued",
                payload={
                    "turn_session_id": turn.id,
                    "turn_revision": latest_revision,
                    "observed_revision": observed,
                    "pending_steer_count": pending,
                    "reason": reason,
                    "latest_customer_message_id": turn.latest_customer_message_id,
                },
            )
            return TurnFinalizationResult(
                can_deliver=False,
                needs_successor=True,
                turn_session_id=turn.id,
                turn_revision=latest_revision,
                observed_revision=observed,
                finalized_revision=None,
                latest_customer_message_id=turn.latest_customer_message_id,
                reason=reason,
            )

        turn.finalized_revision = latest_revision
        await self._lifecycle.transition(turn, TurnState.FINALIZING, stale_reason=None)
        await self._record_turn_event(
            hermes_run_id=hermes_run_id,
            workspace_id=turn.workspace_id,
            event_name="turn.finalize",
            state="fresh",
            payload={
                "turn_session_id": turn.id,
                "turn_revision": latest_revision,
                "observed_revision": observed,
                "pending_steer_count": pending,
                "finalized_revision": latest_revision,
            },
        )
        return TurnFinalizationResult(
            can_deliver=True,
            needs_successor=False,
            turn_session_id=turn.id,
            turn_revision=latest_revision,
            observed_revision=observed,
            finalized_revision=latest_revision,
            latest_customer_message_id=turn.latest_customer_message_id,
        )

    async def complete_finalized_turn(
        self, *, turn_session_id: int, finalized_revision: int | None
    ) -> bool:
        """Transition a finalized turn to ``completed`` after its reply is committed.

        The service-level (direct + test) completion path, folded from the
        dispatcher's private copy (#420). The LIVE dispatcher now completes finalized
        turns via ``TurnLifecycle.complete_finalized`` (same guards), so this method is
        no longer on the hot path — keep it for direct/test callers (#427). Guards
        preserve the crash-loop fix: only a turn still in ``finalizing`` whose
        ``finalized_revision`` matches — and which was not superseded by a newer
        ``turn_revision`` — is completed. Returns whether the turn was transitioned.
        """
        if not finalized_revision:
            return False
        turn = await self._db.get(
            ConversationTurnSession, int(turn_session_id), populate_existing=True
        )
        if turn is None or turn.state != "finalizing":
            return False
        if int(turn.finalized_revision or 0) != int(finalized_revision):
            return False
        if int(turn.turn_revision or 0) > int(finalized_revision):
            return False
        await self._lifecycle.transition(turn, TurnState.COMPLETED)
        return True

    async def _steer_active_run(self, *, turn: ConversationTurnSession, message: Message) -> None:
        # Mid-run messages are never injected into the live Hermes loop: with
        # terminal talk tools nearly every run is single-iteration, so an
        # agent.steer() payload drains into a tool-result for a next LLM call
        # that never happens — accepted, invisible, silently lost (live repro:
        # run 33 / msg 136, 2026-06-09). Note the message on the active handle
        # instead so finalize sees observed < latest and dispatches a
        # successor turn that carries it as the live customer turn.
        handle = active_turn_run_registry.note_mid_run_message(
            workspace_id=turn.workspace_id,
            conversation_id=turn.conversation_id,
            turn_session_id=turn.id,
            turn_revision=turn.turn_revision,
        )
        if handle is None:
            return
        turn.steer_count = int(turn.steer_count or 0) + 1
        turn.last_steer_at = utc_now()
        await self._db.flush()
        await self._record_turn_event(
            hermes_run_id=handle.hermes_run_id,
            workspace_id=turn.workspace_id,
            event_name="turn.steer",
            state="deferred",
            payload={
                "turn_session_id": turn.id,
                "message_id": message.id,
                "turn_revision": turn.turn_revision,
                "deferred_to_successor": True,
            },
        )
        await self._append_steering_session_event(
            turn=turn,
            message=message,
            agent_id=int(handle.agent_id or turn.agent_id or 0),
            hermes_run_id=handle.hermes_run_id,
            accepted=False,
        )

    async def _record_turn_event(
        self,
        *,
        hermes_run_id: str,
        workspace_id: int,
        event_name: str,
        state: str,
        payload: dict[str, Any],
    ) -> None:
        run_exists = await self._db.scalar(select(HermesRun.id).where(HermesRun.run_id == hermes_run_id))
        if run_exists is None:
            return
        await HermesRunService(self._db).record_event(
            HermesRunEventInput(
                run_id=hermes_run_id,
                workspace_id=workspace_id,
                kind=HermesRunEventKind.TOOL_CALLED,
                visibility="internal",
                tool_name=event_name,
                tool_state=state,
                payload=payload,
                idempotency_key=f"{hermes_run_id}:{event_name}:{payload.get('message_id') or payload.get('turn_revision')}:{state}",
            )
        )

    async def _append_customer_session_event(
        self,
        *,
        turn: ConversationTurnSession,
        conversation: Conversation,
        customer: Customer | None,
        message: Message,
        agent_id: int,
    ) -> None:
        if agent_id <= 0:
            return
        service = AgentSessionService(self._db)
        session = await service.get_or_create(
            workspace_id=turn.workspace_id,
            conversation_id=turn.conversation_id,
            customer_id=getattr(customer, "id", None),
            agent_id=agent_id,
            channel=_turn_channel(conversation, message),
        )
        await service.append_event(
            agent_session_id=session.id,
            workspace_id=turn.workspace_id,
            conversation_id=turn.conversation_id,
            agent_id=agent_id,
            event_type="customer_message",
            direction="inbound",
            message_id=message.id,
            text=_message_prompt_text(message),
            payload={
                "turn_session_id": turn.id,
                "turn_revision": int(turn.turn_revision or 1),
                "telegram_message_id": getattr(message, "telegram_message_id", None),
                "channel": _turn_channel(conversation, message),
            },
            idempotency_key=f"message:{message.id}:customer_message:agent:{agent_id}",
        )

    async def _append_steering_session_event(
        self,
        *,
        turn: ConversationTurnSession,
        message: Message,
        agent_id: int,
        hermes_run_id: str,
        accepted: bool,
    ) -> None:
        if agent_id <= 0:
            return
        service = AgentSessionService(self._db)
        session = await service.get_or_create(
            workspace_id=turn.workspace_id,
            conversation_id=turn.conversation_id,
            customer_id=None,
            agent_id=agent_id,
            channel=str(turn.channel or getattr(message, "channel", None) or "telegram_dm"),
        )
        await service.append_event(
            agent_session_id=session.id,
            workspace_id=turn.workspace_id,
            conversation_id=turn.conversation_id,
            agent_id=agent_id,
            event_type="in_flight_steering",
            direction="internal",
            message_id=message.id,
            hermes_run_id=hermes_run_id,
            text=_message_prompt_text(message),
            payload={
                "turn_session_id": turn.id,
                "turn_revision": int(turn.turn_revision or 1),
                "accepted": accepted,
                "telegram_message_id": getattr(message, "telegram_message_id", None),
                "channel": str(turn.channel or getattr(message, "channel", None) or "telegram_dm"),
            },
            idempotency_key=f"turn:{turn.id}:steer:message:{message.id}:agent:{agent_id}",
        )

    async def _get(self, turn_session_id: int) -> ConversationTurnSession:
        turn = await self._db.get(
            ConversationTurnSession,
            turn_session_id,
            populate_existing=True,
        )
        if turn is None:
            raise ValueError(f"conversation turn session not found: {turn_session_id}")
        return turn

    async def _message_is_newer_than_turn_latest(
        self,
        *,
        turn: ConversationTurnSession,
        message: Message,
    ) -> bool:
        latest = await self._db.get(Message, int(turn.latest_customer_message_id))
        if latest is None:
            return True
        return _message_order_key(message) > _message_order_key(latest)

    async def _load_active(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        agent_id: int | None,
    ) -> ConversationTurnSession | None:
        query = select(ConversationTurnSession).where(
            ConversationTurnSession.workspace_id == workspace_id,
            ConversationTurnSession.conversation_id == conversation_id,
            ConversationTurnSession.state.in_(NON_TERMINAL_TURN_STATES),
        )
        if agent_id is not None:
            query = query.where(ConversationTurnSession.agent_id.in_([0, int(agent_id)]))
        query = query.execution_options(populate_existing=True)
        return (await self._db.execute(query.order_by(ConversationTurnSession.id.desc()).limit(1))).scalar_one_or_none()


def _message_observed_at(message: Message) -> datetime:
    return (
        getattr(message, "telegram_timestamp", None)
        or getattr(message, "created_at", None)
        or utc_now()
    )


def _message_order_key(message: Message) -> tuple[datetime, int, int]:
    telegram_message_id = getattr(message, "telegram_message_id", None)
    try:
        telegram_order = int(telegram_message_id or 0)
    except (TypeError, ValueError):
        telegram_order = 0
    return (
        _message_observed_at(message),
        telegram_order,
        int(getattr(message, "id", 0) or 0),
    )


def _turn_channel(conversation: Conversation, message: Message) -> str:
    channel = getattr(conversation, "channel", None) or getattr(message, "channel", None) or "telegram_dm"
    if channel == "dm":
        return "telegram_dm"
    return str(channel)
