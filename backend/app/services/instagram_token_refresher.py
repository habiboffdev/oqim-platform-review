"""Refresh Instagram long-lived tokens (60-day lifetime) before expiry.

Supervisor worker: same start/stop/heartbeat contract as BrainIndexReconciler.
Refresh window: anything expiring within 10 days. Failures queue an owner
'reconnect Instagram' card (idempotent per workspace per day) — never silent.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.workspace import Workspace
from app.services.instagram_messaging_policy import queue_instagram_owner_notification

logger = get_logger("services.instagram_token_refresher")

REFRESH_WINDOW = timedelta(days=10)
_TICK_INTERVAL_SECONDS = 6 * 60 * 60  # tokens live 60 days; 4 checks/day is plenty


class InstagramTokenRefresher:
    def __init__(
        self,
        *,
        db_factory: Callable[[], AsyncSession] | None,
        http_client_factory: Callable[..., Any] = httpx.AsyncClient,
        interval_seconds: float = _TICK_INTERVAL_SECONDS,
    ) -> None:
        self._db_factory = db_factory
        self._http_client_factory = http_client_factory
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
                    await session.commit()
                self._beat()
                if refreshed:
                    logger.info("instagram_token_refresher.refreshed", extra={"count": refreshed})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("instagram_token_refresher.tick_failed", exc_info=exc)
            # Heartbeat-friendly sleep in short slices so stop() is responsive.
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
                select(Workspace).where(
                    Workspace.instagram_connected.is_(True),
                    Workspace.instagram_access_token.is_not(None),
                    Workspace.instagram_token_expires_at.is_not(None),
                    Workspace.instagram_token_expires_at <= now + REFRESH_WINDOW,
                ).order_by(Workspace.id.asc())
            )
        ).scalars().all()
        refreshed = 0
        graph_base = get_settings().instagram_graph_base.rstrip("/")
        for workspace in due:
            self._beat()
            try:
                async with self._http_client_factory(timeout=20.0) as client:
                    # Meta's documented refresh interface takes the token as a
                    # query param (the token is the subject of the refresh).
                    response = await client.get(
                        f"{graph_base}/refresh_access_token",
                        params={
                            "grant_type": "ig_refresh_token",
                            "access_token": workspace.instagram_access_token,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                new_token = str(payload.get("access_token") or "")
                if not new_token:
                    raise ValueError("instagram refresh returned empty access_token")
                workspace.instagram_access_token = new_token
                expires_in = int(payload.get("expires_in") or 5_184_000)
                workspace.instagram_token_expires_at = now + timedelta(seconds=expires_in)
                refreshed += 1
            except Exception as exc:
                logger.warning(
                    "instagram token refresh failed workspace=%s error=%s",
                    workspace.id,
                    type(exc).__name__,
                )
                # Guarded: a failing card queue must never abort the loop and
                # roll back other workspaces' already-refreshed tokens.
                try:
                    await queue_instagram_owner_notification(
                        session,
                        workspace_id=workspace.id,
                        title="Instagram qayta ulash kerak",
                        summary="Instagram ruxsati yangilanmadi — token muddati tugayapti yoki bekor qilingan.",
                        recommended_action="Sozlamalar > Instagram bo'limida 'Qayta ulash' tugmasini bosing.",
                        idempotency_key=f"ig_token_refresh_failed:{workspace.id}:{now.strftime('%Y%m%d')}",
                    )
                except Exception as card_exc:
                    logger.error(
                        "instagram reconnect card queue failed workspace=%s error=%s",
                        workspace.id,
                        type(card_exc).__name__,
                    )
        await session.flush()
        return refreshed
