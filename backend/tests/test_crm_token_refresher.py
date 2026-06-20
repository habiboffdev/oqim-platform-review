"""CrmTokenRefresher — rotate near-expiry amoCRM tokens, single-use-safe.

amoCRM access tokens are short-lived and the refresh token is single-use
(rotated every refresh). All rotation goes through the row-locked, under-lock
re-checked ``refresh_connection_locked`` so the refresher and a sync-worker 401
retry can never double-spend one refresh token. Auth-dead refreshes degrade the
connection and surface an idempotent owner card — other due connections still
refresh.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.models.crm_connection import CrmConnection
from app.modules.crm_connector.contracts import CrmAuthError, CrmTokens
from app.modules.crm_connector.token_refresher import CrmTokenRefresher

pytestmark = pytest.mark.asyncio


class _FakeRefreshProvider:
    """Only ``refresh`` is exercised (refresh_connection_locked duck-types it)."""

    def __init__(self, *, auth_dead: set[str] | None = None) -> None:
        self.refresh_calls = 0
        self.refreshed_refs: list[str] = []
        self.auth_dead = auth_dead or set()

    async def refresh(self, conn) -> CrmTokens:
        self.refresh_calls += 1
        self.refreshed_refs.append(conn.provider_account_ref)
        if conn.provider_account_ref in self.auth_dead:
            raise CrmAuthError("invalid_grant")
        return CrmTokens(
            access_token=f"new-access-{conn.provider_account_ref}",
            refresh_token=f"new-refresh-{conn.provider_account_ref}",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )


def _refresher(provider: _FakeRefreshProvider) -> CrmTokenRefresher:
    return CrmTokenRefresher(db_factory=None, provider_factory=lambda _name: provider)


async def _conn(
    db_session,
    workspace,
    *,
    account_ref="mybiz",
    webhook_token="wh-1",
    status="active",
    expires_in_minutes=30,
):
    conn = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status=status,
        provider_account_ref=account_ref,
        access_token="old-access",
        refresh_token="old-refresh",
        token_expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_minutes),
        webhook_token=webhook_token,
        pipeline_config={},
    )
    db_session.add(conn)
    await db_session.flush()
    return conn


async def _owner_cards(db_session, workspace_id):
    return (
        await db_session.execute(
            select(func.count())
            .select_from(BusinessBrainProjectionRecord)
            .where(
                BusinessBrainProjectionRecord.workspace_id == workspace_id,
                BusinessBrainProjectionRecord.projection_type == "owner_notification",
            )
        )
    ).scalar_one()


# --------------------------------------------------------------------------- #
async def test_refreshes_only_due_active_connections(db_session, workspace, workspace_b):
    due = await _conn(db_session, workspace, account_ref="biz-due", webhook_token="wh-due")
    # Same workspace, disconnected + due -> skipped (not active).
    dead = await _conn(
        db_session, workspace, account_ref="biz-dead", webhook_token="wh-dead",
        status="disconnected",
    )
    # Active but far from expiry -> skipped by the 2h window.
    fresh = await _conn(
        db_session, workspace_b, account_ref="biz-fresh", webhook_token="wh-fresh",
        expires_in_minutes=5 * 60,
    )

    provider = _FakeRefreshProvider()
    refreshed = await _refresher(provider).refresh_due_tokens(db_session)
    for c in (due, dead, fresh):
        await db_session.refresh(c)

    assert provider.refreshed_refs == ["biz-due"]  # only the due, active one
    assert refreshed == 1
    assert due.access_token == "new-access-biz-due"
    assert due.refresh_token == "new-refresh-biz-due"  # BOTH tokens rotated
    assert due.token_expires_at > datetime.now(UTC) + timedelta(hours=20)
    assert dead.access_token == "old-access"
    assert fresh.access_token == "old-access"


async def test_under_lock_recheck_skips_already_rotated(db_session, workspace, monkeypatch):
    """Another path rotates the token between scan and lock -> the real
    under-lock re-check (inside refresh_connection_locked) skips the provider."""
    await _conn(db_session, workspace, account_ref="biz-race", webhook_token="wh-race")
    provider = _FakeRefreshProvider()

    from app.modules.crm_connector import token_refresher as mod

    real_locked = mod.refresh_connection_locked

    async def _racing_locked(session, *, connection_id, provider):
        # Simulate the concurrent refresh: bump expiry past the window, then run
        # the REAL locked refresh — its re-read must now see a fresh token.
        c = await session.get(CrmConnection, connection_id)
        c.token_expires_at = datetime.now(UTC) + timedelta(hours=5)
        await session.flush()
        return await real_locked(session, connection_id=connection_id, provider=provider)

    monkeypatch.setattr(mod, "refresh_connection_locked", _racing_locked)

    await _refresher(provider).refresh_due_tokens(db_session)
    assert provider.refresh_calls == 0  # single-use token never double-spent


async def test_auth_dead_degrades_and_cards_without_blocking_others(
    db_session, workspace, workspace_b
):
    dead = await _conn(
        db_session, workspace, account_ref="auth-dead", webhook_token="wh-a"
    )
    good = await _conn(
        db_session, workspace_b, account_ref="biz-good", webhook_token="wh-b"
    )

    provider = _FakeRefreshProvider(auth_dead={"auth-dead"})
    await _refresher(provider).refresh_due_tokens(db_session)
    await db_session.refresh(dead)
    await db_session.refresh(good)

    assert dead.status == "degraded"
    assert await _owner_cards(db_session, workspace.id) == 1
    assert good.access_token == "new-access-biz-good"  # the loop kept going
    assert await _owner_cards(db_session, workspace_b.id) == 0


async def test_auth_dead_card_is_idempotent_per_workspace_day(db_session, workspace):
    await _conn(db_session, workspace, account_ref="auth-dead", webhook_token="wh-a")
    provider = _FakeRefreshProvider(auth_dead={"auth-dead"})

    await _refresher(provider).refresh_due_tokens(db_session)
    # Re-arm to active + due, fail again the same day -> still exactly one card.
    conn = (
        await db_session.execute(select(CrmConnection))
    ).scalars().first()
    conn.status = "active"
    conn.token_expires_at = datetime.now(UTC) + timedelta(minutes=30)
    await db_session.flush()
    await _refresher(provider).refresh_due_tokens(db_session)

    assert await _owner_cards(db_session, workspace.id) == 1
