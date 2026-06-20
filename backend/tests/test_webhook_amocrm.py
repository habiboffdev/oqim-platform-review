"""amoCRM webhook route: token resolve, account binding, latch effects.

amoCRM CRM/Digital-Pipeline webhooks are UNSIGNED (x-www-form-urlencoded, no
X-Signature) — these tests POST with no signature header and assert the real
unsigned card-move latches `human`.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.models.customer import Customer

pytestmark = pytest.mark.asyncio

_FORM_CT = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest_asyncio.fixture
async def seeded(db_session, workspace):
    conn = CrmConnection(
        workspace_id=workspace.id, provider="amocrm", status="active",
        provider_account_ref="mybiz", webhook_token="wht-1", pipeline_config={},
    )
    db_session.add(conn)
    await db_session.flush()
    cust = Customer(workspace_id=workspace.id, display_name="Ali", contact_type="customer")
    db_session.add(cust)
    await db_session.flush()
    conv = Conversation(workspace_id=workspace.id, customer_id=cust.id,
                        channel="telegram_dm", pipeline_stage="new")
    db_session.add(conv)
    await db_session.flush()
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id, conversation_id=conv.id,
        customer_id=cust.id, provider_lead_id="200", last_synced_stage_id="1001",
        stage_authority="oqim",
    )
    db_session.add(link)
    await db_session.flush()
    return conn, link


async def test_unsigned_status_event_latches_human(app_with_fake_spine, db_session, seeded):
    """The real amoCRM request is UNSIGNED — no X-Signature header. It must 200
    and latch human (this is the regression that proves the wrong HMAC gate is
    gone)."""
    app, _ = app_with_fake_spine
    _conn, link = seeded
    body = b"account[subdomain]=mybiz&leads[status][0][id]=200&leads[status][0][status_id]=1002"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/webhook/amocrm/wht-1", content=body, headers=_FORM_CT,
        )
    assert resp.status_code == 200
    await db_session.refresh(link)
    assert link.last_observed_stage_id == "1002"
    assert link.stage_authority == "human"


async def test_unknown_token_404(app_with_fake_spine):
    app, _ = app_with_fake_spine
    body = b"account[subdomain]=mybiz&leads[status][0][id]=200"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/webhook/amocrm/nope", content=body, headers=_FORM_CT)
    assert resp.status_code == 404


async def test_degraded_connection_still_accepts(app_with_fake_spine, db_session, seeded):
    """A degraded (recoverable) connection must still ACK+apply — repeated 404s
    would make amoCRM DISABLE the webhook."""
    app, _ = app_with_fake_spine
    conn, link = seeded
    conn.status = "degraded"
    await db_session.flush()
    body = b"account[subdomain]=mybiz&leads[status][0][id]=200&leads[status][0][status_id]=1002"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/webhook/amocrm/wht-1", content=body, headers=_FORM_CT)
    assert resp.status_code == 200
    await db_session.refresh(link)
    assert link.stage_authority == "human"


async def test_account_mismatch_401(app_with_fake_spine, seeded):
    body = b"account[subdomain]=other&leads[status][0][id]=200&leads[status][0][status_id]=1002"
    app, _ = app_with_fake_spine
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/webhook/amocrm/wht-1", content=body, headers=_FORM_CT)
    assert resp.status_code == 401


async def test_note_two_level_nesting_accepts_without_latching(app_with_fake_spine, db_session, seeded):
    """amoCRM may doubly-nest the note key as leads[note][i][note][element_id].
    A note for a tracked lead must 200 (parser tolerates the shape) but must NOT
    latch human — OQIM's own first-contact note echoes back as note_lead."""
    app, _ = app_with_fake_spine
    _conn, link = seeded
    body = b"account[subdomain]=mybiz&leads[note][0][note][element_id]=200"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/webhook/amocrm/wht-1", content=body, headers=_FORM_CT)
    assert resp.status_code == 200
    await db_session.refresh(link)
    assert link.stage_authority == "oqim"


async def test_note_one_level_nesting_accepts_without_latching(app_with_fake_spine, db_session, seeded):
    """The flat leads[note][i][element_id] shape must keep being accepted (200) and
    must NOT latch."""
    app, _ = app_with_fake_spine
    _conn, link = seeded
    body = b"account[subdomain]=mybiz&leads[note][0][element_id]=200"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/webhook/amocrm/wht-1", content=body, headers=_FORM_CT)
    assert resp.status_code == 200
    await db_session.refresh(link)
    assert link.stage_authority == "oqim"


async def test_apply_error_still_acks_200(app_with_fake_spine, seeded, monkeypatch):
    app, _ = app_with_fake_spine

    class _Boom:
        def __init__(self, session):
            pass

        async def apply(self, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.api.routes.webhook_amocrm.CrmWebhookService", _Boom)
    body = b"account[subdomain]=mybiz&leads[status][0][id]=200&leads[status][0][status_id]=1002"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/webhook/amocrm/wht-1", content=body, headers=_FORM_CT)
    assert resp.status_code == 200
