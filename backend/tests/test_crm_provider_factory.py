"""provider_for — the single closed CRM provider registry."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import get_settings
from app.modules.crm_connector.amocrm import AmoCrmProvider
from app.modules.crm_connector.contracts import CrmOAuthCallback
from app.modules.crm_connector.factory import provider_for

pytestmark = pytest.mark.asyncio


def _client_factory(responses: list):
    queue = list(responses)

    async def _next(*args, **kwargs):
        return queue.pop(0)

    @asynccontextmanager
    async def _client(*args, **kwargs):
        client = MagicMock()
        client.post = AsyncMock(side_effect=_next)
        yield client

    return _client


def _response(payload: dict):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def test_provider_for_amocrm_returns_amocrm_provider():
    assert isinstance(provider_for("amocrm"), AmoCrmProvider)


def test_provider_for_kommo_returns_amocrm_provider():
    # Kommo shares amoCRM's OAuth/API; it maps to the same adapter (S5 adds its host).
    assert isinstance(provider_for("kommo"), AmoCrmProvider)


def test_provider_for_unknown_raises():
    with pytest.raises(ValueError):
        provider_for("salesforce")


def test_provider_for_passes_http_client_factory():
    factory = _client_factory([])
    provider = provider_for("amocrm", http_client_factory=factory)
    assert provider._http_client_factory is factory


async def test_kommo_referer_host_is_accepted(monkeypatch):
    # The Kommo host-suffix smoke: a .kommo.com referer is whitelisted, so the
    # OAuth exchange resolves the subdomain instead of rejecting the host.
    s = get_settings()
    monkeypatch.setattr(s, "amocrm_client_id", "cid")
    monkeypatch.setattr(s, "amocrm_client_secret", "sec")
    provider = provider_for(
        "kommo",
        http_client_factory=_client_factory(
            [_response({"access_token": "a", "refresh_token": "r", "expires_in": 86400})]
        ),
    )
    boot = await provider.oauth_exchange(
        CrmOAuthCallback(code="c", raw_params={"referer": "mybiz.kommo.com"})
    )
    assert boot.provider_account_ref == "mybiz"
