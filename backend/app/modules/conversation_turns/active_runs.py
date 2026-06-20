from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class ActiveTurnRunHandle:
    workspace_id: int
    conversation_id: int
    agent_id: int
    turn_session_id: int
    hermes_run_id: str
    agent: Any
    turn_revision_start: int
    registered_at: datetime = field(default_factory=_utc_now)
    latest_known_revision: int = 0
    deferred_message_count: int = 0
    finished_at: datetime | None = None
    pending_steer_text: str | None = None

    def __post_init__(self) -> None:
        if not self.latest_known_revision:
            self.latest_known_revision = int(self.turn_revision_start)

    @property
    def key(self) -> tuple[int, int, int]:
        return (int(self.workspace_id), int(self.conversation_id), int(self.agent_id))

    def note_mid_run_message(self, *, turn_revision: int) -> None:
        """Record a customer message that arrived while this run is live.

        Mid-run messages are NEVER injected into the running Hermes loop:
        with terminal talk tools nearly every run is single-iteration, so an
        ``agent.steer()`` payload is drained into a tool-result message for a
        next LLM call that never happens — accepted, invisible, and silently
        lost (live repro: run 33 / msg 136, 2026-06-09). The message defers to
        a successor turn instead, which finalize dispatches because the
        observed revision stays behind ``turn_revision``.
        """
        self.latest_known_revision = max(self.latest_known_revision, int(turn_revision))
        self.deferred_message_count += 1

    def finish(self, *, pending_steer_text: str | None = None) -> dict[str, Any]:
        self.finished_at = _utc_now()
        self.pending_steer_text = pending_steer_text
        leftover_count = 1 if (pending_steer_text or "").strip() else 0
        return {
            "turn_session_id": self.turn_session_id,
            "turn_revision_start": self.turn_revision_start,
            "latest_known_revision": self.latest_known_revision,
            # The model only ever saw the turn it started with; anything that
            # arrived mid-run belongs to the successor turn.
            "observed_revision": self.turn_revision_start,
            "steer_count": self.deferred_message_count,
            "steer_deferred_count": self.deferred_message_count,
            "steer_leftover_count": leftover_count,
            "pending_steer_count": leftover_count,
        }


class ActiveTurnRunRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handles: dict[tuple[int, int, int], ActiveTurnRunHandle] = {}

    def register(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        agent_id: int,
        turn_session_id: int,
        hermes_run_id: str,
        agent: Any,
        turn_revision_start: int,
    ) -> ActiveTurnRunHandle:
        handle = ActiveTurnRunHandle(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            turn_session_id=turn_session_id,
            hermes_run_id=hermes_run_id,
            agent=agent,
            turn_revision_start=turn_revision_start,
        )
        with self._lock:
            self._handles[handle.key] = handle
        return handle

    def unregister(self, handle: ActiveTurnRunHandle) -> None:
        with self._lock:
            if self._handles.get(handle.key) is handle:
                self._handles.pop(handle.key, None)

    def finish(self, handle: ActiveTurnRunHandle, *, pending_steer_text: str | None = None) -> dict[str, Any]:
        try:
            return handle.finish(pending_steer_text=pending_steer_text)
        finally:
            self.unregister(handle)

    def note_mid_run_message(
        self,
        *,
        workspace_id: int,
        conversation_id: int,
        turn_session_id: int,
        turn_revision: int,
    ) -> ActiveTurnRunHandle | None:
        with self._lock:
            handles = [
                handle
                for handle in self._handles.values()
                if int(handle.workspace_id) == int(workspace_id)
                and int(handle.conversation_id) == int(conversation_id)
                and int(handle.turn_session_id) == int(turn_session_id)
            ]
        if not handles:
            return None
        handle = handles[0]
        handle.note_mid_run_message(turn_revision=turn_revision)
        return handle

    def clear(self) -> None:
        with self._lock:
            self._handles.clear()


active_turn_run_registry = ActiveTurnRunRegistry()
