"""amoCRM read methods for promoter segments."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.crm_connector.amocrm import AmoCrmProvider

pytestmark = pytest.mark.asyncio


class _Conn:
    provider_account_ref = "mybiz"
    access_token = "tok"


def _client_factory(responses, calls):
    queue = list(responses)

    async def _next(*args, **kwargs):
        calls.append((args, kwargs))
        return queue.pop(0)

    @asynccontextmanager
    async def _client(*args, **kwargs):
        c = MagicMock()
        c.get = AsyncMock(side_effect=_next)
        c.post = AsyncMock(side_effect=_next)
        yield c

    return _client


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


async def test_fetch_contacts_parses_name_and_phone():
    payload = {"_embedded": {"contacts": [
        {"id": 5, "name": "Ali", "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": "+998901112233"}]}]},
        {"id": 6, "name": "Vali", "custom_fields_values": None},
    ]}}
    calls = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_resp(payload)], calls))
    contacts = await provider.fetch_contacts(_Conn(), page=1)

    assert [c.contact_id for c in contacts] == ["5", "6"]
    assert contacts[0].name == "Ali"
    assert contacts[0].phone == "+998901112233"
    assert contacts[1].phone is None
    # paginated GET /contacts with page + limit, Bearer header, no token in params
    args, kwargs = calls[0]
    assert args[0].endswith("/api/v4/contacts")
    assert kwargs["params"]["page"] == 1
    assert "tok" not in str(kwargs.get("params"))


async def test_fetch_contacts_empty_returns_empty_list():
    calls = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_resp(None)], calls))
    assert await provider.fetch_contacts(_Conn(), page=9) == []


async def test_fetch_leads_by_stage_filters_and_embeds_contacts():
    payload = {"_embedded": {"leads": [
        {"id": 10, "name": "Deal", "status_id": 111,
         "_embedded": {"contacts": [{"id": 5}]}},
    ]}}
    calls = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_resp(payload)], calls))
    rows = await provider.fetch_leads_by_stage(_Conn(), pipeline_id="777", status_ids=["111"], page=1)

    assert rows[0].contact_id == "5"
    args, kwargs = calls[0]
    assert args[0].endswith("/api/v4/leads")
    # amoCRM filter DSL: filter[statuses][0][pipeline_id] / [status_id]
    params = kwargs["params"]
    assert params["filter[statuses][0][pipeline_id]"] == "777"
    assert params["filter[statuses][0][status_id]"] == "111"
    assert params["with"] == "contacts"


async def test_fetch_leads_by_stage_supports_multiple_statuses():
    payload = {"_embedded": {"leads": []}}
    calls = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_resp(payload)], calls))
    await provider.fetch_leads_by_stage(_Conn(), pipeline_id="777", status_ids=["111", "222"], page=1)
    params = calls[0][1]["params"]
    assert params["filter[statuses][0][status_id]"] == "111"
    assert params["filter[statuses][1][status_id]"] == "222"
    assert params["filter[statuses][1][pipeline_id]"] == "777"


async def test_fetch_contacts_by_ids_filters_by_id():
    payload = {"_embedded": {"contacts": [
        {"id": 5, "name": "Ali", "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": "+998901112233"}]}]},
    ]}}
    calls = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_resp(payload)], calls))
    contacts = await provider.fetch_contacts_by_ids(_Conn(), contact_ids=["5"])

    assert contacts[0].contact_id == "5"
    assert contacts[0].name == "Ali"
    assert contacts[0].phone == "+998901112233"
    args, kwargs = calls[0]
    assert args[0].endswith("/api/v4/contacts")
    assert kwargs["params"]["filter[id][0]"] == "5"


async def test_fetch_contacts_by_ids_chunks_over_50_ids():
    def _page(ids):
        return {"_embedded": {"contacts": [
            {"id": i, "name": f"C{i}", "custom_fields_values": None} for i in ids
        ]}}
    calls = []
    ids = [str(i) for i in range(1, 52)]  # 51 ids -> 2 chunks
    provider = AmoCrmProvider(http_client_factory=_client_factory(
        [_resp(_page(range(1, 51))), _resp(_page([51]))], calls))
    contacts = await provider.fetch_contacts_by_ids(_Conn(), contact_ids=ids)

    assert len(calls) == 2
    assert calls[0][1]["params"]["filter[id][49]"] == "50"   # first chunk carries 50 ids
    assert calls[1][1]["params"]["filter[id][0]"] == "51"    # second chunk starts fresh at index 0
    assert len(contacts) == 51


async def test_fetch_contacts_by_ids_empty_input_makes_no_http_call():
    calls = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([], calls))
    assert await provider.fetch_contacts_by_ids(_Conn(), contact_ids=[]) == []
    assert calls == []


async def test_fetch_last_contact_note_returns_latest_text():
    payload = {"_embedded": {"notes": [{"id": 1, "params": {"text": "HR kursi bilan qiziqqan"}}]}}
    calls = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_resp(payload)], calls))
    note = await provider.fetch_last_contact_note(_Conn(), contact_id="5")

    assert note == "HR kursi bilan qiziqqan"
    args, kwargs = calls[0]
    assert args[0].endswith("/api/v4/contacts/5/notes")
    assert kwargs["params"]["order[updated_at]"] == "desc"
    assert kwargs["params"]["limit"] == 1


async def test_fetch_last_contact_note_none_when_empty():
    provider = AmoCrmProvider(http_client_factory=_client_factory([_resp(None)], []))
    assert await provider.fetch_last_contact_note(_Conn(), contact_id="5") is None


async def test_add_contact_note_posts_common_note():
    calls = []
    provider = AmoCrmProvider(http_client_factory=_client_factory([_resp({})], calls))
    await provider.add_contact_note(_Conn(), contact_id="5", text="OQIM outreach: sent")
    args, kwargs = calls[0]
    assert args[0].endswith("/api/v4/contacts/5/notes")
    assert kwargs["json"] == [{"note_type": "common", "params": {"text": "OQIM outreach: sent"}}]
