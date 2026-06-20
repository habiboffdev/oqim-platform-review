"""AmoCrmProvider auth + locked single-use refresh."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.config import get_settings
from app.db.base import utc_now
from app.models.crm_connection import CrmConnection
from app.modules.crm_connector.amocrm import AmoCrmProvider
from app.modules.crm_connector.contracts import (
    CrmAuthError,
    CrmContactInput,
    CrmLeadInput,
    CrmOAuthCallback,
    CrmTokens,
    CrmUnauthorizedError,
)
from app.modules.crm_connector.provider import CrmProvider
from app.modules.crm_connector.token_refresh import refresh_connection_locked

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _amocrm_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "amocrm_client_id", "cid-123")
    monkeypatch.setattr(s, "amocrm_client_secret", "secret-xyz")


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


async def test_authorize_url_has_client_id_state_no_secret():
    provider = AmoCrmProvider()
    url = provider.oauth_authorize_url(
        state="st-1", redirect_uri="https://your-domain.example/api/amocrm/auth/callback"
    )
    assert "cid-123" in url
    assert "st-1" in url
    assert "secret-xyz" not in url


async def test_oauth_exchange_uses_callback_subdomain():
    calls: list = []
    factory = _client_factory(
        [_response({"access_token": "acc", "refresh_token": "ref", "expires_in": 86400})],
        calls,
    )
    provider = AmoCrmProvider(http_client_factory=factory)
    boot = await provider.oauth_exchange(
        CrmOAuthCallback(code="c", raw_params={"referer": "mybiz.amocrm.ru"})
    )
    assert boot.provider_account_ref == "mybiz"
    assert boot.tokens.access_token == "acc"
    assert boot.tokens.refresh_token == "ref"
    assert boot.tokens.expires_at > utc_now()
    args, kwargs = calls[0]
    assert args[0] == "https://mybiz.amocrm.ru/oauth2/access_token"
    data = kwargs["data"]
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "c"
    assert "redirect_uri" in data
    # the secret travels in the POST body to amoCRM's own token endpoint (the
    # documented interface) but never appears in a URL/params.
    assert "secret-xyz" not in str(args)


async def test_oauth_exchange_missing_referer_raises():
    provider = AmoCrmProvider(http_client_factory=_client_factory([]))
    with pytest.raises(CrmAuthError):
        await provider.oauth_exchange(CrmOAuthCallback(code="c", raw_params={}))
    with pytest.raises(CrmAuthError):
        await provider.oauth_exchange(
            CrmOAuthCallback(code="c", raw_params={"referer": "not-an-amocrm-host"})
        )


async def test_refresh_rotates_both_tokens():
    calls: list = []
    factory = _client_factory(
        [_response({"access_token": "acc2", "refresh_token": "ref2", "expires_in": 86400})],
        calls,
    )
    provider = AmoCrmProvider(http_client_factory=factory)
    conn = SimpleNamespace(provider_account_ref="mybiz", refresh_token="ref1")
    tokens = await provider.refresh(conn)
    assert tokens.access_token == "acc2"
    assert tokens.refresh_token == "ref2"  # single-use rotation — new refresh token
    args, kwargs = calls[0]
    assert args[0] == "https://mybiz.amocrm.ru/oauth2/access_token"
    assert kwargs["data"]["grant_type"] == "refresh_token"
    assert kwargs["data"]["refresh_token"] == "ref1"


async def test_refresh_connection_locked_rotates_then_rechecks(db_session, workspace):
    conn = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status="active",
        provider_account_ref="mybiz",
        webhook_token="tokX",
        pipeline_config={},
        access_token="acc1",
        refresh_token="ref1",
        token_expires_at=utc_now() + timedelta(minutes=10),  # near expiry
    )
    db_session.add(conn)
    await db_session.flush()

    refresh_calls: list[str] = []

    class _Provider:
        async def refresh(self, c):
            refresh_calls.append(c.refresh_token)
            return CrmTokens(
                access_token="acc2",
                refresh_token="ref2",
                expires_at=utc_now() + timedelta(hours=24),
            )

    provider = _Provider()
    await refresh_connection_locked(db_session, connection_id=conn.id, provider=provider)
    await db_session.refresh(conn)
    assert conn.access_token == "acc2"
    assert conn.refresh_token == "ref2"
    assert len(refresh_calls) == 1

    # second call: token now far-future → under-lock re-check skips the provider
    await refresh_connection_locked(db_session, connection_id=conn.id, provider=provider)
    assert len(refresh_calls) == 1


# --- Task 4: writes + pipelines -------------------------------------------------
def _conn():
    return SimpleNamespace(provider_account_ref="mybiz", access_token="acc")


async def test_fetch_pipelines_parses_statuses_and_kinds():
    payload = {
        "_embedded": {
            "pipelines": [
                {
                    "id": 111,
                    "name": "Main",
                    "is_main": True,
                    "sort": 1,
                    "_embedded": {
                        "statuses": [
                            {"id": 1, "name": "Incoming", "sort": 0, "type": 1},
                            {"id": 201, "name": "First contact", "sort": 10, "type": 0},
                            {"id": 202, "name": "Negotiation", "sort": 20, "type": 0},
                            {"id": 142, "name": "Won", "sort": 10000, "type": 1},
                            {"id": 143, "name": "Lost", "sort": 11000, "type": 0},
                        ]
                    },
                }
            ]
        }
    }
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response(payload)], calls))
    pipelines = await provider.fetch_pipelines(_conn())
    assert len(pipelines) == 1
    pl = pipelines[0]
    assert pl.pipeline_id == "111" and pl.is_main is True
    kinds = {s.stage_id: s.kind for s in pl.statuses}
    assert kinds == {"1": "unsorted", "201": "active", "202": "active", "142": "won", "143": "lost"}
    args, kwargs = calls[0]
    assert args[0] == "https://mybiz.amocrm.ru/api/v4/leads/pipelines"
    assert kwargs["headers"]["Authorization"] == "Bearer acc"


async def test_find_contact_by_phone_returns_first_or_none():
    provider = AmoCrmProvider(
        http_client_factory=_client_factory([_response({"_embedded": {"contacts": [{"id": 555}]}})])
    )
    assert await provider.find_contact_by_phone(_conn(), "998901112233") == "555"
    provider2 = AmoCrmProvider(http_client_factory=_client_factory([_response({}, status_code=204)]))
    assert await provider2.find_contact_by_phone(_conn(), "x") is None


async def test_create_contact_posts_name_and_phone_field_code():
    calls: list = []
    provider = AmoCrmProvider(
        http_client_factory=_client_factory([_response({"_embedded": {"contacts": [{"id": 777}]}})], calls)
    )
    cid = await provider.create_contact(
        _conn(),
        CrmContactInput(name="Ali", phone="998901112233", channel_label="telegram_dm:@ali"),
    )
    assert cid == "777"
    args, kwargs = calls[0]
    assert args[0] == "https://mybiz.amocrm.ru/api/v4/contacts"
    body = kwargs["json"]
    assert body[0]["name"] == "Ali"
    assert any(f.get("field_code") == "PHONE" for f in body[0]["custom_fields_values"])


async def test_create_lead_posts_pipeline_status_contact():
    calls: list = []
    provider = AmoCrmProvider(
        http_client_factory=_client_factory([_response({"_embedded": {"leads": [{"id": 888}]}})], calls)
    )
    lid = await provider.create_lead(
        _conn(), CrmLeadInput(name="Ali", pipeline_id="111", stage_id="201", contact_id="777")
    )
    assert lid == "888"
    body = calls[0][1]["json"]
    assert body[0]["pipeline_id"] == 111
    assert body[0]["status_id"] == 201
    assert body[0]["_embedded"]["contacts"][0]["id"] == 777


async def test_update_lead_stage_resolves_role_to_status_and_pipeline():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response({})], calls))
    config = {
        "pipeline_id": "111",
        "stage_map": {"negotiation": {"stage_id": "202", "sort": 20}},
    }
    await provider.update_lead_stage(_conn(), "888", role="negotiation", config=config)
    args, kwargs = calls[0]
    assert args[0] == "https://mybiz.amocrm.ru/api/v4/leads/888"
    assert kwargs["json"]["status_id"] == 202
    assert kwargs["json"]["pipeline_id"] == 111


async def test_set_lead_value_coerces_decimal_to_int_price():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response({})], calls))
    await provider.set_lead_value(_conn(), "888", amount=Decimal("4900000.50"), currency="UZS")
    # Decimal coerced to int INSIDE the adapter — amoCRM price is integer so'm.
    assert calls[0][1]["json"]["price"] == 4900000


async def test_add_note_posts_common_note():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response({})], calls))
    await provider.add_note(_conn(), lead_id="888", text="hello")
    args, kwargs = calls[0]
    assert args[0] == "https://mybiz.amocrm.ru/api/v4/leads/888/notes"
    assert kwargs["json"][0]["note_type"] == "common"
    assert kwargs["json"][0]["params"]["text"] == "hello"


async def test_fetch_lead_snapshot():
    provider = AmoCrmProvider(
        http_client_factory=_client_factory(
            [_response({"id": 888, "status_id": 202, "pipeline_id": 111, "price": 4900000})]
        )
    )
    snap = await provider.fetch_lead(_conn(), "888")
    assert snap.lead_id == "888"
    assert snap.stage_id == "202"
    assert snap.value == 4900000


async def test_fetch_lead_parses_custom_fields():
    provider = AmoCrmProvider(
        http_client_factory=_client_factory(
            [
                _response(
                    {
                        "id": 26789627,
                        "status_id": 86476602,
                        "price": 0,
                        "custom_fields_values": [
                            {"field_id": 600126, "values": [{"value": True}]}
                        ],
                    }
                )
            ]
        )
    )
    snap = await provider.fetch_lead(_conn(), "26789627")
    assert snap.custom_fields == {"600126": True}


async def test_api_401_raises_unauthorized():
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response({}, status_code=401)]))
    with pytest.raises(CrmUnauthorizedError):
        await provider.fetch_lead(_conn(), "888")


async def test_fetch_lead_without_notes_issues_no_notes_call():
    calls: list = []
    factory = _client_factory(
        [_response({"id": 55, "status_id": 1002, "price": 4900000})],
        calls,
    )
    provider = AmoCrmProvider(http_client_factory=factory)
    conn = SimpleNamespace(provider_account_ref="mybiz", access_token="tok")

    snapshot = await provider.fetch_lead(conn, "55")

    assert snapshot.lead_id == "55"
    assert snapshot.stage_id == "1002"
    assert snapshot.value == 4900000
    assert snapshot.notes == []
    assert len(calls) == 1  # only the lead GET, no notes GET


async def test_fetch_lead_with_notes_returns_recent_notes_newest_first():
    calls: list = []
    long_text = "x" * 500
    factory = _client_factory(
        [
            _response({"id": 55, "status_id": 1002, "price": 4900000}),
            _response(
                {
                    "_embedded": {
                        "notes": [
                            {"params": {"text": "newest note"}},
                            {"params": {"text": long_text}},
                            {"params": {"text": "third note"}},
                            {"params": {"text": "fourth note (dropped)"}},
                            {"note_type": "call_in", "params": {}},  # no text -> skipped
                        ]
                    }
                }
            ),
        ],
        calls,
    )
    provider = AmoCrmProvider(http_client_factory=factory)
    conn = SimpleNamespace(provider_account_ref="mybiz", access_token="tok")

    snapshot = await provider.fetch_lead(conn, "55", include_notes=True)

    assert snapshot.notes[0] == "newest note"
    assert len(snapshot.notes[1]) == 300  # truncated to 300 chars
    assert snapshot.notes == ["newest note", "x" * 300, "third note"]  # capped at 3
    # second call is the notes GET, ordered desc, over-fetching the raw window
    # (then keeps the 3 most-recent TEXT notes; see the system-note test below)
    notes_args, notes_kwargs = calls[1]
    assert notes_args[0].endswith("/leads/55/notes")
    assert notes_kwargs["params"]["order[updated_at]"] == "desc"
    assert notes_kwargs["params"]["limit"] >= 10


async def test_fetch_lead_notes_skips_system_notes_without_crowding_out_owner_text():
    """System notes (lead created, stage change, calls) carry no params.text. They
    must NOT consume the 3 owner-note slots: over-fetch the raw window, then keep
    the 3 most-recent TEXT notes (design 2026-06-14 'last 3 owner notes')."""
    calls: list = []
    factory = _client_factory(
        [
            _response({"id": 55, "status_id": 1002, "price": 4900000}),
            _response(
                {
                    "_embedded": {
                        "notes": [
                            {"note_type": "lead_status_changed", "params": {}},
                            {"note_type": "call_in", "params": {}},
                            {"note_type": "common", "params": {"text": "owner A"}},
                            {"note_type": "service_message", "params": {}},
                            {"note_type": "common", "params": {"text": "owner B"}},
                            {"note_type": "common", "params": {"text": "owner C"}},
                            {"note_type": "common", "params": {"text": "owner D (dropped)"}},
                        ]
                    }
                }
            ),
        ],
        calls,
    )
    provider = AmoCrmProvider(http_client_factory=factory)
    conn = SimpleNamespace(provider_account_ref="mybiz", access_token="tok")

    snapshot = await provider.fetch_lead(conn, "55", include_notes=True)

    assert snapshot.notes == ["owner A", "owner B", "owner C"]  # 3 text notes, system skipped
    _, notes_kwargs = calls[1]
    assert notes_kwargs["params"]["limit"] >= 10  # over-fetch beyond the 3-note output cap


# --- Slice A: create_task + add_tags --------------------------------------------
async def test_create_followup_task_posts_array_with_required_fields():
    calls: list = []
    factory = _client_factory(
        [_response({"_embedded": {"tasks": [{"id": 987}]}})], calls
    )
    provider = AmoCrmProvider(http_client_factory=factory)
    conn = SimpleNamespace(provider_account_ref="mybiz", access_token="tok")

    task_id = await provider.create_followup_task(
        conn, "55", text="Call this lead",
        due_at=datetime.fromtimestamp(1718000000, tz=UTC),
    )

    assert task_id == "987"
    args, kwargs = calls[0]
    assert args[0].endswith("/tasks")
    body = kwargs["json"]
    assert isinstance(body, list) and len(body) == 1
    entry = body[0]
    assert entry["text"] == "Call this lead"
    assert entry["complete_till"] == 1718000000  # datetime -> epoch inside the adapter
    assert entry["entity_id"] == 55
    assert entry["entity_type"] == "leads"
    assert entry["task_type_id"] == 1
    assert "responsible_user_id" not in entry  # omitted -> amoCRM defaults the user


async def test_create_followup_task_includes_owner_when_given():
    calls: list = []
    factory = _client_factory([_response({"_embedded": {"tasks": [{"id": 1}]}})], calls)
    provider = AmoCrmProvider(http_client_factory=factory)
    conn = SimpleNamespace(provider_account_ref="mybiz", access_token="tok")
    await provider.create_followup_task(
        conn, "55", text="t",
        due_at=datetime.fromtimestamp(1, tz=UTC), owner_ref="42",
    )
    assert calls[0][1]["json"][0]["responsible_user_id"] == 42


async def test_add_tags_patches_additive_tags():
    calls: list = []
    factory = _client_factory([_response({})], calls)
    provider = AmoCrmProvider(http_client_factory=factory)
    conn = SimpleNamespace(provider_account_ref="mybiz", access_token="tok")

    await provider.add_tags(conn, "55", ["oqim:operator-kerak"])

    args, kwargs = calls[0]
    assert args[0].endswith("/leads/55")
    assert kwargs["json"] == {"tags_to_add": [{"name": "oqim:operator-kerak"}]}


async def test_add_tags_with_empty_names_issues_no_http():
    calls: list = []
    factory = _client_factory([], calls)
    provider = AmoCrmProvider(http_client_factory=factory)
    conn = SimpleNamespace(provider_account_ref="mybiz", access_token="tok")

    await provider.add_tags(conn, "55", [])

    assert calls == []


# --- Slice B: webhook register/verify/parse -------------------------------------
async def test_register_webhook_posts_destination_and_events():
    calls: list = []
    factory = _client_factory([_response({"_embedded": {"webhooks": [{"id": 77}]}})], calls)
    provider = AmoCrmProvider(http_client_factory=factory)
    conn = SimpleNamespace(provider_account_ref="mybiz", access_token="tok")
    hook_id = await provider.register_webhook(
        conn, destination="https://your-domain.example/api/webhook/amocrm/abc",
        events=["status_lead", "responsible_lead", "note_lead"],
    )
    assert hook_id == "77"
    args, kwargs = calls[0]
    assert args[0].endswith("/webhooks")
    assert kwargs["json"]["destination"] == "https://your-domain.example/api/webhook/amocrm/abc"
    assert kwargs["json"]["settings"] == ["status_lead", "responsible_lead", "note_lead"]


def test_parse_webhook_maps_status_responsible_note():
    from app.modules.crm_connector.amocrm import AmoCrmProvider
    form = {
        "account[subdomain]": "mybiz.amocrm.ru",
        "leads[status][0][id]": "200",
        "leads[status][0][status_id]": "1002",
        "leads[update][0][id]": "201",
        "leads[update][0][modified_user_id]": "777",
        "leads[responsible][0][id]": "202",
        "leads[responsible][0][modified_user_id]": "42",
        "leads[note][0][element_id]": "203",
    }
    batch = AmoCrmProvider().parse_webhook(form)
    by_kind = {e.kind: e for e in batch.events}
    assert by_kind["status_lead"].lead_id == "200"
    # author is extracted for the events whose latch depends on it
    assert by_kind["update_lead"].lead_id == "201"
    assert by_kind["update_lead"].author_id == 777
    assert by_kind["responsible_lead"].lead_id == "202"
    assert by_kind["responsible_lead"].author_id == 42
    # a note still parses (it just no longer latches); author not required
    assert by_kind["note_lead"].lead_id == "203"


def test_parse_webhook_update_without_author_is_none():
    """A leads[update] payload with no modified_user_id parses author_id=None
    (fail-open: the service then won't latch)."""
    from app.modules.crm_connector.amocrm import AmoCrmProvider
    form = {"account[subdomain]": "mybiz", "leads[update][0][id]": "201"}
    batch = AmoCrmProvider().parse_webhook(form)
    ev = next(e for e in batch.events if e.kind == "update_lead")
    assert ev.author_id is None


def test_parse_webhook_handles_two_level_note_nesting():
    # amoCRM may doubly-nest the note key: leads[note][i][note][element_id].
    form = {"leads[note][0][note][element_id]": "202"}
    batch = AmoCrmProvider().parse_webhook(form)
    kinds = {(e.kind, e.lead_id, e.status_id) for e in batch.events}
    assert ("note_lead", "202", None) in kinds


def test_parse_webhook_captures_status_price():
    # amoCRM sends the lead price on every status_lead event; capture it as int.
    form = {
        "account[subdomain]": "mybiz",
        "leads[status][0][id]": "200",
        "leads[status][0][status_id]": "1002",
        "leads[status][0][price]": "4900000",
    }
    batch = AmoCrmProvider().parse_webhook(form)
    event = next(e for e in batch.events if e.kind == "status_lead")
    assert event.lead_id == "200"
    assert event.value == 4900000


def test_parse_webhook_ignores_zero_and_nonpositive_price():
    # amoCRM sends price="0" on a status webhook for any lead with no Sum set
    # (incl. OQIM's own lead-creation events). A captured 0 would pin deal_value
    # to 0 and lock out a later real human-set price, so <=0 must read as None.
    for raw in ("0", "-5", "", "   "):
        form = {
            "leads[status][0][id]": "200",
            "leads[status][0][status_id]": "1002",
            "leads[status][0][price]": raw,
        }
        event = next(
            e for e in AmoCrmProvider().parse_webhook(form).events
            if e.kind == "status_lead"
        )
        assert event.value is None, f"price {raw!r} must read as no value"


def test_parse_webhook_maps_update_lead():
    # A human editing a card (Sum/stage) without moving stage fires leads[update].
    form = {
        "account[subdomain]": "mybiz",
        "leads[update][0][id]": "200",
        "leads[update][0][status_id]": "1003",
        "leads[update][0][price]": "5000000",
    }
    batch = AmoCrmProvider().parse_webhook(form)
    event = next(e for e in batch.events if e.kind == "update_lead")
    assert event.lead_id == "200"
    assert event.status_id == "1003"
    assert event.value == 5000000


# --- S0: capabilities ----------------------------------------------------------
def test_capabilities_amocrm_declares_full_set():
    caps = AmoCrmProvider().capabilities()
    assert {"tasks", "tags", "custom_fields", "notes"} <= caps


def test_capabilities_default_is_empty():
    # A provider built incrementally declares nothing until it overrides.
    class _Bare(CrmProvider):
        def oauth_authorize_url(self, *, state, redirect_uri):
            return ""

        async def oauth_exchange(self, callback):
            ...

        async def refresh(self, conn):
            ...

    assert _Bare().capabilities() == frozenset()


# --- S2: discover_schema (full schema) -----------------------------------------
async def test_discover_schema_reads_fields_users_task_types():
    pipelines = {"_embedded": {"pipelines": [
        {"id": 111, "name": "Main", "is_main": True,
         "_embedded": {"statuses": [{"id": 201, "name": "First", "sort": 10, "type": 0}]}}]}}
    leads_cf = {"_embedded": {"custom_fields": [
        {"id": 600124, "code": None, "name": "Manba", "type": "select",
         "enums": [{"id": 9001, "value": "Instagram"}]}]}}
    contacts_cf = {"_embedded": {"custom_fields": [
        {"id": 737293, "code": None, "name": "Test field", "type": "text"}]}}
    users = {"_embedded": {"users": [{"id": 55001, "name": "Aziz"}]}}
    task_types = {"_embedded": {"task_types": [{"id": 1, "name": "Aloqa"}]}}
    provider = AmoCrmProvider(http_client_factory=_client_factory(
        [_response(pipelines), _response(leads_cf), _response(contacts_cf),
         _response(users), _response(task_types)]))
    schema = await provider.discover_schema(_conn())
    assert schema.pipelines[0].pipeline_id == "111"
    assert schema.custom_fields["leads"][0].name == "Manba"
    assert schema.custom_fields["leads"][0].enums[0].value == "Instagram"
    assert schema.custom_fields["contacts"][0].name == "Test field"
    assert schema.users[0].name == "Aziz"
    assert schema.task_types[0].task_type_id == "1"


def _error_response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "err", request=MagicMock(), response=MagicMock()
    )
    return response


