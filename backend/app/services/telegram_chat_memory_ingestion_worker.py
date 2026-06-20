from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.services.telegram_chat_memory_ingestion import TelegramChatMemoryIngestionService
from app.services.worker_lease import WorkerLease

logger = get_logger("services.telegram_chat_memory_ingestion_worker")

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_BATCH_SIZE = 100
WORKER_LEASE_ROLE = "telegram_chat_memory_ingestion"

WorkspaceIdsProvider = Callable[[], Awaitable[list[int]] | list[int]]


class TelegramChatMemoryIngestionWorker:
    """Supervised worker for durable sidecar raw-message projection."""

    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        workspace_ids_provider: WorkspaceIdsProvider,
        redis: Any | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        service: TelegramChatMemoryIngestionService | None = None,
    ) -> None:
        self._db_factory = db_factory
        self._workspace_ids_provider = workspace_ids_provider
        self._redis = redis
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.1)
        self._batch_size = max(1, int(batch_size))
        self._service = service or TelegramChatMemoryIngestionService()
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._lease = (
            WorkerLease(redis, role=WORKER_LEASE_ROLE, ttl_seconds=30)
            if redis is not None
            else None
        )

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._heartbeat_callback = callback

    async def start(self) -> None:
        self._stopping = False
        has_lease = False
        while not self._stopping:
            try:
                if self._lease is not None:
                    has_lease = (
                        await self._lease.renew()
                        if has_lease
                        else await self._lease.acquire()
                    )
                    if not has_lease:
                        self._beat()
                        await asyncio.sleep(self._poll_interval_seconds)
                        continue
                processed = await self.run_once()
                self._beat()
                if processed == 0:
                    await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                raise
            except Exception:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                has_lease = False
                logger.exception("telegram_chat_memory_ingestion_worker.tick_failed")
                await asyncio.sleep(min(self._poll_interval_seconds * 2, 10.0))
        if has_lease and self._lease is not None:
            await self._lease.release()

    async def stop(self) -> None:
        self._stopping = True

    async def run_once(self) -> int:
        workspace_ids = await self._load_workspace_ids()
        processed = 0
        for workspace_id in workspace_ids:
            self._beat()
            async with self._db_factory() as session:
                result = await self._service.ingest_due_raw_messages(
                    session=session,
                    workspace_id=int(workspace_id),
                    limit=self._batch_size,
                )
                processed += result.persisted
                if result.degraded_reason:
                    logger.info(
                        "telegram_chat_memory_ingestion_worker.degraded",
                        extra={
                            "workspace_id": workspace_id,
                            "degraded_reason": result.degraded_reason,
                        },
                    )
        return processed

    async def _load_workspace_ids(self) -> list[int]:
        value = self._workspace_ids_provider()
        if inspect.isawaitable(value):
            value = await value
        return [int(workspace_id) for workspace_id in value]

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()
