from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services import telegram_sidecar_health
from app.services.telegram_sidecar_health import load_telegram_sidecar_status


class _FakeAsyncClient:
    response: httpx.Response
    calls: list[dict]

    def __init__(self, *, timeout: float):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return None

    async def get(self, url: str, *, headers: dict):
        self.__class__.calls.append({
            "url": url,
            "headers": headers,
            "timeout": self.timeout,
        })
        return self.__class__.response


@pytest.mark.asyncio
async def test_load_telegram_sidecar_status_uses_workspace_scoped_status(monkeypatch):
    _FakeAsyncClient.response = httpx.Response(
        200,
        json={
            "workspaceId": 7,
            "state": "connected",
            "userId": "111",
            "phone": "+998900000000",
            "queueSize": 2,
            "reconnectAttempts": 1,
        },
    )
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(telegram_sidecar_health.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        telegram_sidecar_health,
        "get_settings",
        lambda: SimpleNamespace(sidecar_url="http://sidecar", sidecar_api_key="secret"),
    )

    status = await load_telegram_sidecar_status(workspace_id=7)

    assert status.state == "connected"
    assert status.workspace_id == 7
    assert status.user_id == "111"
    assert status.queue_size == 2
    assert _FakeAsyncClient.calls == [{
        "url": "http://sidecar/status?workspaceId=7",
        "headers": {"X-Sidecar-Key": "secret"},
        "timeout": 2.0,
    }]


@pytest.mark.asyncio
async def test_load_telegram_sidecar_status_projects_http_failure(monkeypatch):
    _FakeAsyncClient.response = httpx.Response(503, json={"error": "down"})
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(telegram_sidecar_health.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        telegram_sidecar_health,
        "get_settings",
        lambda: SimpleNamespace(sidecar_url="http://sidecar", sidecar_api_key=""),
    )

    status = await load_telegram_sidecar_status(workspace_id=7)

    assert status.state == "failed"
    assert status.last_error == "sidecar_http_503"
    assert status.user_id is None