async def test_discover_schema_degrades_when_secondary_reads_fail():
    # pipelines (load-bearing) succeeds; the 4 SECONDARY reads all 403 (a restricted
    # OAuth token) -> connect must still succeed with pipelines + empty extras,
    # never abort. Regression guard for the OAuth-connect hard-fail the review found.
    pipelines = {"_embedded": {"pipelines": [
        {"id": 111, "name": "Main", "is_main": True,
         "_embedded": {"statuses": [{"id": 201, "name": "First", "sort": 10, "type": 0}]}}]}}
    err = _error_response(403)
    provider = AmoCrmProvider(http_client_factory=_client_factory(
        [_response(pipelines), err, err, err, err]))
    schema = await provider.discover_schema(_conn())
    assert schema.pipelines[0].pipeline_id == "111"
    assert schema.custom_fields == {"leads": [], "contacts": []}
    assert schema.users == []
    assert schema.task_types == []


async def test_discover_schema_pipelines_failure_still_raises():
    # fetch_pipelines is load-bearing: a failure there must NOT be swallowed.
    provider = AmoCrmProvider(http_client_factory=_client_factory([_error_response(500)]))
    with pytest.raises(httpx.HTTPError):
        await provider.discover_schema(_conn())


# --- S0: set_custom_fields -----------------------------------------------------
async def test_set_custom_fields_patches_values_and_returns_ok():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response({})], calls))
    result = await provider.set_custom_fields(
        _conn(), "888", {"123456": {"value": "Premium", "type": "text"}}
    )
    assert result == "ok"
    args, kwargs = calls[0]
    assert args[0] == "https://mybiz.amocrm.ru/api/v4/leads/888"
    assert kwargs["json"]["custom_fields_values"] == [
        {"field_id": 123456, "values": [{"value": "Premium"}]}
    ]


