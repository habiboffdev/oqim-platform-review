"""Off-lease records plane (PRD #433, ADR 2026-06-15).

The Records Pass is a second LLM call per reply-delivering turn. It MUST NOT run
on the customer reply path: the dispatcher enqueues a ``RecordsJob`` here and
returns; a small supervised ``RecordsConsumer`` pool drains the queue into
``run_records_pass`` with bounded concurrency. A model slowdown then degrades
recording freshness (a bounded backlog, drop-oldest under pressure) instead of
starving replies — the 2026-06-15 outage was the records pass awaited inline on
the serial turn lease.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger("records.queue")

RECORDS_QUEUE_MAXSIZE = 256
RECORDS_CONSUMER_POOL_SIZE = 2
# Idle workers still beat so the supervisor never marks the pool stale while the
# queue is empty; bounded get() wakes them on this cadence.
_IDLE_BEAT_SECONDS = 5.0


@dataclass
class RecordsJob:
    """The records pass inputs captured from the dispatcher scope at enqueue time.

    Carried by reference: the records pass only READS these and appends to the
    in-memory ``session_db`` without flushing, so the dispatcher's now-closed db is
    harmless and no DB reconstruction is needed (why in-process beat a worker)."""

    workspace_id: int
    conversation_id: int
    customer_id: int
    agent_id: int
    agent_session_id: int
    hermes_run_id: str
    agent_config: Any
    agent_kind: str
    hermes_session_id: str | None
    session_db: Any
    grounding: list[str]
    conversation_state: dict[str, Any]
    reply_delivered: bool
    customer_text: str = ""
    reply_text: str = ""
    intelligence_payloads: list[dict[str, Any]] = field(default_factory=list)
    handoff_kinds: list[str] = field(default_factory=list)


class RecordsQueue:
    """A bounded FIFO of RecordsJobs. Full => drop the OLDEST (the newest record
    supersedes prior ones for a conversation) and count it — never silently, never
    blocking the caller."""

    def __init__(self, *, maxsize: int = RECORDS_QUEUE_MAXSIZE) -> None:
        self._queue: asyncio.Queue[RecordsJob] = asyncio.Queue(maxsize=maxsize)
        self._dropped = 0

    def enqueue(self, job: RecordsJob) -> bool:
        """Non-blocking. Returns True if accepted without a drop, False if a drop
        was needed to make room."""
        try:
            self._queue.put_nowait(job)
            return True
        except asyncio.QueueFull:
            try:
                dropped = self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                dropped = None
            self._dropped += 1
            logger.warning(
                "records_queue_full_dropped_oldest dropped_total=%d dropped_conv=%s",
                self._dropped,
                getattr(dropped, "conversation_id", None),
            )
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(job)
            return False

    async def get(self) -> RecordsJob:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    @property
    def dropped_count(self) -> int:
        return self._dropped


class RecordsConsumer:
    """Supervised pool draining the records queue off the reply path. Implements the
    supervisor's Consumer protocol (start/stop/set_heartbeat_callback)."""

    def __init__(
        self,
        *,
        queue: RecordsQueue,
        pool_size: int = RECORDS_CONSUMER_POOL_SIZE,
        run_records: Callable[..., Any] | None = None,
    ) -> None:
        self._queue = queue
        self._pool_size = max(1, pool_size)
        self._run_records = run_records
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._stop_event: asyncio.Event | None = None
        self._heartbeat_callback: Callable[[], None] | None = None

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()

    async def _invoke(self, job: RecordsJob) -> None:
        run = self._run_records
        if run is None:
            from app.modules.agent_runtime_v2.turn_consumers import run_records_pass

            run = run_records_pass
        # No db= — the records pass owns its own session (Task 1 / S4).
        await run(
            workspace_id=job.workspace_id,
            conversation_id=job.conversation_id,
            customer_id=job.customer_id,
            agent_id=job.agent_id,
            agent_session_id=job.agent_session_id,
            hermes_run_id=job.hermes_run_id,
            agent_config=job.agent_config,
            agent_kind=job.agent_kind,
            hermes_session_id=job.hermes_session_id,
            session_db=job.session_db,
            grounding=job.grounding,
            conversation_state=job.conversation_state,
            reply_delivered=job.reply_delivered,
            customer_text=job.customer_text,
            reply_text=job.reply_text,
            intelligence_payloads=job.intelligence_payloads,
            handoff_kinds=job.handoff_kinds,
        )

    async def _drain_one(self) -> None:
        """Drain exactly one job and run the records pass. Non-fatal: a failing job
        is logged and the queue slot is still released, so one bad turn never wedges
        the pool. Test seam: enqueue then await this directly."""
        job = await self._queue.get()
        try:
            await self._invoke(job)
        except Exception:
            logger.exception("records consumer job failed (non-fatal)")
        finally:
            self._queue.task_done()
            self._beat()

    async def _worker_loop(self) -> None:
        while self._running:
            self._beat()
            try:
                job = await asyncio.wait_for(
                    self._queue.get(), timeout=_IDLE_BEAT_SECONDS
                )
            except TimeoutError:
                continue  # idle tick; loop to beat again
            except asyncio.CancelledError:
                raise
            try:
                await self._invoke(job)
            except Exception:
                logger.exception("records consumer job failed (non-fatal)")
            finally:
                self._queue.task_done()
                self._beat()

    async def start(self) -> None:
        self._running = True
        self._beat()
        self._stop_event = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self._worker_loop(), name=f"records-consumer-{i}")
            for i in range(self._pool_size)
        ]
        logger.info("RecordsConsumer started pool_size=%d", self._pool_size)
        await self._stop_event.wait()  # block until stop() (supervisor treats return as crash)

    async def stop(self) -> None:
        self._running = False
        if self._stop_event is not None:
            self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        logger.info("RecordsConsumer stopped")


# --- process-singleton: the dispatcher enqueues here; main.py wires the consumer
# to the SAME instance. Mirrors active_turn_run_registry's process-global pattern.

_RECORDS_QUEUE: RecordsQueue | None = None


def get_records_queue() -> RecordsQueue:
    global _RECORDS_QUEUE
    if _RECORDS_QUEUE is None:
        _RECORDS_QUEUE = RecordsQueue()
    return _RECORDS_QUEUE


def set_records_queue(queue: RecordsQueue | None) -> None:
    global _RECORDS_QUEUE
    _RECORDS_QUEUE = queue


def enqueue_records_job(job: RecordsJob) -> bool:
    return get_records_queue().enqueue(job)
