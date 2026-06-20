"""amoCRM provider — the first ``CrmProvider`` adapter.

Owns its own httpx client (CRM is not an LLM call — it does NOT go through
``app.brain``). Auth here; writes/reads land in Task 4. Bearer header for API
calls, never tokens in query params (proxy/server logs).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.base import utc_now
from app.modules.crm_connector.contracts import (
    CrmAccountSchema,
    CrmAuthError,
    CrmConnectionBootstrap,
    CrmContactInput,
    CrmContactSnapshot,
    CrmFieldDef,
    CrmFieldEnum,
    CrmLeadInput,
    CrmLeadSnapshot,
    CrmOAuthCallback,
    CrmPipeline,
    CrmPipelineStatus,
    CrmStageEvent,
    CrmTaskType,
    CrmTokens,
    CrmUnauthorizedError,
    CrmUser,
    CrmWebhookBatch,
)
from app.modules.crm_connector.provider import CrmProvider

logger = get_logger("crm.amocrm")

# amoCRM platform-constant terminal status ids (same across every account /
# pipeline — not account-specific, so safe to recognize by id).
_AMOCRM_WON_STATUS_ID = 142
_AMOCRM_LOST_STATUS_ID = 143
_AMOCRM_PAGE_LIMIT = 250
_AMOCRM_ID_FILTER_CHUNK = 50  # ids per filter[id][] request — keeps the URL sane


def _phone_from_contact(entry: dict) -> str | None:
    for field in entry.get("custom_fields_values") or []:
        if field.get("field_code") == "PHONE":
            values = field.get("values") or []
            if values:
                return str(values[0].get("value") or "") or None
    return None


def _coerce_price(raw: str | None) -> int | None:
    """amoCRM sends the lead price as an integer string of so'm. Ignore anything
    non-numeric (missing/empty/garbage) AND non-positive: amoCRM fires a status
    webhook with price="0" on every stage move for a lead with no Sum set (incl.
    OQIM's own lead-creation events), and a captured 0 would pin deal_value to 0
    and lock out a later real human-set price. Treat <=0 as 'no price'."""
    if raw is None:
        return None
    try:
        amount = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _coerce_user_id(raw: str | None) -> int | None:
    """amoCRM user ids arrive as form strings. Return an int, or None when absent
    or unparseable (the latch then fails open)."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _status_kind(status: dict) -> str:
    sid = int(status.get("id", 0))
    if sid == _AMOCRM_WON_STATUS_ID:
        return "won"
    if sid == _AMOCRM_LOST_STATUS_ID:
        return "lost"
    if int(status.get("type", 0)) == 1:  # the system/incoming status
        return "unsorted"
    return "active"

# amoCRM's OAuth dialog is global (the account host is unknown until the
# callback's ``referer`` arrives); the token endpoint is per-subdomain.
_AUTHORIZE_URL = "https://www.amocrm.ru/oauth"
_AMOCRM_HOST_SUFFIXES = (".amocrm.ru", ".kommo.com")


def _subdomain_from_referer(referer: str | None) -> str:
    """Extract the account subdomain from amoCRM's callback ``referer`` param.

    amoCRM never tells us the account host at authorize time — it only arrives
    here. A missing/foreign host is unrecoverable: we must not guess a subdomain.
    """
    if not referer:
        raise CrmAuthError("amocrm callback missing referer (account host)")
    host = referer.replace("https://", "").replace("http://", "").split("/", 1)[0].strip()
    if not any(host.endswith(suffix) for suffix in _AMOCRM_HOST_SUFFIXES):
        raise CrmAuthError(f"amocrm callback referer is not an amoCRM host: {host!r}")
    subdomain = host.split(".", 1)[0]
    if not subdomain:
        raise CrmAuthError(f"amocrm callback referer has no subdomain: {host!r}")
    return subdomain


def _amocrm_field_values(ftype: str, val: Any) -> list[dict]:
    """amoCRM custom-field value encoding per field type. select/multiselect use
    the enum_id slot (coerce already resolved label->enum_id above the seam);
    every other type uses the value slot."""
    if ftype == "select":
        return [{"enum_id": int(val)}]
    if ftype == "multiselect":
        return [{"enum_id": int(v)} for v in (val or [])]
    return [{"value": val}]


class AmoCrmProvider(CrmProvider):
    def __init__(
        self,
        *,
        http_client_factory: Callable[..., Any] = httpx.AsyncClient,
        timeout: float = 20.0,
    ) -> None:
        self._http_client_factory = http_client_factory
        self._timeout = timeout

    def _base(self, conn: Any) -> str:
        return f"https://{conn.provider_account_ref}.amocrm.ru"

    def capabilities(self) -> frozenset[str]:
        return frozenset({"tasks", "tags", "custom_fields", "notes"})

    # --- auth ---
    def oauth_authorize_url(self, *, state: str, redirect_uri: str) -> str:
        settings = get_settings()
        # redirect_uri is configured on the integration in amoCRM; passed here
        # for parity/future-proofing. mode=post_message is amoCRM's documented
        # dialog mode; confirmed against the live account during connect testing.
        params = urlencode(
            {
                "client_id": settings.amocrm_client_id,
                "state": state,
                "mode": "post_message",
            }
        )
        return f"{_AUTHORIZE_URL}?{params}"

    async def oauth_exchange(self, callback: CrmOAuthCallback) -> CrmConnectionBootstrap:
        settings = get_settings()
        subdomain = _subdomain_from_referer(callback.raw_params.get("referer"))
        tokens = await self._token_request(
            subdomain,
            data={
                "client_id": settings.amocrm_client_id,
                "client_secret": settings.amocrm_client_secret,
                "grant_type": "authorization_code",
                "code": callback.code,
                "redirect_uri": settings.amocrm_redirect_uri,
            },
        )
        return CrmConnectionBootstrap(tokens=tokens, provider_account_ref=subdomain)

    async def refresh(self, conn: Any) -> CrmTokens:
        settings = get_settings()
        return await self._token_request(
            conn.provider_account_ref,
            data={
                "client_id": settings.amocrm_client_id,
                "client_secret": settings.amocrm_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": conn.refresh_token,
                "redirect_uri": settings.amocrm_redirect_uri,
            },
        )

    async def _token_request(self, subdomain: str, *, data: dict) -> CrmTokens:
        url = f"https://{subdomain}.amocrm.ru/oauth2/access_token"
        try:
            async with self._http_client_factory(timeout=self._timeout) as client:
                response = await client.post(url, data=data)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            # never log the body — it carries the secret/tokens.
            raise CrmAuthError(f"amocrm token request failed: {type(exc).__name__}") from exc
        access_token = str(payload.get("access_token") or "")
        refresh_token = str(payload.get("refresh_token") or "")
        if not access_token or not refresh_token:
            raise CrmAuthError("amocrm token response missing access/refresh token")
        expires_in = int(payload.get("expires_in") or 86_400)
        return CrmTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=utc_now() + timedelta(seconds=expires_in),
        )

    # --- authenticated API calls ---
    async def _api(
        self,
        conn: Any,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{self._base(conn)}/api/v4{path}"
        # Bearer header, never a token in the query string (server/proxy logs).
        kwargs: dict = {"headers": {"Authorization": f"Bearer {conn.access_token}"}}
        if params is not None:
            kwargs["params"] = params
        if json is not None:  # GET has no body — httpx.get rejects a json kwarg.
            kwargs["json"] = json
        async with self._http_client_factory(timeout=self._timeout) as client:
            response = await getattr(client, method)(url, **kwargs)
        if response.status_code == 401:
            raise CrmUnauthorizedError("amocrm api 401")
        if response.status_code == 204:
            return None
        if response.status_code >= 400:
            # Surface the amoCRM validation body at the choke point — the worker
            # only ever logged the exception class, so 4xx detail was invisible.
            logger.warning(
                "amocrm api error method=%s path=%s status=%s body=%s",
                method,
                path,
                response.status_code,
                (getattr(response, "text", "") or "")[:600],
            )
        response.raise_for_status()
        return response.json()

    async def fetch_pipelines(self, conn: Any) -> list[CrmPipeline]:
        payload = await self._api(conn, "get", "/leads/pipelines")
        pipelines: list[CrmPipeline] = []
        for p in (payload or {}).get("_embedded", {}).get("pipelines", []):
            statuses = [
                CrmPipelineStatus(
                    stage_id=str(s["id"]),
                    name=str(s.get("name", "")),
                    sort=int(s.get("sort", 0)),
                    kind=_status_kind(s),
                )
                for s in p.get("_embedded", {}).get("statuses", [])
            ]
            pipelines.append(
                CrmPipeline(
                    pipeline_id=str(p["id"]),
                    name=str(p.get("name", "")),
                    is_main=bool(p.get("is_main", False)),
                    statuses=statuses,
                )
            )
        return pipelines

    async def discover_schema(self, conn: Any) -> CrmAccountSchema:
        # S2: the full account schema — pipelines + stages, per-entity custom
        # fields, responsible users, and task types (5 reads). fetch_pipelines is
        # LOAD-BEARING (its failure aborts connect/rediscover); the 4 secondary
        # reads are best-effort — a 403 (restricted token) / 5xx / 204 on them
        # degrades to empty so connect never hard-fails on optional enrichment.
        pipelines = await self.fetch_pipelines(conn)
        custom_fields = {
            "leads": await self._safe_fetch(self._fetch_custom_fields, conn, "leads"),
            "contacts": await self._safe_fetch(self._fetch_custom_fields, conn, "contacts"),
        }
        users = await self._safe_fetch(self._fetch_users, conn)
        task_types = await self._safe_fetch(self._fetch_task_types, conn)
        return CrmAccountSchema(
            pipelines=pipelines, custom_fields=custom_fields,
            users=users, task_types=task_types,
        )

    async def _safe_fetch(self, fn: Any, *args: Any) -> list:
        """Run a SECONDARY (non-load-bearing) discovery read, degrading to [] on any
        HTTP error or 401 so it can never abort connect or a rediscover tick."""
        try:
            return await fn(*args)
        except (httpx.HTTPError, CrmUnauthorizedError) as exc:
            logger.warning("crm.amocrm.secondary_read_failed fn=%s error=%s",
                           getattr(fn, "__name__", "?"), type(exc).__name__)
            return []

    async def _fetch_custom_fields(self, conn: Any, entity: str) -> list[CrmFieldDef]:
        payload = await self._api(conn, "get", f"/{entity}/custom_fields", params={"limit": 250})
        out: list[CrmFieldDef] = []
        for f in (payload or {}).get("_embedded", {}).get("custom_fields", []):
            enums = tuple(
                CrmFieldEnum(enum_id=str(e.get("id")), value=str(e.get("value") or ""))
                for e in (f.get("enums") or [])
            )
            out.append(CrmFieldDef(
                key_id=str(f["id"]) if f.get("id") is not None else None,
                code=f.get("code"),
                name=str(f.get("name") or ""),
                type=str(f.get("type") or ""),
                enums=enums,
            ))
        return out

    async def _fetch_users(self, conn: Any) -> list[CrmUser]:
        payload = await self._api(conn, "get", "/users", params={"limit": 250})
        return [
            CrmUser(user_id=str(u["id"]), name=str(u.get("name") or ""))
            for u in (payload or {}).get("_embedded", {}).get("users", [])
        ]

    async def _fetch_task_types(self, conn: Any) -> list[CrmTaskType]:
        payload = await self._api(conn, "get", "/account", params={"with": "task_types"})
        return [
            CrmTaskType(task_type_id=str(t["id"]), name=str(t.get("name") or ""))
            for t in (payload or {}).get("_embedded", {}).get("task_types", [])
        ]

    async def set_custom_fields(
        self, conn: Any, entity_id: str, fields: dict[str, dict], *, entity: str = "leads"
    ) -> str:
        # fields maps amoCRM field_id -> {"value": v, "type": t}. The adapter owns
        # the per-type amoCRM encoding (the seam): selects go in the enum_id slot,
        # not value. key->id + label->enum_id resolution lives above the seam (S4).
        # entity is "leads" or "contacts" (S4b). Empty dict is a no-op.
        if not fields:
            return "ok"
        values = [
            {
                "field_id": int(fid),
                "values": _amocrm_field_values(
                    (spec or {}).get("type") or "text", (spec or {}).get("value")
                ),
            }
            for fid, spec in fields.items()
        ]
        await self._api(
            conn, "patch", f"/{entity}/{entity_id}",
            json={"custom_fields_values": values},
        )
        return "ok"

    async def fetch_contact_custom_fields(self, conn: Any, contact_id: str) -> dict:
        # S4b DNC inbound: read a contact's custom field values as {field_id_str: value}.
        payload = await self._api(conn, "get", f"/contacts/{contact_id}")
        cfv = (payload or {}).get("custom_fields_values") or []
        return {
            str(c.get("field_id")): (c.get("values") or [{}])[0].get("value")
            for c in cfv
            if c.get("field_id") is not None
        }

    async def find_contact_by_phone(self, conn: Any, phone: str) -> str | None:
        payload = await self._api(conn, "get", "/contacts", params={"query": phone})
        if not payload:
            return None
        contacts = payload.get("_embedded", {}).get("contacts", [])
        return str(contacts[0]["id"]) if contacts else None

    async def create_contact(self, conn: Any, contact: CrmContactInput) -> str:
        entry: dict = {"name": contact.name}
        if contact.phone:
            # standard field by field_code — no per-account custom-field id needed.
            entry["custom_fields_values"] = [
                {"field_code": "PHONE", "values": [{"value": contact.phone}]}
            ]
        payload = await self._api(conn, "post", "/contacts", json=[entry])
        return str(payload["_embedded"]["contacts"][0]["id"])

    async def update_contact_phone(self, conn: Any, contact_id: str, phone: str) -> None:
        await self._api(
            conn,
            "patch",
            f"/contacts/{contact_id}",
            json={
                "custom_fields_values": [
                    {"field_code": "PHONE", "values": [{"value": phone}]}
                ]
            },
        )

    async def create_lead(self, conn: Any, lead: CrmLeadInput) -> str:
        entry = {
            "name": lead.name,
            "pipeline_id": int(lead.pipeline_id),
            "status_id": int(lead.stage_id),
            "_embedded": {"contacts": [{"id": int(lead.contact_id)}]},
        }
        payload = await self._api(conn, "post", "/leads", json=[entry])
        return str(payload["_embedded"]["leads"][0]["id"])

    async def update_lead_stage(
        self, conn: Any, lead_id: str, *, role: str, config: dict
    ) -> str:
        # Resolve role -> amoCRM status_id + pipeline_id INSIDE the adapter so the
        # raw provider ids never cross the seam. config is the neutral pipeline view
        # (resolve_pipeline_view) for the lead's pipeline. Returns the stage_id
        # actually pushed (for the caller's last_synced_stage_id), or "" on a no-op.
        stage_map = (config or {}).get("stage_map") or {}
        pipeline_id = str((config or {}).get("pipeline_id") or "")
        stage_id = str((stage_map.get(role) or {}).get("stage_id") or "")
        if not stage_id:
            # The role has no stage in this pipeline (e.g. a re-home target whose
            # role_map lacks this role) — fall back to the first ACTIVE stage rather
            # than emit int("") (which would crash). Never push to won/lost.
            actives = sorted(
                (s for s in (config or {}).get("snapshot_statuses") or []
                 if s.get("kind") == "active"),
                key=lambda s: int(s.get("sort", 0)),
            )
            stage_id = str(actives[0].get("stage_id") or "") if actives else ""
        if not stage_id or not pipeline_id:
            return ""  # degenerate config: nothing safe to push
        await self._api(
            conn,
            "patch",
            f"/leads/{lead_id}",
            json={"status_id": int(stage_id), "pipeline_id": int(pipeline_id)},
        )
        return stage_id

    async def set_lead_value(
        self, conn: Any, lead_id: str, *, amount: Decimal, currency: str = "UZS"
    ) -> None:
        # amoCRM lead price is an integer of the account currency; coerce HERE,
        # never above the seam (a raw Decimal upstream is not JSON-serializable —
        # cf. the 2026-06-15 deal_value crash). currency is account-level in amoCRM
        # (no per-lead field); accepted for the neutral contract / future providers.
        await self._api(conn, "patch", f"/leads/{lead_id}", json={"price": int(amount)})

    async def add_note(self, conn: Any, lead_id: str, text: str) -> None:
        await self._api(
            conn,
            "post",
            f"/leads/{lead_id}/notes",
            json=[{"note_type": "common", "params": {"text": text}}],
        )

    async def create_followup_task(
        self, conn: Any, lead_id: str, *, text: str, due_at: datetime,
        owner_ref: str | None = None,
    ) -> str:
        entry: dict = {
            "text": text,
            "complete_till": int(due_at.timestamp()),  # datetime -> amoCRM epoch, inside the adapter
            "entity_id": int(lead_id),
            "entity_type": "leads",
            "task_type_id": 1,
        }
        if owner_ref is not None:
            entry["responsible_user_id"] = int(owner_ref)
        payload = await self._api(conn, "post", "/tasks", json=[entry])
        return str(payload["_embedded"]["tasks"][0]["id"])

    async def add_tags(self, conn: Any, lead_id: str, names: list[str]) -> None:
        # Additive: tags_to_add does NOT replace existing tags (amoCRM dedupes by name).
        # tags_to_add is a TOP-LEVEL sibling of price/status_id on the single-lead PATCH;
        # amoCRM silently drops it if nested under _embedded (200 OK, no tag landed).
        if not names:
            return
        await self._api(
            conn, "patch", f"/leads/{lead_id}",
            json={"tags_to_add": [{"name": n} for n in names]},
        )

    async def fetch_lead(
        self, conn: Any, lead_id: str, *, include_notes: bool = False
    ) -> CrmLeadSnapshot:
        payload = await self._api(conn, "get", f"/leads/{lead_id}")
        notes: list[str] = []
        if include_notes:
            notes = await self._fetch_lead_notes(conn, lead_id)
        cfv = payload.get("custom_fields_values") or []
        custom_fields = {
            str(c.get("field_id")): (c.get("values") or [{}])[0].get("value")
            for c in cfv
            if c.get("field_id") is not None
        }
        return CrmLeadSnapshot(
            lead_id=str(payload.get("id", lead_id)),
            stage_id=str(payload.get("status_id", "")),
            value=payload.get("price"),
            notes=notes,
            custom_fields=custom_fields,
        )

    async def _fetch_lead_notes(
        self,
        conn: Any,
        lead_id: str,
        *,
        limit: int = 3,
        max_chars: int = 300,
        fetch_window: int = 10,
    ) -> list[str]:
        # Over-fetch the raw window (amoCRM interleaves text-less system notes —
        # lead created, stage change, call_in/out — by updated_at), THEN keep the
        # `limit` most-recent notes that actually carry owner text. Capping the API
        # at `limit` would let system notes crowd out real owner notes.
        payload = await self._api(
            conn,
            "get",
            f"/leads/{lead_id}/notes",
            params={"limit": fetch_window, "order[updated_at]": "desc"},
        )
        rows = (payload or {}).get("_embedded", {}).get("notes", [])
        out: list[str] = []
        for row in rows:
            text = str(((row or {}).get("params") or {}).get("text") or "").strip()
            if text:
                out.append(text[:max_chars])
            if len(out) >= limit:
                break
        return out

    async def fetch_contacts(self, conn: Any, *, page: int) -> list[CrmContactSnapshot]:
        payload = await self._api(
            conn, "get", "/contacts", params={"page": page, "limit": _AMOCRM_PAGE_LIMIT}
        )
        rows = (payload or {}).get("_embedded", {}).get("contacts", [])
        return [
            CrmContactSnapshot(
                contact_id=str(c["id"]),
                name=str(c.get("name", "")),
                phone=_phone_from_contact(c),
            )
            for c in rows
        ]

    async def fetch_leads_by_stage(
        self, conn: Any, *, pipeline_id: str, status_ids: list[str], page: int
    ) -> list[CrmContactSnapshot]:
        params: dict = {"with": "contacts", "page": page, "limit": _AMOCRM_PAGE_LIMIT}
        for i, sid in enumerate(status_ids):
            params[f"filter[statuses][{i}][pipeline_id]"] = pipeline_id
            params[f"filter[statuses][{i}][status_id]"] = sid
        payload = await self._api(conn, "get", "/leads", params=params)
        rows = (payload or {}).get("_embedded", {}).get("leads", [])
        out: list[CrmContactSnapshot] = []
        for lead in rows:
            for contact in lead.get("_embedded", {}).get("contacts", []):
                out.append(CrmContactSnapshot(contact_id=str(contact["id"]), name="", phone=None))
        return out

    async def fetch_contacts_by_ids(
        self, conn: Any, *, contact_ids: list[str]
    ) -> list[CrmContactSnapshot]:
        out: list[CrmContactSnapshot] = []
        for start in range(0, len(contact_ids), _AMOCRM_ID_FILTER_CHUNK):
            chunk = contact_ids[start : start + _AMOCRM_ID_FILTER_CHUNK]
            params: dict = {"limit": _AMOCRM_PAGE_LIMIT}
            for i, cid in enumerate(chunk):
                params[f"filter[id][{i}]"] = cid
            payload = await self._api(conn, "get", "/contacts", params=params)
            rows = (payload or {}).get("_embedded", {}).get("contacts", [])
            out.extend(
                CrmContactSnapshot(
                    contact_id=str(c["id"]),
                    name=str(c.get("name", "")),
                    phone=_phone_from_contact(c),
                )
                for c in rows
            )
        return out

    async def fetch_last_contact_note(self, conn: Any, *, contact_id: str) -> str | None:
        payload = await self._api(
            conn,
            "get",
            f"/contacts/{contact_id}/notes",
            params={"limit": 1, "order[updated_at]": "desc"},
        )
        rows = (payload or {}).get("_embedded", {}).get("notes", [])
        if not rows:
            return None
        text = str(((rows[0] or {}).get("params") or {}).get("text") or "").strip()
        return text or None

    async def add_contact_note(self, conn: Any, *, contact_id: str, text: str) -> None:
        await self._api(
            conn,
            "post",
            f"/contacts/{contact_id}/notes",
            json=[{"note_type": "common", "params": {"text": text}}],
        )

    # --- webhooks (Slice B: two-way) ---
    async def register_webhook(self, conn: Any, *, destination: str, events: list[str]) -> str:
        payload = await self._api(
            conn, "post", "/webhooks",
            json={"destination": destination, "settings": list(events), "sort": 10},
        )
        hooks = (payload or {}).get("_embedded", {}).get("webhooks", [])
        if hooks:
            return str(hooks[0].get("id", ""))
        return str((payload or {}).get("id", ""))

    def parse_webhook(self, form: dict[str, str]) -> CrmWebhookBatch:
        # amoCRM posts form-encoded nested keys. Lead id keys per event type:
        # status carries an id plus a status_id, responsible carries an id, and a
        # note carries an element_id (the lead the note is on). See the while-loops
        # below for the exact key shapes.
        # BUILD-TIME: confirm these keys against a real amoCRM payload (see spec §5).
        subdomain = (form.get("account[subdomain]", "") or "").split(".", 1)[0]
        events: list[CrmStageEvent] = []
        i = 0
        while f"leads[status][{i}][id]" in form:
            events.append(CrmStageEvent(
                kind="status_lead", lead_id=form[f"leads[status][{i}][id]"],
                status_id=form.get(f"leads[status][{i}][status_id]"),
                value=_coerce_price(form.get(f"leads[status][{i}][price]")),
            ))
            i += 1
        # A human editing a card (Sum/stage) without a stage move fires leads[update].
        # leads[add]/leads[delete] stay UNhandled (out of scope).
        i = 0
        while f"leads[update][{i}][id]" in form:
            events.append(CrmStageEvent(
                kind="update_lead", lead_id=form[f"leads[update][{i}][id]"],
                status_id=form.get(f"leads[update][{i}][status_id]"),
                value=_coerce_price(form.get(f"leads[update][{i}][price]")),
                author_id=_coerce_user_id(form.get(f"leads[update][{i}][modified_user_id]")),
            ))
            i += 1
        i = 0
        while f"leads[responsible][{i}][id]" in form:
            events.append(CrmStageEvent(
                kind="responsible_lead", lead_id=form[f"leads[responsible][{i}][id]"],
                author_id=_coerce_user_id(form.get(f"leads[responsible][{i}][modified_user_id]")),
            ))
            i += 1
        # A human editing a CONTACT (e.g. the do-not-contact checkbox) fires
        # contacts[update]. lead_id here carries the CONTACT id; the handler matches
        # it against provider_contact_id (S4b DNC inbound). BUILD-TIME: confirm the
        # contacts[update] key shape against a real amoCRM payload.
        i = 0
        while f"contacts[update][{i}][id]" in form:
            events.append(CrmStageEvent(
                kind="update_contact", lead_id=form[f"contacts[update][{i}][id]"],
                author_id=_coerce_user_id(form.get(f"contacts[update][{i}][modified_user_id]")),
            ))
            i += 1
        # amoCRM note nesting depth is undocumented; accept both the flat
        # leads[note][i][element_id] and the doubly-nested
        # leads[note][i][note][element_id] shapes (element_id = the lead the note
        # is on, element_type=2); tighten once a real note payload is captured.
        i = 0
        while (
            f"leads[note][{i}][element_id]" in form
            or f"leads[note][{i}][note][element_id]" in form
        ):
            lead_id = form.get(f"leads[note][{i}][element_id]") or form.get(
                f"leads[note][{i}][note][element_id]"
            )
            if lead_id:
                events.append(CrmStageEvent(kind="note_lead", lead_id=lead_id))
            i += 1
        return CrmWebhookBatch(account_subdomain=subdomain, events=events)
