"""Periodically re-read each active connection's amoCRM schema (no reconnect) so
OQIM stays current as the owner changes pipelines/stages/fields. Same supervisor
contract as CrmTokenRefresher."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.crm_connection import CrmConnection
from app.modules.crm_connector.factory import provider_for
from app.modules.crm_connector.provider import CrmProvider
from app.modules.crm_connector.rediscovery import rediscover_connection

logger = get_logger("crm.schema_refresher")

_TICK_INTERVAL_SECONDS = 6 * 60 * 60  # schema changes are rare; 4 checks/day


class CrmSchemaRefresher:
    def __init__(
        self,
        *,
        db_factory: Callable[[], AsyncSession] | None,
        provider_factory: Callable[[str], CrmProvider] | None = None,
        interval_seconds: float = _TICK_INTERVAL_SECONDS,
    ) -> None:
        self._db_factory = db_factory
        self._provider_factory = provider_factory or provider_for
        self._interval = interval_seconds
        self._stopping = False
        self._beat: Callable[[], None] = lambda: None

    def set_heartbeat_callback(self, callback: Callable[[], None]) -> None:
        self._beat = callback or (lambda: None)

    async def start(self) -> None:
        assert self._db_factory is not None, "db_factory required to run the loop"
        self._stopping = False
        while not self._stopping:
            try:
                async with self._db_factory() as session:
                    changed = await self.refresh_due(session)
                self._beat()
                if changed:
                    logger.info("crm_schema_refresher.refreshed", extra={"count": changed})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("crm_schema_refresher.tick_failed", exc_info=exc)
            slept = 0.0
            while not self._stopping and slept < self._interval:
                await asyncio.sleep(min(60.0, self._interval - slept))
                slept += 60.0
                self._beat()

    async def stop(self) -> None:
        self._stopping = True

    async def refresh_due(self, session: AsyncSession) -> int:
        conns = (await session.execute(
            select(CrmConnection)
            .where(CrmConnection.status == "active")
            .order_by(CrmConnection.id.asc())
        )).scalars().all()
        changed = 0
        for conn in conns:
            self._beat()
            provider = self._provider_factory(conn.provider)
            try:
                if await rediscover_connection(session, conn, provider):
                    changed += 1
            except Exception as exc:
                # Best-effort, same contract as CrmTokenRefresher: a transient
                # (network/5xx) discovery failure for one connection is logged and
                # skipped this tick, never blocking the others. (rediscover_connection
                # commits per connection, so a successful conn's write is already
                # durable before a later one fails.)
                logger.warning("crm_schema_refresher.failed workspace=%s error=%s",
                               conn.workspace_id, type(exc).__name__)
                continue
        return changed
