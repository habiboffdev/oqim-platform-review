"""Provider factory — the single place that constructs a CrmProvider.

Closed registry keyed on ``CrmConnection.provider``. Adding a CRM is one entry
here plus the adapter subclass; nothing else above the seam constructs a
provider. Kommo shares amoCRM's OAuth/API surface (the ``.kommo.com`` host is
whitelisted inside ``AmoCrmProvider``), so it maps to ``AmoCrmProvider``; full
Kommo support (its base/token host is ``.kommo.com``, not ``.amocrm.ru``) is
deferred to S5.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

import httpx

from app.modules.crm_connector.amocrm import AmoCrmProvider
from app.modules.crm_connector.provider import CrmProvider

ProviderName = Literal["amocrm", "kommo"]

# Closed registry. bitrix24/hubspot land when a business signs on one (S5).
_PROVIDERS: dict[str, type[CrmProvider]] = {
    "amocrm": AmoCrmProvider,
    "kommo": AmoCrmProvider,
}


def provider_for(
    provider: str,
    *,
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> CrmProvider:
    """Construct the adapter for a provider key (``CrmConnection.provider``).

    Raises ``ValueError`` for an unknown provider — the same fail-closed behavior
    as the three per-worker factories this replaces.
    """
    cls = _PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(f"unknown CRM provider: {provider!r}")
    return cls(http_client_factory=http_client_factory)
