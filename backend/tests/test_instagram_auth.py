"""Instagram OAuth connect flow."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.routes.instagram_auth import (
    _clear_instagram_connection,
    _state_for,
    _state_signature,
    _workspace_id_from_state,
    exchange_and_store_instagram_token,
)
from app.core.config import get_settings
from tests.conftest import make_token

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _instagram_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "instagram_app_id", "1234567890")
    monkeypatch.setattr(settings, "instagram_app_secret", "test-ig-app-secret")


def _client_factory(responses: list[MagicMock], calls: list | None = None):
    """Sequential mock httpx client: each call pops the next response.

    Pass ``calls`` to record each (args, kwargs) pair in request order.
    """
    queue = list(responses)

    async def _next(*args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        return queue.pop(0)

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.post = AsyncMock(side_effect=_next)
        client.get = AsyncMock(side_effect=_next)
        yield client

    return _client


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


async def test_state_roundtrip_validates_workspace():
    state = _state_for(42)
    assert state.startswith("42.")
    assert _workspace_id_from_state(state) == 42
    # tampered workspace id fails
    assert _workspace_id_from_state("43." + state.split(".", 1)[1]) is None
    # garbage state fails
    assert _workspace_id_from_state("x.y.z") is None
    # expired state fails even with a valid signature
    past = int(time.time()) - 60
    expired = f"42.{past}.somenonce.{_state_signature(42, past, 'somenonce')}"
    assert _workspace_id_from_state(expired) is None
    # nonce makes every issued state unique
    assert _state_for(42) != _state_for(42)


async def test_exchange_and_store_persists_connection(db_session, workspace):
    responses = [
        _response({"access_token": "SHORT-token", "user_id": 999}),          # short-lived
        _response({"access_token": "IGAA-LONG-token", "expires_in": 5_184_000}),  # long-lived
        _response(  # /me — Instagram-Login exposes both ids; webhook entry.id may be either
            {"user_id": "17841400000000000", "id": "27100000000000000", "username": "testshop"}
        ),
        _response({"success": True}),                                         # subscribed_apps
    ]
    calls: list = []
    await exchange_and_store_instagram_token(
        db_session,
        workspace=workspace,
        code="auth-code-1",
        http_client_factory=_client_factory(responses, calls),
    )
    assert workspace.instagram_connected is True
    assert workspace.instagram_access_token == "IGAA-LONG-token"
    assert workspace.instagram_page_id == "17841400000000000"
    # Both ids stored so the webhook resolver matches whichever Meta sends as entry.id.
    assert workspace.instagram_account_id == "27100000000000000"
    assert workspace.instagram_token_expires_at is not None

    # /me and subscribed_apps carry the token in the Authorization header,
    # never in query params (query strings land in proxy/server logs).
    _, me_kwargs = calls[2]
    assert me_kwargs["headers"] == {"Authorization": "Bearer IGAA-LONG-token"}
    assert "access_token" not in me_kwargs["params"]
    # /me must request the `id` field too (not just user_id), else we can't store it.
    assert "id" in me_kwargs["params"]["fields"]
    _, subscribe_kwargs = calls[3]
    assert subscribe_kwargs["headers"] == {"Authorization": "Bearer IGAA-LONG-token"}
    assert "access_token" not in subscribe_kwargs["params"]


async def test_exchange_rejects_page_id_already_connected_elsewhere(db_session, workspace, workspace_b):
    """Two workspaces must never share one IG account (webhook routing would be ambiguous)."""
    workspace_b.instagram_connected = True
    workspace_b.instagram_page_id = "17841400000000000"
    await db_session.flush()

    responses = [
        _response({"access_token": "SHORT-token", "user_id": 999}),
        _response({"access_token": "IGAA-LONG-token", "expires_in": 5_184_000}),
        _response({"user_id": "17841400000000000", "username": "testshop"}),
    ]

    with pytest.raises(HTTPException) as exc_info:
        await exchange_and_store_instagram_token(
            db_session,
            workspace=workspace,
            code="auth-code-1",
            http_client_factory=_client_factory(responses),
        )
    assert exc_info.value.status_code == 409
    assert workspace.instagram_connected is False


async def test_disconnect_frees_page_id_for_reconnection(db_session, workspace, workspace_b):
    """After A disconnects, the same IG account can connect to B (and webhooks
    can never route to the disconnected workspace)."""
    workspace.instagram_connected = True
    workspace.instagram_page_id = "17841400000000000"
    workspace.instagram_access_token = "IGAA-A"
    await db_session.flush()

    # Same mutation the /disconnect endpoint applies.
    _clear_instagram_connection(workspace)
    await db_session.flush()
    assert workspace.instagram_page_id is None

    responses = [
        _response({"access_token": "SHORT-token", "user_id": 999}),
        _response({"access_token": "IGAA-LONG-token", "expires_in": 5_184_000}),
        _response({"user_id": "17841400000000000", "username": "testshop"}),
        _response({"success": True}),
    ]
    await exchange_and_store_instagram_token(
        db_session,
        workspace=workspace_b,
        code="auth-code-2",
        http_client_factory=_client_factory(responses),
    )
    assert workspace_b.instagram_page_id == "17841400000000000"
    assert workspace_b.instagram_connected is True


async def test_exchange_rejects_missing_account_id(db_session, workspace):
    """A /me response without user_id must 502 — never persist an empty
    page_id (the webhook resolver matches on instagram_page_id, so an empty
    one would match malformed webhook entries)."""
    responses = [
        _response({"access_token": "SHORT-token", "user_id": 999}),
        _response({"access_token": "IGAA-LONG-token", "expires_in": 5_184_000}),
        _response({"username": "testshop"}),  # /me without user_id
    ]

    with pytest.raises(HTTPException) as exc_info:
        await exchange_and_store_instagram_token(
            db_session,
            workspace=workspace,
            code="auth-code-1",
            http_client_factory=_client_factory(responses),
        )
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "instagram_account_id_missing"
    assert workspace.instagram_connected is False
    assert workspace.instagram_page_id is None
    assert workspace.instagram_access_token is None


# ---------------------------------------------------------------------------
# Callback endpoint proof (session binding, state validation, failure paths)
# ---------------------------------------------------------------------------

_CALLBACK_PATH = "/api/instagram/auth/callback"


def _bearer(workspace_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(workspace_id)}"}


async def test_callback_rejects_bad_state(app_with_fake_spine, workspace):
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _CALLBACK_PATH,
            params={"code": "x", "state": "garbage"},
            headers=_bearer(workspace.id),
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "invalid_state"}


async def test_callback_rejects_session_workspace_mismatch(
    app_with_fake_spine, workspace, workspace_b
):
    """Valid state for workspace A completed by a browser logged into
    workspace B must be rejected (account-binding CSRF)."""
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _CALLBACK_PATH,
            params={"code": "x", "state": _state_for(workspace.id)},
            headers=_bearer(workspace_b.id),
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "state_session_mismatch"}

    # Unauthenticated browser is rejected too.
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _CALLBACK_PATH,
            params={"code": "x", "state": _state_for(workspace.id)},
        )
    assert response.status_code == 403
    assert workspace.instagram_connected is False


async def test_callback_exchange_failure_leaves_workspace_disconnected(
    app_with_fake_spine, workspace, monkeypatch
):
    app, _ = app_with_fake_spine

    async def _boom(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(
        "app.api.routes.instagram_auth.exchange_and_store_instagram_token", _boom
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _CALLBACK_PATH,
            params={"code": "auth-code-1", "state": _state_for(workspace.id)},
            headers=_bearer(workspace.id),
        )
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/integrations?instagram=error"
    assert workspace.instagram_connected is False
    assert workspace.instagram_page_id is None
