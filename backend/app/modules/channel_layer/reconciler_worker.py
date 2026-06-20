"""Supervised worker that periodically reconciles 'unknown' deliveries.

Mirrors the ScheduledReplySender pattern: a poll loop (leader-elected via
WorkerLease when Redis is present) calling run_once(), which finds workspaces
with unknown deliveries and runs the DeliveryReconciler for each.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.models.delivery_runtime import DeliveryRuntime
from app.modules.channel_layer.reconciler import DeliveryReconciler
from app.services.delivery_runtime import DELIVERY_UNKNOWN
from app.services.worker_lease import WorkerLease

logger = logging.getLogger("oqim_business.delivery_reconciler_worker")


class DeliveryReconcilerWorker:
    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        redis: Any | None = None,
        poll_interval_seconds: float = 60.0,
    ) -> None:
        self._db_factory = db_factory
        self._redis = redis
        self._poll_interval_seconds = poll_interval_seconds
        self._stopping = False
        self._heartbeat_callback: Callable[[], None] | None = None
        self._lease = (
            WorkerLease(redis, role="delivery_reconciler", ttl_seconds=120)
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
                await self.run_once()
                self._beat()
                await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                raise
            except Exception as exc:
                if has_lease and self._lease is not None:
                    await self._lease.release()
                has_lease = False
                logger.error("delivery_reconciler.tick_failed", exc_info=exc)
                await asyncio.sleep(min(self._poll_interval_seconds * 2, 30.0))
        if has_lease and self._lease is not None:
            await self._lease.release()

    async def stop(self) -> None:
        self._stopping = True

    def _beat(self) -> None:
        if self._heartbeat_callback is not None:
            self._heartbeat_callback()

    async def run_once(self) -> int:
        async with self._db_factory() as session:
            workspace_ids = list(
                (
                    await session.scalars(
                        select(DeliveryRuntime.workspace_id)
                        .where(DeliveryRuntime.state == DELIVERY_UNKNOWN)
                        .group_by(DeliveryRuntime.workspace_id)
                    )
                ).all()
            )
            total = 0
            for workspace_id in workspace_ids:
                report = await DeliveryReconciler(session).reconcile(workspace_id=workspace_id)
                total += report.reconciled
            await session.commit()
            return total
