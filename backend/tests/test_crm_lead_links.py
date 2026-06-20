"""Shared CRM lead-link helpers (#421 S2 dedup): one active-link lookup and one
re-arm, used by both the CRM sync service and the promoter opt-out hook."""
from __future__ import annotations

import pytest

from app.db.base import utc_now
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.modules.crm_connector.lead_links import active_lead_link, rearm_lead_link

pytestmark = pytest.mark.asyncio


async def _conn(db_session, workspace, status="active"):
    conn = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status=status,
        provider_account_ref="mybiz",
        webhook_token="tok-1",
        pipeline_config={},
    )
    db_session.add(conn)
    await db_session.flush()
    return conn


async def _conversation(db_session, workspace, customer):
    conv = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        channel="telegram_dm",
        pipeline_stage="new",
    )
    db_session.add(conv)
    await db_session.flush()
    return conv


async def _link(db_session, workspace, conn, conversation, customer, *,
                sync_state="degraded", attempts=8):
    link = CrmLeadLink(
        workspace_id=workspace.id,
        connection_id=conn.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        desired_stage_role="new",
        stage_authority="oqim",
        sync_state=sync_state,
        attempts=attempts,
        next_attempt_at=utc_now(),
        pending_notes=[],
    )
    db_session.add(link)
    await db_session.flush()
    return link


async def test_active_lead_link_found_when_connection_active(
    db_session, workspace, customer
):
    conn = await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    link = await _link(db_session, workspace, conn, conv, customer)
    found = await active_lead_link(
        db_session, workspace_id=workspace.id, conversation_id=conv.id
    )
    assert found is not None
    assert found.id == link.id


async def test_active_lead_link_none_when_connection_inactive(
    db_session, workspace, customer
):
    conn = await _conn(db_session, workspace, status="degraded")
    conv = await _conversation(db_session, workspace, customer)
    await _link(db_session, workspace, conn, conv, customer)
    found = await active_lead_link(
        db_session, workspace_id=workspace.id, conversation_id=conv.id
    )
    assert found is None


async def test_rearm_lead_link_resets_to_pending(db_session, workspace, customer):
    conn = await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    link = await _link(db_session, workspace, conn, conv, customer,
                       sync_state="degraded", attempts=8)
    rearm_lead_link(link)
    assert link.sync_state == "pending"
    assert link.attempts == 0
    assert link.next_attempt_at is not None
