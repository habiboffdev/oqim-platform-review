from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.modules.hermes_runtime.contracts import HermesRunLane


@dataclass(frozen=True)
class HermesLaneLimits:
    global_caps: dict[str, int] = field(
        default_factory=lambda: {
            HermesRunLane.FAST_INTERACTIVE.value: 8,
            HermesRunLane.BACKGROUND.value: 4,
            HermesRunLane.BROADCAST.value: 2,
            HermesRunLane.DEEP_ANALYSIS.value: 2,
        }
    )
    per_workspace_caps: dict[str, int] = field(
        default_factory=lambda: {
            HermesRunLane.FAST_INTERACTIVE.value: 2,
            HermesRunLane.BACKGROUND.value: 2,
            HermesRunLane.BROADCAST.value: 1,
            HermesRunLane.DEEP_ANALYSIS.value: 1,
        }
    )
    wait_timeout_seconds: float = 0.25


@dataclass(frozen=True)
class HermesLaneAcquisition:
    acquired: bool
    lane: str
    workspace_id: int
    waited_ms: int
    reason: str | None = None


class HermesLaneLimiter:
    def __init__(self, limits: HermesLaneLimits | None = None) -> None:
        self._limits = limits or HermesLaneLimits()
        self._global: dict[str, asyncio.Semaphore] = {
            lane: asyncio.Semaphore(max(1, cap))
            for lane, cap in self._limits.global_caps.items()
        }
        self._workspace: dict[tuple[str, int], asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        *,
        lane: HermesRunLane | str,
        workspace_id: int,
        wait_timeout_seconds: float | None = None,
    ) -> tuple[HermesLaneAcquisition, _HermesLaneRelease]:
        lane_value = lane.value if isinstance(lane, HermesRunLane) else str(lane)
        timeout = self._limits.wait_timeout_seconds if wait_timeout_seconds is None else wait_timeout_seconds
        started = time.perf_counter()
        global_sem = self._global.setdefault(lane_value, asyncio.Semaphore(1))
        workspace_sem = await self._workspace_semaphore(lane_value, workspace_id)

        acquired_global = False
        acquired_workspace = False
        try:
            await asyncio.wait_for(global_sem.acquire(), timeout=timeout)
            acquired_global = True
            remaining = max(0.001, timeout - (time.perf_counter() - started))
            await asyncio.wait_for(workspace_sem.acquire(), timeout=remaining)
            acquired_workspace = True
        except TimeoutError:
            if acquired_global:
                global_sem.release()
            waited_ms = int((time.perf_counter() - started) * 1000)
            return (
                HermesLaneAcquisition(
                    acquired=False,
                    lane=lane_value,
                    workspace_id=workspace_id,
                    waited_ms=waited_ms,
                    reason="lane_capacity_timeout",
                ),
                _HermesLaneRelease(None, None),
            )

        waited_ms = int((time.perf_counter() - started) * 1000)
        release = _HermesLaneRelease(global_sem, workspace_sem)
        return (
            HermesLaneAcquisition(
                acquired=acquired_global and acquired_workspace,
                lane=lane_value,
                workspace_id=workspace_id,
                waited_ms=waited_ms,
            ),
            release,
        )

    @asynccontextmanager
    async def limit(
        self,
        *,
        lane: HermesRunLane | str,
        workspace_id: int,
        wait_timeout_seconds: float | None = None,
    ) -> AsyncIterator[HermesLaneAcquisition]:
        acquisition, release = await self.acquire(
            lane=lane,
            workspace_id=workspace_id,
            wait_timeout_seconds=wait_timeout_seconds,
        )
        try:
            yield acquisition
        finally:
            release.release()

    async def _workspace_semaphore(self, lane: str, workspace_id: int) -> asyncio.Semaphore:
        key = (lane, workspace_id)
        async with self._lock:
            existing = self._workspace.get(key)
            if existing is not None:
                return existing
            cap = max(1, self._limits.per_workspace_caps.get(lane, 1))
            created = asyncio.Semaphore(cap)
            self._workspace[key] = created
            return created


@dataclass
class _HermesLaneRelease:
    global_sem: asyncio.Semaphore | None
    workspace_sem: asyncio.Semaphore | None
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        if self.workspace_sem is not None:
            self.workspace_sem.release()
        if self.global_sem is not None:
            self.global_sem.release()
