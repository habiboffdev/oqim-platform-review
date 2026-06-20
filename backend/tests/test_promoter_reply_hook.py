"""mark_outreach_replied — a customer reply ends the promoter's job for that person."""

from __future__ import annotations

import pytest

from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection
from app.models.customer import Customer
from app.models.outreach import OutreachCampaign, OutreachTarget
from app.modules.bi_promoter.reply_hook import mark_outreach_replied

pytestmark = pytest.mark.asyncio


async def _conn(db_session, workspace, token, *, ref="mybiz"):
    """One active CRM connection per workspace — shared across campaigns."""
    conn = CrmConnection(workspace_id=workspace.id, provider="amocrm", status="active",
                         provider_account_ref=ref, webhook_token=token, pipeline_config={})
    db_session.add(conn)
    await db_session.flush()
    return conn


async def _campaign(db_session, workspace, conn, *, name="Iyun"):
    c = OutreachCampaign(workspace_id=workspace.id, connection_id=conn.id, name=name,
                         goal="reactivate", segment_spec={}, base_message="Salom",
                         caps={}, status="running")
    db_session.add(c)
    await db_session.flush()
    return c


async def _target(db_session, campaign, phone, *, state, key, conversation_id=None):
    t = OutreachTarget(campaign_id=campaign.id, workspace_id=campaign.workspace_id,
                       provider_contact_id="5", phone=phone, display_name="Ali",
                       tier="warm", state=state, idempotency_key=key,
                       conversation_id=conversation_id)
    db_session.add(t)
    await db_session.flush()
    return t


async def _conversation(db_session, workspace, phone):
    cust = Customer(workspace_id=workspace.id, display_name="Ali", phone_number=phone)
    db_session.add(cust)
    await db_session.flush()
    conv = Conversation(workspace_id=workspace.id, customer_id=cust.id,
                        channel="telegram_dm", pipeline_stage="new")
    db_session.add(conv)
    await db_session.flush()
    return cust, conv


async def test_reply_flips_sent_and_pending_targets(db_session, workspace):
    conn = await _conn(db_session, workspace, "wh-r1")
    campaign = await _campaign(db_session, workspace, conn, name="Iyun")
    other_campaign = await _campaign(db_session, workspace, conn, name="Avgust")
    cust, conv = await _conversation(db_session, workspace, "+998901112233")
    sent = await _target(db_session, campaign, cust.phone_number, state="sent",
                         key="r1", conversation_id=conv.id)
    # same person queued in ANOTHER campaign — matched by phone, retired too
    pending_same_phone = await _target(db_session, other_campaign, cust.phone_number,
                                       state="pending", key="r2")
    skipped = await _target(db_session, campaign, "+998909990000", state="skipped", key="r3",
                            conversation_id=conv.id)

    flipped = await mark_outreach_replied(
        db_session, workspace_id=workspace.id, conversation_id=conv.id,
        phone=cust.phone_number)

    assert flipped == 2
    for t in (sent, pending_same_phone, skipped):
        await db_session.refresh(t)
    assert sent.state == "replied" and sent.reply_at is not None
    assert pending_same_phone.state == "replied"
    assert skipped.state == "skipped"  # terminal — untouched


async def test_reply_without_phone_matches_by_conversation_only(db_session, workspace):
    conn = await _conn(db_session, workspace, "wh-r2", ref="mybiz2")
    campaign = await _campaign(db_session, workspace, conn)
    cust, conv = await _conversation(db_session, workspace, "+998901112233")
    sent = await _target(db_session, campaign, cust.phone_number, state="sent",
                         key="r4", conversation_id=conv.id)
    flipped = await mark_outreach_replied(
        db_session, workspace_id=workspace.id, conversation_id=conv.id, phone=None)
    assert flipped == 1
    await db_session.refresh(sent)
    assert sent.state == "replied"


async def test_reply_flips_sending_and_failed_targets(db_session, workspace):
    conn = await _conn(db_session, workspace, "wh-r4", ref="mybiz4")
    campaign = await _campaign(db_session, workspace, conn)
    cust, conv = await _conversation(db_session, workspace, "+998901112233")
    sending = await _target(db_session, campaign, cust.phone_number, state="sending",
                            key="s1", conversation_id=conv.id)
    failed = await _target(db_session, campaign, "+998905556677", state="failed",
                           key="f1", conversation_id=conv.id)
    flipped = await mark_outreach_replied(
        db_session, workspace_id=workspace.id, conversation_id=conv.id, phone=cust.phone_number)
    assert flipped == 2
    for t in (sending, failed):
        await db_session.refresh(t)
    assert sending.state == "replied"
    assert failed.state == "replied"


async def test_other_workspace_is_isolated(db_session, workspace, workspace_b):
    conn_b = await _conn(db_session, workspace_b, "wh-r3", ref="mybiz3")
    campaign_b = await _campaign(db_session, workspace_b, conn_b)
    foreign = await _target(db_session, campaign_b, "+998901112233", state="sent", key="r5")
    cust, conv = await _conversation(db_session, workspace, "+998901112233")

    await mark_outreach_replied(
        db_session, workspace_id=workspace.id, conversation_id=conv.id,
        phone=cust.phone_number)

    await db_session.refresh(foreign)
    assert foreign.state == "sent"
