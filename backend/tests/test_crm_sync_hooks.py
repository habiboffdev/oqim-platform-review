"""CRM deterministic-hook wiring (on_turn_facts stage advance from a fresh session).

After slice 4/5 the records pass is the sole CRM writer: it synthesizes the turn's
facts from the recorded payload and calls ``CrmSyncService.on_turn_facts`` directly
(the old ``apply_crm_turn_facts`` non-fatal wrapper is gone). These tests pin the
stage-advance behavior at that direct call site."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.models.customer import Customer
from app.modules.crm_connector.sync_service import CrmSyncService

pytestmark = pytest.mark.asyncio


async def _seed_link(db_session, workspace):
    conn = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status="active",
        provider_account_ref="mybiz",
        webhook_token="tok-1",
        pipeline_config={},
    )
    cust = Customer(workspace_id=workspace.id, display_name="Ali", contact_type="customer")
    db_session.add_all([conn, cust])
    await db_session.flush()
    conv = Conversation(
        workspace_id=workspace.id, customer_id=cust.id, channel="telegram_dm", pipeline_stage="new"
    )
    db_session.add(conv)
    await db_session.flush()
    await CrmSyncService(db_session).ensure_lead_link(
        workspace=workspace, conversation=conv, customer=cust
    )
    link = (await db_session.execute(select(CrmLeadLink))).scalars().first()
    return conv, link


async def test_on_turn_facts_advances_on_handoff_facts(db_session, workspace):
    conv, link = await _seed_link(db_session, workspace)
    await CrmSyncService(db_session).on_turn_facts(
        workspace_id=workspace.id,
        conversation_id=conv.id,
        facts={"handoff_recorded": "lead"},
    )
    await db_session.refresh(link)
    assert link.desired_stage_role == "qualified"


async def test_on_turn_facts_noop_on_empty_facts(db_session, workspace):
    # The records pass synthesizes empty facts when the turn changed nothing — the
    # stage must NOT advance (empty facts map to role 'new', which never out-ranks).
    conv, link = await _seed_link(db_session, workspace)
    await CrmSyncService(db_session).on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={}
    )
    await db_session.refresh(link)
    assert link.desired_stage_role == "new"  # unchanged