async def test_set_custom_fields_select_uses_enum_id_slot():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response({})], calls))
    await provider.set_custom_fields(
        _conn(), "888", {"740941": {"value": 1308885, "type": "select"}}
    )
    _, kwargs = calls[0]
    assert kwargs["json"]["custom_fields_values"] == [
        {"field_id": 740941, "values": [{"enum_id": 1308885}]}
    ]


async def test_set_custom_fields_multiselect_lists_enum_ids():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response({})], calls))
    await provider.set_custom_fields(
        _conn(), "888", {"5": {"value": [10, 11], "type": "multiselect"}}
    )
    _, kwargs = calls[0]
    assert kwargs["json"]["custom_fields_values"] == [
        {"field_id": 5, "values": [{"enum_id": 10}, {"enum_id": 11}]}
    ]


async def test_set_custom_fields_checkbox_uses_value_slot():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response({})], calls))
    await provider.set_custom_fields(
        _conn(), "888", {"9": {"value": True, "type": "checkbox"}}
    )
    _, kwargs = calls[0]
    assert kwargs["json"]["custom_fields_values"] == [
        {"field_id": 9, "values": [{"value": True}]}
    ]


async def test_set_custom_fields_empty_is_noop_ok():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([], calls))
    assert await provider.set_custom_fields(_conn(), "888", {}) == "ok"
    assert calls == []


