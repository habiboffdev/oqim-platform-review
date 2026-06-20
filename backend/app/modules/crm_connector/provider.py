"""Provider-neutral CRM adapter interface.

The whole connector — OAuth routes, sync worker, token refresher, read tool —
is written against this ABC. A second CRM (Bitrix24/HubSpot) is one new subclass
selected by ``CrmConnection.provider``; nothing above this seam changes.

Auth methods are abstract (every provider must implement them). The write/read
methods carry a ``NotImplementedError`` default so a provider can be built up
incrementally and stay instantiable; concrete providers override them.

``conn`` is duck-typed: any object exposing ``provider_account_ref`` and
``access_token`` (the ``CrmConnection`` ORM row at runtime). Keeping the ORM out
of the signature avoids coupling the seam to the persistence layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.modules.crm_connector.contracts import (
    CrmAccountSchema,
    CrmConnectionBootstrap,
    CrmContactInput,
    CrmContactSnapshot,
    CrmLeadInput,
    CrmLeadSnapshot,
    CrmOAuthCallback,
    CrmPipeline,
    CrmTokens,
    CrmWebhookBatch,
)


class CrmProvider(ABC):
    # --- auth (shared connection layer + token refresher call these) ---
    @abstractmethod
    def oauth_authorize_url(self, *, state: str, redirect_uri: str) -> str: ...

    @abstractmethod
    async def oauth_exchange(self, callback: CrmOAuthCallback) -> CrmConnectionBootstrap: ...

    @abstractmethod
    async def refresh(self, conn: Any) -> CrmTokens: ...

    # --- capability declaration (graceful degradation: a missing capability
    # falls back to an owner card, never a crash) ---
    def capabilities(self) -> frozenset[str]:
        """The feature set this provider supports. Default: empty — a provider
        declares what it implements."""
        return frozenset()

    # --- reads / writes (CrmSyncWorker + read tool); overridden by Task 4+ ---
    async def fetch_pipelines(self, conn: Any) -> list[CrmPipeline]:
        raise NotImplementedError

    async def discover_schema(self, conn: Any) -> CrmAccountSchema:
        raise NotImplementedError

    async def set_custom_fields(
        self, conn: Any, entity_id: str, fields: dict[str, Any], *, entity: str = "leads"
    ) -> str:
        """Write custom field values (provider field id -> value) on ``entity``
        ("leads" or "contacts", S4b). Returns ``"ok"`` or ``"unsupported"`` when
        the provider has no custom-field write. Default: unsupported (never raises)."""
        return "unsupported"

    async def fetch_contact_custom_fields(self, conn: Any, contact_id: str) -> dict:
        """Read a contact's custom field values as {field_id_str: value} (S4b DNC
        inbound). Default: empty (graceful)."""
        return {}

    async def find_contact_by_phone(self, conn: Any, phone: str) -> str | None:
        raise NotImplementedError

    async def create_contact(self, conn: Any, contact: CrmContactInput) -> str:
        raise NotImplementedError

    async def update_contact_phone(self, conn: Any, contact_id: str, phone: str) -> None:
        raise NotImplementedError

    async def create_lead(self, conn: Any, lead: CrmLeadInput) -> str:
        raise NotImplementedError

    async def update_lead_stage(
        self, conn: Any, lead_id: str, *, role: str, config: dict
    ) -> None:
        raise NotImplementedError

    async def set_lead_value(
        self, conn: Any, lead_id: str, *, amount: Decimal, currency: str = "UZS"
    ) -> None:
        raise NotImplementedError

    async def add_note(self, conn: Any, lead_id: str, text: str) -> None:
        raise NotImplementedError

    async def create_followup_task(
        self, conn: Any, lead_id: str, *, text: str, due_at: datetime,
        owner_ref: str | None = None,
    ) -> str:
        raise NotImplementedError

    async def add_tags(self, conn: Any, lead_id: str, names: list[str]) -> None:
        raise NotImplementedError

    async def fetch_lead(
        self, conn: Any, lead_id: str, *, include_notes: bool = False
    ) -> CrmLeadSnapshot:
        raise NotImplementedError

    async def fetch_contacts(self, conn: Any, *, page: int) -> list[CrmContactSnapshot]:
        raise NotImplementedError

    async def fetch_leads_by_stage(
        self, conn: Any, *, pipeline_id: str, status_ids: list[str], page: int
    ) -> list[CrmContactSnapshot]:
        raise NotImplementedError

    async def fetch_contacts_by_ids(
        self, conn: Any, *, contact_ids: list[str]
    ) -> list[CrmContactSnapshot]:
        raise NotImplementedError

    async def fetch_last_contact_note(self, conn: Any, *, contact_id: str) -> str | None:
        raise NotImplementedError

    async def add_contact_note(self, conn: Any, *, contact_id: str, text: str) -> None:
        raise NotImplementedError

    # --- webhooks (Slice B: two-way) ---
    async def register_webhook(self, conn: Any, *, destination: str, events: list[str]) -> str:
        raise NotImplementedError

    def parse_webhook(self, form: dict[str, str]) -> CrmWebhookBatch:
        raise NotImplementedError
