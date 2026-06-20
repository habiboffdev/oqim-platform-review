"""apply_promoter_turn_facts — opted_out propagation (DB-only, mirrors crm hook)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.base import utc_now
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.models.customer import Customer
from app.models.outreach import OutreachCampaign, OutreachTarget
from app.modules.bi_promoter.fact_hooks import apply_promoter_turn_facts

pytestmark = pytest.mark.asyncio


async def _conn(db_session, workspace, token="wh-fact"):
    c = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status="active",
        provider_account_ref="mybiz",
        webhook_token=token,
        pipeline_config={},
    )
    db_session.add(c)
    await db_session.flush()
    return c


async def _campaign(db_session, workspace, conn):
    c = OutreachCampaign(
        workspace_id=workspace.id,
        connection_id=conn.id,
        name="Iyun",
        goal="reactivate",
        segment_spec={},
        base_message="Salom",
        caps={},
        status="running",
    )
    db_session.add(c)
    await db_session.flush()
    return c


async def _person(db_session, workspace, phone="+998901112233"):
    cust = Customer(workspace_id=workspace.id, display_name="Ali", phone_number=phone)
    db_session.add(cust)
    await db_session.flush()
    conv = Conversation(
        workspace_id=workspace.id,
        customer_id=cust.id,
        channel="telegram_dm",
        pipeline_stage="new",
    )
    db_session.add(conv)
    await db_session.flush()
    return cust, conv


async def _target(db_session, campaign, phone, *, state="pending", key):
    t = OutreachTarget(
        campaign_id=campaign.id,
        workspace_id=campaign.workspace_id,
        provider_contact_id="5",
        phone=phone,
        display_name="Ali",
        tier="warm",
        state=state,
        idempotency_key=key,
    )
    db_session.add(t)
    await db_session.flush()
    return t


def _result(facts):
    return SimpleNamespace(state={"facts": facts})


async def test_opted_out_sets_customer_and_retires_targets(db_session, workspace):
    conn = await _conn(db_session, workspace)
    campaign = await _campaign(db_session, workspace, conn)
    cust, conv = await _person(db_session, workspace)
    # pending target: matched by phone (no conversation_id set)
    pending = await _target(db_session, campaign, cust.phone_number, key="k1")
    # sent target: different phone but linked to this conversation
    sent = await _target(db_session, campaign, "+998905556677", key="k2", state="sent")
    sent.conversation_id = conv.id
    # replied target: different phone, linked to this conversation, but terminal
    replied = await _target(db_session, campaign, "+998907778899", key="k3", state="replied")
    replied.conversation_id = conv.id
    await db_session.flush()

    await apply_promoter_turn_facts(
        db_session, _result({"opted_out": True}),
        workspace_id=workspace.id, conversation_id=conv.id, customer_id=cust.id)

    await db_session.refresh(cust)
    assert cust.opted_out is True
    for t in (pending, sent, replied):
        await db_session.refresh(t)
    assert pending.state == "skipped"      # matched by phone
    assert pending.last_error == "opted_out"  # worker reads this diagnostically
    assert sent.state == "skipped"          # matched by conversation
    assert replied.state == "replied"       # terminal — untouched


async def test_opted_out_appends_crm_note_and_rearms_link(db_session, workspace):
    conn = await _conn(db_session, workspace)
    cust, conv = await _person(db_session, workspace)
    link = CrmLeadLink(
        workspace_id=workspace.id,
        connection_id=conn.id,
        conversation_id=conv.id,
        customer_id=cust.id,
        desired_stage_role="new",
        stage_authority="oqim",
        sync_state="synced",
        attempts=3,
        next_attempt_at=utc_now(),
        pending_notes=[],
    )
    db_session.add(link)
    await db_session.flush()

    await apply_promoter_turn_facts(
        db_session, _result({"opted_out": True}),
        workspace_id=workspace.id, conversation_id=conv.id, customer_id=cust.id)

    await db_session.refresh(link)
    assert any("opted out" in (n or {}).get("text", "").lower() for n in link.pending_notes)
    assert link.sync_state == "pending" and link.attempts == 0


async def test_noop_without_opted_out_or_result(db_session, workspace):
    cust, conv = await _person(db_session, workspace)
    await apply_promoter_turn_facts(
        db_session, None, workspace_id=workspace.id, conversation_id=conv.id, customer_id=cust.id)
    await apply_promoter_turn_facts(
        db_session, _result({"engaged": True}),
        workspace_id=workspace.id, conversation_id=conv.id, customer_id=cust.id)
    await db_session.refresh(cust)
    assert cust.opted_out is False


async def test_other_workspace_targets_untouched(db_session, workspace, workspace_b):
    conn_b = await _conn(db_session, workspace_b, token="wh-fact-b")
    campaign_b = await _campaign(db_session, workspace_b, conn_b)
    # same phone as the workspace-A customer, but in workspace B
    foreign = await _target(db_session, campaign_b, "+998901112233", key="k-b")
    cust, conv = await _person(db_session, workspace)  # same phone, workspace A

    await apply_promoter_turn_facts(
        db_session, _result({"opted_out": True}),
        workspace_id=workspace.id, conversation_id=conv.id, customer_id=cust.id)

    await db_session.refresh(foreign)
    assert foreign.state == "pending"