async def test_set_custom_fields_default_is_unsupported():
    class _Bare(CrmProvider):
        def oauth_authorize_url(self, *, state, redirect_uri):
            return ""

        async def oauth_exchange(self, callback):
            ...

        async def refresh(self, conn):
            ...

    assert await _Bare().set_custom_fields(SimpleNamespace(), "1", {}) == "unsupported"


async def test_update_lead_stage_falls_back_to_first_active_and_returns_stage():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_response({})], calls))
    view = {"pipeline_id": "222", "stage_map": {}, "snapshot_statuses": [
        {"stage_id": "301", "kind": "active", "sort": 10},
        {"stage_id": "142", "kind": "won", "sort": 100}]}
    pushed = await provider.update_lead_stage(_conn(), "888", role="negotiation", config=view)
    assert pushed == "301"                       # fell back to the first active stage
    _, kwargs = calls[0]
    assert kwargs["json"] == {"status_id": 301, "pipeline_id": 222}


async def test_update_lead_stage_noop_when_no_active_stage():
    calls: list = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([], calls))
    view = {"pipeline_id": "222", "stage_map": {}, "snapshot_statuses": [
        {"stage_id": "142", "kind": "won", "sort": 100}]}
    pushed = await provider.update_lead_stage(_conn(), "888", role="new", config=view)
    assert pushed == ""                          # nothing safe to push (no int("") crash)
    assert calls == []


# --- (S4c diagnostics) amoCRM API errors must log status + body --------------
# The worker only logged error=HTTPStatusError (class name), so a 400's amoCRM
# validation body was invisible. _api must surface it at the choke point.


async def test_api_logs_status_and_body_on_4xx(caplog):
    import logging

    err_response = MagicMock()
    err_response.status_code = 400
    err_response.text = '{"detail":"Field 740941: value not in enum list"}'
    err_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "400 Bad Request", request=MagicMock(), response=err_response
    )
    provider = AmoCrmProvider(http_client_factory=_client_factory([err_response]))
    with caplog.at_level(logging.WARNING):
        with pytest.raises(httpx.HTTPStatusError):
            await provider._api(_conn(), "patch", "/contacts/888", json={"x": 1})
    assert "amocrm api error" in caplog.text
    assert "status=400" in caplog.text
    assert "740941" in caplog.text  # the amoCRM validation body is logged
