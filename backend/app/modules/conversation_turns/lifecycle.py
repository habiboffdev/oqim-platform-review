"""TurnLifecycle — the single owner of every turn-state transition (#427 S8).

`transition` is the ONLY writer of `turn.state`. Named methods (a later task) wrap
it and encode each edge's full side-effect set + its dispatcher-injected run pairing.
The guard is log-and-allow: an unexpected edge warns but applies, so production logs
reveal a missing table edge without ever blocking a live turn.

A TurnLifecycle is bound to ONE AsyncSession and must not be reused across a rollback.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import utc_now
from app.models.conversation_turn_session import ConversationTurnSession
from app.modules.conversation_turns.turn_state import (
    ALLOWED_TRANSITIONS,
    TURN_RUNNER_MAX_DISPATCH_ATTEMPTS,
    TurnState,
)

logger = get_logger("turn.lifecycle")

_UNSET = object()


class TurnLifecycle:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def _guard(self, frm: TurnState, to: TurnState, turn: Any) -> None:
        if to not in ALLOWED_TRANSITIONS.get(frm, frozenset()):
            logger.warning(
                "turn.transition.unexpected from=%s to=%s turn=%s workspace=%s",
                frm.value, to.value, getattr(turn, "id", None),
                getattr(turn, "workspace_id", None),
            )

    async def transition(self, turn: Any, to: TurnState, *,
                         stale_reason: Any = _UNSET, flush: bool = True) -> None:
        """The ONLY writer of turn.state. Sets state + updated_at, the terminal
        completed_at, and (when given) stale_reason. flush=False lets a bulk caller
        flush once."""
        self._guard(TurnState(turn.state), to, turn)
        now = utc_now()
        turn.state = to.value
        turn.updated_at = now
        if to in (TurnState.COMPLETED, TurnState.QUARANTINED):
            turn.completed_at = now
        if stale_reason is not _UNSET:
            turn.stale_reason = stale_reason
        if flush:
            await self._db.flush()

    # ------------------------------------------------------------------
    # Non-run-paired named methods (each encodes one edge's full side effects)
    # ------------------------------------------------------------------

    async def lease(self, turn: Any, *, flush: bool = False) -> None:
        await self.transition(turn, TurnState.STARTING, stale_reason=None, flush=flush)

    async def reopen_for_successor(self, turn: Any) -> None:
        turn.active_hermes_run_id = None
        turn.active_engine_run_id = None
        await self.transition(turn, TurnState.CONTINUED,
                              stale_reason="customer_message_after_finalization")

    async def reclaim_lease(self, turn: Any, *, flush: bool = False) -> None:
        await self.transition(turn, TurnState.OPEN, stale_reason="turn_lease_expired", flush=flush)

    async def complete_for_agent_message(self, turn: Any, *, reason: str,
                                         flush: bool = False) -> None:
        turn.active_hermes_run_id = None
        turn.active_engine_run_id = None
        await self.transition(turn, TurnState.COMPLETED, stale_reason=reason, flush=flush)

    async def complete_for_trigger(self, turn: Any, *, reason: str) -> None:
        turn.active_hermes_run_id = None
        turn.active_engine_run_id = None
        await self.transition(turn, TurnState.COMPLETED, stale_reason=reason)

    async def complete_pre_dispatch(self, turn: Any, *, reason: str) -> None:
        await self.transition(turn, TurnState.COMPLETED, stale_reason=reason)

    async def release_failed(self, turn: Any, *, reason: str) -> None:
        turn.failed_dispatch_count = int(turn.failed_dispatch_count or 0) + 1
        if turn.failed_dispatch_count >= TURN_RUNNER_MAX_DISPATCH_ATTEMPTS:
            logger.error(
                "Quarantined poisoned turn after %s failed dispatches: "
                "workspace=%s conv=%s turn=%s",
                turn.failed_dispatch_count, turn.workspace_id,
                turn.conversation_id, turn.id,
            )
            await self.transition(turn, TurnState.QUARANTINED, stale_reason="dispatch_poisoned")
        else:
            await self.transition(turn, TurnState.OPEN, stale_reason=reason)

    # ------------------------------------------------------------------
    # Run-paired named methods (each merges the dispatcher's run + turn writes)
    # ------------------------------------------------------------------

    async def begin_run(self, turn: Any, run_id: str, *, agent_id: int) -> Any:
        """Edge 7 (starting->running). Absorbs the FULL side-effect set of the old
        service.mark_running: mark the run running, copy the engine id from the
        returned snapshot onto the turn, stamp agent_id + started_at, then transition.
        RETURNS the run snapshot — it carries engine_run_id; dropping it would regress
        turn.active_engine_run_id to None (the #418 anti-regression catch)."""
        from app.modules.hermes_runtime.service import HermesRunService

        snap = await HermesRunService(self._db).mark_running(run_id)
        turn.agent_id = int(agent_id)
        turn.started_at = turn.started_at or utc_now()
        turn.active_hermes_run_id = run_id
        turn.active_engine_run_id = snap.engine_run_id
        await self.transition(turn, TurnState.RUNNING)
        return snap

    async def complete_finalized(self, turn: Any, *, run_id: str, run_patch: Any,
                                 finalized_revision: int | None) -> bool:
        """Edge 10 (finalizing->completed). A FAITHFUL coordinator-owned merge of the
        dispatcher's two-call pairing `run_service.complete(...)` THEN
        `complete_finalized_turn(...)`. The run write is UNCONDITIONAL and FIRST (it
        always runs today, before the turn write); the guarded turn completion then
        mirrors complete_finalized_turn (#420) EXACTLY — the same four guards on a
        freshly-reloaded turn (populate_existing — the crash-loop fix). Returns whether
        the turn was transitioned."""
        from app.modules.hermes_runtime.service import HermesRunService

        await HermesRunService(self._db).complete(run_id, run_patch)
        if not finalized_revision:
            return False
        fresh = await self._db.get(
            ConversationTurnSession, int(turn.id), populate_existing=True
        )
        if fresh is None or fresh.state != TurnState.FINALIZING:
            return False
        if int(fresh.finalized_revision or 0) != int(finalized_revision):
            return False
        if int(fresh.turn_revision or 0) > int(finalized_revision):
            return False
        await self.transition(fresh, TurnState.COMPLETED)
        return True
