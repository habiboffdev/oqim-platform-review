"""amoCRM per-workspace OAuth connect flow."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.routes.amocrm_auth import (
    _state_for,
    _state_signature,
    _workspace_id_from_state,
    exchange_and_store_amocrm_connection,
)
from app.core.config import get_settings
from app.models.crm_connection import CrmConnection
from tests.conftest import make_token

pytestmark = pytest.mark.asyncio

_START_PATH = "/api/amocrm/auth/start"
_CALLBACK_PATH = "/api/amocrm/auth/callback"
_STATUS_PATH = "/api/amocrm/auth/status"
_DISCONNECT_PATH = "/api/amocrm/auth/disconnect"


@pytest.fixture(autouse=True)
def _amocrm_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "amocrm_client_id", "cid-123")
    monkeypatch.setattr(settings, "amocrm_client_secret", "secret-xyz")


def _bearer(workspace_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(workspace_id)}"}


def _client_factory(responses: list, calls: list | None = None):
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
        client.patch = AsyncMock(side_effect=_next)
        yield client

    return _client


def _response(payload: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


_TOKEN_RESPONSE = {"access_token": "acc", "refresh_token": "ref", "expires_in": 86400}
_PIPELINES_RESPONSE = {
    "_embedded": {
        "pipelines": [
            {
                "id": 111,
                "name": "Main",
                "is_main": True,
                "_embedded": {
                    "statuses": [
                        {"id": 201, "name": "First", "sort": 10, "type": 0},
                        {"id": 142, "name": "Won", "sort": 100, "type": 1},
                        {"id": 143, "name": "Lost", "sort": 110, "type": 0},
                    ]
                },
            }
        ]
    }
}
# S2: discover_schema now also reads custom fields / users / task types on connect.
_LEADS_CF_RESPONSE = {"_embedded": {"custom_fields": []}}
_CONTACTS_CF_RESPONSE = {"_embedded": {"custom_fields": []}}
_USERS_RESPONSE = {"_embedded": {"users": []}}
_TASK_TYPES_RESPONSE = {"_embedded": {"task_types": []}}


def _connect_responses() -> list:
    """The full mocked amoCRM response sequence for a connect: OAuth token, then
    the 5 discover_schema reads (pipelines + leads/contacts custom fields + users
    + task types). Fresh mocks per call so tests never share queue state."""
    return [
        _response(_TOKEN_RESPONSE),
        _response(_PIPELINES_RESPONSE),
        _response(_LEADS_CF_RESPONSE),
        _response(_CONTACTS_CF_RESPONSE),
        _response(_USERS_RESPONSE),
        _response(_TASK_TYPES_RESPONSE),
    ]


# --- state helpers --------------------------------------------------------------
async def test_state_roundtrip_validates_workspace():
    state = _state_for(42)
    assert state.startswith("42.")
    assert _workspace_id_from_state(state) == 42
    assert _workspace_id_from_state("43." + state.split(".", 1)[1]) is None
    assert _workspace_id_from_state("x.y.z") is None
    past = int(time.time()) - 60
    expired = f"42.{past}.nonce.{_state_signature(42, past, 'nonce')}"
    assert _workspace_id_from_state(expired) is None


# --- /start ---------------------------------------------------------------------
async def test_start_returns_authorize_url(app_with_fake_spine, workspace):
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_START_PATH, headers=_bearer(workspace.id))
    assert response.status_code == 200
    url = response.json()["authorize_url"]
    assert "cid-123" in url and "state=" in url


async def test_start_503_when_not_configured(app_with_fake_spine, workspace, monkeypatch):
    monkeypatch.setattr(get_settings(), "amocrm_client_id", "")
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_START_PATH, headers=_bearer(workspace.id))
    assert response.status_code == 503
    assert response.json()["detail"] == "amocrm_not_configured"


async def test_start_401_without_auth(app_with_fake_spine):
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_START_PATH)
    assert response.status_code == 401


# --- exchange (direct) ----------------------------------------------------------
async def test_exchange_persists_active_connection(db_session, workspace):
    await exchange_and_store_amocrm_connection(
        db_session,
        workspace=workspace,
        code="auth-code",
        referer="mybiz.amocrm.ru",
        http_client_factory=_client_factory(_connect_responses()),
    )
    conn = (
        await db_session.execute(
            select(CrmConnection).where(
                CrmConnection.workspace_id == workspace.id, CrmConnection.status == "active"
            )
        )
    ).scalars().first()
    assert conn is not None
    assert conn.provider_account_ref == "mybiz"
    assert conn.access_token == "acc"
    assert conn.refresh_token == "ref"
    assert conn.webhook_token  # fresh per-connection webhook secret
    assert conn.pipeline_config["mapping"]["default_pipeline_id"] == "111"
    assert (
        conn.pipeline_config["mapping"]["pipelines"]["111"]["role_map"]["new"]["stage_id"]
        == "201"
    )


async def test_exchange_409_when_account_active_in_other_workspace(
    db_session, workspace, workspace_b
):
    db_session.add(
        CrmConnection(
            workspace_id=workspace_b.id,
            provider="amocrm",
            status="active",
            provider_account_ref="mybiz",
            webhook_token="tok-b",
            pipeline_config={},
        )
    )
    await db_session.flush()
    with pytest.raises(HTTPException) as exc_info:
        await exchange_and_store_amocrm_connection(
            db_session,
            workspace=workspace,
            code="auth-code",
            referer="mybiz.amocrm.ru",
            http_client_factory=_client_factory(_connect_responses()),
        )
    # HTTPException(409) bubbles up; the route turns it into the already-connected redirect.
    assert getattr(exc_info.value, "status_code", None) == 409


# --- /callback ------------------------------------------------------------------
async def test_callback_rejects_bad_state(app_with_fake_spine, workspace):
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _CALLBACK_PATH, params={"code": "x", "state": "garbage"}, headers=_bearer(workspace.id)
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "invalid_state"


async def test_callback_rejects_session_mismatch(app_with_fake_spine, workspace, workspace_b):
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _CALLBACK_PATH,
            params={"code": "x", "state": _state_for(workspace.id)},
            headers=_bearer(workspace_b.id),
        )
    assert response.status_code == 403
    assert response.json()["detail"] == "state_session_mismatch"


async def test_callback_exchange_failure_redirects_error(
    app_with_fake_spine, workspace, monkeypatch
):
    async def _boom(*args, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(
        "app.api.routes.amocrm_auth.exchange_and_store_amocrm_connection", _boom
    )
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _CALLBACK_PATH,
            params={"code": "auth-code", "state": _state_for(workspace.id)},
            headers=_bearer(workspace.id),
        )
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/integrations?amocrm=error"


async def test_callback_unauthorized_during_discovery_redirects_error(
    app_with_fake_spine, workspace, monkeypatch
):
    # A 401 mid-discovery (CrmUnauthorizedError) must redirect to the error page,
    # not escape as an unhandled 500. S2 widened this from 1 read to 5.
    from app.modules.crm_connector.contracts import CrmUnauthorizedError

    async def _boom(*args, **kwargs):
        raise CrmUnauthorizedError("amocrm api 401")

    monkeypatch.setattr(
        "app.api.routes.amocrm_auth.exchange_and_store_amocrm_connection", _boom
    )
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _CALLBACK_PATH,
            params={"code": "auth-code", "state": _state_for(workspace.id)},
            headers=_bearer(workspace.id),
        )
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/integrations?amocrm=error"


async def test_callback_success_redirects_connected(app_with_fake_spine, workspace, monkeypatch):
    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.api.routes.amocrm_auth.exchange_and_store_amocrm_connection", _ok
    )
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _CALLBACK_PATH,
            params={"code": "auth-code", "state": _state_for(workspace.id)},
            headers=_bearer(workspace.id),
        )
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/integrations?amocrm=connected"


# --- /status + /disconnect ------------------------------------------------------
async def test_status_reflects_connection_with_isolation(
    app_with_fake_spine, db_session, workspace, workspace_b
):
    db_session.add(
        CrmConnection(
            workspace_id=workspace.id,
            provider="amocrm",
            status="active",
            provider_account_ref="mybiz",
            webhook_token="tok-x",
            pipeline_config={},
        )
    )
    await db_session.flush()
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mine = await client.get(_STATUS_PATH, headers=_bearer(workspace.id))
        theirs = await client.get(_STATUS_PATH, headers=_bearer(workspace_b.id))
    assert mine.json()["connected"] is True
    assert mine.json()["provider_account_ref"] == "mybiz"
    assert theirs.json()["connected"] is False  # workspace isolation


async def test_disconnect_sets_status_disconnected(
    app_with_fake_spine, db_session, workspace
):
    db_session.add(
        CrmConnection(
            workspace_id=workspace.id,
            provider="amocrm",
            status="active",
            provider_account_ref="mybiz",
            webhook_token="tok-y",
            pipeline_config={},
        )
    )
    await db_session.flush()
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_DISCONNECT_PATH, headers=_bearer(workspace.id))
    assert response.json() == {"status": "disconnected"}
