"""Instagram token refresher: refresh near-expiry tokens; surface failures."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import select

from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.services.instagram_token_refresher import InstagramTokenRefresher

pytestmark = pytest.mark.asyncio


def _client_factory(responses: list[MagicMock] | MagicMock, calls: list | None = None):
    """Sequential mock httpx client: each GET pops the next response, across
    client instantiations (the worker opens a fresh client per workspace).

    Pass ``calls`` to record each (args, kwargs) pair in request order.
    """
    queue = list(responses) if isinstance(responses, list) else [responses]

    async def _next(*args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        return queue.pop(0)

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.get = AsyncMock(side_effect=_next)
        yield client

    return _client


def _ok_response(token: str, expires_in: int = 5_184_000) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"access_token": token, "expires_in": expires_in}
    response.raise_for_status.return_value = None
    return response


def _error_response(status_code: int = 400) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"error": {"message": "revoked"}}
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        str(status_code), request=MagicMock(), response=response
    )
    return response


async def _owner_notification(db_session, workspace_id: int):
    return (
        await db_session.execute(
            select(BusinessBrainProjectionRecord).where(
                BusinessBrainProjectionRecord.workspace_id == workspace_id,
                BusinessBrainProjectionRecord.projection_type == "owner_notification",
            )
        )
    ).scalars().first()


async def test_refreshes_token_expiring_soon(db_session, workspace):
    workspace.instagram_connected = True
    workspace.instagram_access_token = "IGAA-OLD"
    workspace.instagram_token_expires_at = datetime.now(UTC) + timedelta(days=3)
    await db_session.flush()

    calls: list = []
    refresher = InstagramTokenRefresher(
        db_factory=None,
        http_client_factory=_client_factory(_ok_response("IGAA-NEW"), calls=calls),
    )
    refreshed = await refresher.refresh_due_tokens(db_session)

    assert refreshed == 1
    assert workspace.instagram_access_token == "IGAA-NEW"
    expires_at = workspace.instagram_token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    assert expires_at > datetime.now(UTC) + timedelta(days=50)

    # The request hits Meta's documented refresh interface.
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0].endswith("/refresh_access_token")
    assert kwargs["params"]["grant_type"] == "ig_refresh_token"
    assert kwargs["params"]["access_token"] == "IGAA-OLD"


async def test_skips_token_with_plenty_of_time(db_session, workspace):
    workspace.instagram_connected = True
    workspace.instagram_access_token = "IGAA-FRESH"
    workspace.instagram_token_expires_at = datetime.now(UTC) + timedelta(days=45)
    await db_session.flush()

    refresher = InstagramTokenRefresher(db_factory=None)
    refreshed = await refresher.refresh_due_tokens(db_session)
    assert refreshed == 0
    assert workspace.instagram_access_token == "IGAA-FRESH"


async def test_refresh_failure_queues_reconnect_owner_card(db_session, workspace):
    workspace.instagram_connected = True
    workspace.instagram_access_token = "IGAA-REVOKED"
    workspace.instagram_token_expires_at = datetime.now(UTC) + timedelta(days=2)
    await db_session.flush()

    refresher = InstagramTokenRefresher(
        db_factory=None, http_client_factory=_client_factory(_error_response())
    )
    refreshed = await refresher.refresh_due_tokens(db_session)
    assert refreshed == 0

    projection = await _owner_notification(db_session, workspace.id)
    assert projection is not None


async def test_failure_in_one_workspace_does_not_block_another(
    db_session, workspace, workspace_b
):
    # Worker processes in Workspace.id order: workspace (first) fails,
    # workspace_b (second) succeeds.
    for ws, token in ((workspace, "IGAA-A-OLD"), (workspace_b, "IGAA-B-OLD")):
        ws.instagram_connected = True
        ws.instagram_access_token = token
        ws.instagram_token_expires_at = datetime.now(UTC) + timedelta(days=2)
    await db_session.flush()
    assert workspace.id < workspace_b.id

    refresher = InstagramTokenRefresher(
        db_factory=None,
        http_client_factory=_client_factory(
            [_error_response(), _ok_response("IGAA-B-NEW")]
        ),
    )
    refreshed = await refresher.refresh_due_tokens(db_session)

    assert refreshed == 1
    assert workspace.instagram_access_token == "IGAA-A-OLD"
    assert workspace_b.instagram_access_token == "IGAA-B-NEW"
    assert await _owner_notification(db_session, workspace.id) is not None
    assert await _owner_notification(db_session, workspace_b.id) is None


async def test_empty_refresh_response_token_treated_as_failure(db_session, workspace):
    workspace.instagram_connected = True
    workspace.instagram_access_token = "IGAA-OLD"
    workspace.instagram_token_expires_at = datetime.now(UTC) + timedelta(days=2)
    await db_session.flush()

    refresher = InstagramTokenRefresher(
        db_factory=None, http_client_factory=_client_factory(_ok_response(""))
    )
    refreshed = await refresher.refresh_due_tokens(db_session)

    assert refreshed == 0
    assert workspace.instagram_access_token == "IGAA-OLD"
    assert await _owner_notification(db_session, workspace.id) is not None
