"""Promoter schema invariants — real DB."""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.crm_connection import CrmConnection
from app.models.customer import Customer
from app.models.outreach import OutreachCampaign, OutreachTarget

pytestmark = pytest.mark.asyncio


async def _conn(db_session, workspace):
    c = CrmConnection(workspace_id=workspace.id, provider="amocrm", status="active",
                      provider_account_ref="mybiz", webhook_token="wh-models", pipeline_config={})
    db_session.add(c)
    await db_session.flush()
    return c


async def _campaign(db_session, workspace, **over):
    conn_id = over.pop("connection_id", None)
    if conn_id is None:
        conn_id = (await _conn(db_session, workspace)).id
    c = OutreachCampaign(
        workspace_id=workspace.id, connection_id=conn_id,
        name="Iyun intake", goal="reactivate",
        segment_spec={"stage_ids": ["111"]}, base_message="Salom!", **over)
    db_session.add(c)
    await db_session.flush()
    return c


async def test_campaign_defaults(db_session, workspace):
    c = await _campaign(db_session, workspace)
    assert c.status == "draft"
    assert c.caps == {}  # overrides only; defaults live in PROMOTER_DEFAULT_CAPS


async def test_target_defaults_and_unique_per_phone(db_session, workspace):
    c = await _campaign(db_session, workspace)
    db_session.add(OutreachTarget(
        campaign_id=c.id, workspace_id=workspace.id,
        provider_contact_id="5", phone="+998901112233", display_name="Ali",
        tier="warm", idempotency_key="k1"))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        db_session.add(OutreachTarget(
            campaign_id=c.id, workspace_id=workspace.id,
            provider_contact_id="5", phone="+998901112233", display_name="Ali",
            tier="cold", idempotency_key="k2"))
        await db_session.flush()


async def test_target_state_defaults(db_session, workspace):
    c = await _campaign(db_session, workspace)
    t = OutreachTarget(campaign_id=c.id, workspace_id=workspace.id,
                       provider_contact_id="6", phone="+998900000000",
                       display_name="V", tier="cold", idempotency_key="k3")
    db_session.add(t)
    await db_session.flush()
    assert t.state == "pending"
    assert t.attempts == 0
    assert t.customer_id is None


async def test_customer_opted_out_defaults_false(db_session, workspace):
    cust = Customer(workspace_id=workspace.id, display_name="Ali")
    db_session.add(cust)
    await db_session.flush()
    assert cust.opted_out is False
