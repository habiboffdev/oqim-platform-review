"""Row-locked, single-use-safe amoCRM token refresh.

amoCRM rotates the refresh token on EVERY refresh and invalidates the old one.
Two concurrent refreshes (the refresher worker + a 401-retry on the sync worker)
would permanently brick the connection. So every refresh: lock the row, re-check
under the lock (someone may have already rotated), refresh, and **commit
immediately** — a consumed single-use rotation must never be rolled back by a
later unrelated failure in the same session.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.crm_connection import CrmConnection
from app.modules.crm_connector.contracts import CrmAuthError

# Refresh when the access token expires within this window.
REFRESH_WINDOW = timedelta(hours=2)


async def refresh_connection_locked(
    session: AsyncSession,
    *,
    connection_id: int,
    provider: Any,
) -> None:
    """Lock the connection row, rotate tokens if due, commit immediately."""
    result = await session.execute(
        select(CrmConnection)
        .where(CrmConnection.id == connection_id)
        .with_for_update()
    )
    conn = result.scalar_one_or_none()
    if conn is None or conn.status != "active":
        return
    # Under-lock re-check: a concurrent path may already have rotated.
    if (
        conn.token_expires_at is not None
        and conn.token_expires_at > utc_now() + REFRESH_WINDOW
    ):
        return
    try:
        tokens = await provider.refresh(conn)
    except CrmAuthError:
        conn.status = "degraded"
        conn.last_error = "amocrm refresh failed (auth-dead) — reconnect required"
        await session.commit()
        raise
    conn.access_token = tokens.access_token
    conn.refresh_token = tokens.refresh_token
    conn.token_expires_at = tokens.expires_at
    await session.commit()
