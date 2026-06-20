"""Refresh near-expiry amoCRM access tokens before they lapse.

Supervisor worker, same start/stop/heartbeat contract as the Instagram
refresher. amoCRM access tokens are short-lived (~24h) and the refresh token is
single-use (rotated on every refresh), so ALL rotation goes through
``refresh_connection_locked`` (row lock + under-lock re-check + immediate
commit) — the refresher and a sync-worker 401 retry can never double-spend one
single-use refresh token. An auth-dead refresh degrades the connection and
surfaces an idempotent owner 'reconnect amoCRM' card; one failing connection
never blocks the others.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.crm_connection import CrmConnection
from app.modules.crm_connector.contracts import CrmAuthError
from app.modules.crm_connector.factory import provider_for
from app.modules.crm_connector.owner_cards import queue_crm_owner_notification
from app.modules.crm_connector.provider import CrmProvider
from app.modules.crm_connector.token_refresh import (
    REFRESH_WINDOW,
    refresh_connection_locked,
)

logger = get_logger("crm.token_refresher")

_TICK_INTERVAL_SECONDS = 10 * 60  # access tokens live ~24h; 6 checks/hour is plenty


class CrmTokenRefresher:
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
                    refreshed = await self.refresh_due_tokens(session)
                self._beat()
                if refreshed:
                    logger.info("crm_token_refresher.refreshed", extra={"count": refreshed})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("crm_token_refresher.tick_failed", exc_info=exc)
            slept = 0.0
            while not self._stopping and slept < self._interval:
                await asyncio.sleep(min(30.0, self._interval - slept))
                slept += 30.0
                self._beat()

    async def stop(self) -> None:
        self._stopping = True

    async def refresh_due_tokens(self, session: AsyncSession) -> int:
        now = datetime.now(UTC)
        due = (
            await session.execute(
                select(CrmConnection)
                .where(
                    CrmConnection.status == "active",
                    CrmConnection.token_expires_at.is_not(None),
                    CrmConnection.token_expires_at <= now + REFRESH_WINDOW,
                )
                .order_by(CrmConnection.id.asc())
            )
        ).scalars().all()

        refreshed = 0
        for conn in due:
            self._beat()
            provider = self._provider_factory(conn.provider)
            before = conn.token_expires_at
            try:
                await refresh_connection_locked(
                    session, connection_id=conn.id, provider=provider
                )
            except CrmAuthError:
                # refresh_connection_locked already set status=degraded + committed.
                await self._queue_reconnect_card(session, conn, now)
                continue
            except Exception as exc:
                # transient (network/5xx): leave the connection for the next tick.
                logger.warning(
                    "crm_token_refresher.refresh_failed workspace=%s error=%s",
                    conn.workspace_id,
                    type(exc).__name__,
                )
                continue
            if conn.token_expires_at != before:  # a real rotation (not an under-lock skip)
                refreshed += 1
        return refreshed

    async def _queue_reconnect_card(
        self, session: AsyncSession, conn: Any, now: datetime
    ) -> None:
        # Guarded: a card failure must never abort the loop nor roll back the
        # other connections already rotated this tick.
        try:
            await queue_crm_owner_notification(
                session,
                workspace_id=conn.workspace_id,
                title="amoCRM qayta ulash kerak",
                summary="amoCRM ruxsati yangilanmadi — token bekor qilingan yoki muddati tugagan.",
                recommended_action="Integratsiyalar > amoCRM bo'limida 'Qayta ulash' tugmasini bosing.",
                idempotency_key=f"crm_token_refresh_failed:{conn.workspace_id}:{now.strftime('%Y%m%d')}",
            )
        except Exception as card_exc:
            logger.error(
                "crm_token_refresher.card_failed workspace=%s error=%s",
                conn.workspace_id,
                type(card_exc).__name__,
            )
