from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")

DEFAULT_OPERATION_DELAYS: dict[str, float] = {
    "dialogs": 0.75,
    "messages": 0.35,
    "media": 0.75,
}
DEFAULT_MAX_WAIT_SECONDS = 2.0


class ChannelSyncRateLimitError(RuntimeError):
    def __init__(
        self,
        *,
        retry_after_seconds: float,
        channel: str,
        operation: str,
    ) -> None:
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))
        self.channel = channel
        self.operation = operation
        super().__init__(
            f"Rate limited for channel={channel} operation={operation} "
            f"retry_after={self.retry_after_seconds:.2f}s"
        )


@dataclass(slots=True)
class _WorkspaceChannelState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_allowed_at: float = 0.0
    last_operation: str | None = None


class ChannelSyncRuntime:
    def __init__(
        self,
        *,
        operation_delays: dict[str, float] | None = None,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._operation_delays = dict(DEFAULT_OPERATION_DELAYS)
        if operation_delays:
            self._operation_delays.update(operation_delays)
        self._max_wait_seconds = max(0.0, max_wait_seconds)
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._states: dict[tuple[int, str], _WorkspaceChannelState] = {}

    async def run(
        self,
        *,
        workspace_id: int,
        channel: str,
        operation: str,
        func: Callable[[], Awaitable[T]],
    ) -> T:
        state = self._states.setdefault((workspace_id, channel), _WorkspaceChannelState())
        async with state.lock:
            wait_seconds = max(0.0, state.next_allowed_at - self._clock())
            if wait_seconds > 0:
                if wait_seconds > self._max_wait_seconds:
                    raise ChannelSyncRateLimitError(
                        retry_after_seconds=wait_seconds,
                        channel=channel,
                        operation=operation,
                    )
                await self._sleep(wait_seconds)

            try:
                result = await func()
            except ChannelSyncRateLimitError as exc:
                state.next_allowed_at = max(
                    state.next_allowed_at,
                    self._clock() + max(exc.retry_after_seconds, self._delay_for(operation)),
                )
                state.last_operation = operation
                raise

            state.next_allowed_at = max(
                state.next_allowed_at,
                self._clock() + self._delay_for(operation),
            )
            state.last_operation = operation
            return result

    def _delay_for(self, operation: str) -> float:
        return max(0.0, self._operation_delays.get(operation, 0.25))


_runtime: ChannelSyncRuntime | None = None


def get_channel_sync_runtime() -> ChannelSyncRuntime:
    global _runtime
    if _runtime is None:
        _runtime = ChannelSyncRuntime()
    return _runtime
