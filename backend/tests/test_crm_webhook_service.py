"""CrmWebhookService: inbound amoCRM events latch human + record observed stage."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.models.customer import Customer
from app.modules.crm_connector.contracts import CrmStageEvent, CrmWebhookBatch
from app.modules.crm_connector.webhook_service import CrmWebhookService

pytestmark = pytest.mark.asyncio


async def _conn(db_session, workspace):
    conn = CrmConnection(
        workspace_id=workspace.id, provider="amocrm", status="active",
        provider_account_ref="mybiz", webhook_token="tok-1", pipeline_config={},
    )
    db_session.add(conn)
    await db_session.flush()
    return conn


async def _link(db_session, workspace, conn, *, provider_lead_id="200",
                last_synced_stage_id="1001", stage_authority="oqim",
                deal_value=None):
    cust = Customer(workspace_id=workspace.id, display_name="Ali", contact_type="customer")
    db_session.add(cust)
    await db_session.flush()
    conv = Conversation(workspace_id=workspace.id, customer_id=cust.id,
                        channel="telegram_dm", pipeline_stage="new",
                        deal_value=deal_value)
    db_session.add(conv)
    await db_session.flush()
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id, conversation_id=conv.id,
        customer_id=cust.id, provider_lead_id=provider_lead_id,
        last_synced_stage_id=last_synced_stage_id, stage_authority=stage_authority,
    )
    db_session.add(link)
    await db_session.flush()
    return link, conv


async def test_status_event_records_observed_and_latches_human(db_session, workspace):
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn)  # last_synced 1001
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("status_lead", "200", "1002")])
    n = await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(link)
    assert n == 1
    assert link.last_observed_stage_id == "1002"
    assert link.stage_authority == "human"


async def test_note_event_never_latches(db_session, workspace):
    """A note_lead event is no longer a takeover signal — OQIM's own first-contact
    note echoes back as note_lead, so it must NOT latch human."""
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn)  # oqim
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("note_lead", "200", author_id=0)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(link)
    assert link.stage_authority == "oqim"


async def test_human_authored_note_still_does_not_latch(db_session, workspace):
    """Design decision: a note is too weak a takeover signal, so note_lead NEVER
    latches — even a real human-authored note (author_id > 0). This pins the
    'never latch on notes' choice so a future 'latch human notes' regression fails
    here (the author=0 case alone can't distinguish the two designs)."""
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn)  # oqim
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("note_lead", "200", author_id=777)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(link)
    assert link.stage_authority == "oqim"


async def test_update_lead_latches_only_for_human_author(db_session, workspace):
    """OQIM's own Sum push echoes as update_lead with author 0 — must NOT latch.
    A human card edit (author > 0) MUST latch."""
    conn = await _conn(db_session, workspace)
    # OQIM's own echo (author 0): no latch
    link, _ = await _link(db_session, workspace, conn)
    oqim_echo = CrmWebhookBatch("mybiz", [CrmStageEvent("update_lead", "200", author_id=0)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=oqim_echo)
    await db_session.refresh(link)
    assert link.stage_authority == "oqim"
    # a real human edit (author > 0): latch
    human = CrmWebhookBatch("mybiz", [CrmStageEvent("update_lead", "200", author_id=777)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=human)
    await db_session.refresh(link)
    assert link.stage_authority == "human"


async def test_responsible_lead_latches_only_for_human_author(db_session, workspace):
    """OQIM setting the responsible user on create echoes as responsible_lead with
    author 0 — must NOT latch. A human reassign (author > 0) MUST latch."""
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn)
    oqim_echo = CrmWebhookBatch("mybiz", [CrmStageEvent("responsible_lead", "200", author_id=0)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=oqim_echo)
    await db_session.refresh(link)
    assert link.stage_authority == "oqim"
    human = CrmWebhookBatch("mybiz", [CrmStageEvent("responsible_lead", "200", author_id=42)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=human)
    await db_session.refresh(link)
    assert link.stage_authority == "human"


async def test_human_update_lead_rearms_link_for_dnc_check(db_session, workspace):
    """S4 DNC inbound: a human update_lead (author_id>0) re-arms the link
    (sync_state='pending') so the worker (which does HTTP) inspects the mapped
    do-not-contact field. DB-only here — the handler never makes a network call."""
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn)
    link.sync_state = "synced"  # start settled so the re-arm is observable
    await db_session.flush()
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("update_lead", "200", author_id=777)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(link)
    assert link.stage_authority == "human"
    assert link.sync_state == "pending"  # re-armed for the worker to inspect


async def test_update_lead_with_unknown_author_does_not_latch(db_session, workspace):
    """Fail-open: an update_lead whose author didn't parse (None) must NOT latch
    (we are fixing OVER-latching; a human stage move is still caught by status_lead)."""
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn)
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("update_lead", "200", author_id=None)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(link)
    assert link.stage_authority == "oqim"


async def test_untracked_lead_is_ignored(db_session, workspace):
    conn = await _conn(db_session, workspace)
    await _link(db_session, workspace, conn, provider_lead_id="200")
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("status_lead", "999", "1002")])
    n = await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    assert n == 0


async def test_status_echo_records_observed_without_latching(db_session, workspace):
    """OQIM's own stage echoed back (status_id == last_synced) records the observed
    stage but must NOT latch human."""
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn, last_synced_stage_id="1001")
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("status_lead", "200", "1001")])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(link)
    assert link.last_observed_stage_id == "1001"
    assert link.stage_authority == "oqim"


async def test_event_scoped_to_connection(db_session, workspace):
    """An event resolved against the wrong connection_id touches nothing."""
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn, provider_lead_id="200")
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("status_lead", "200", "1002")])
    n = await CrmWebhookService(db_session).apply(connection_id=conn.id + 999, batch=batch)
    await db_session.refresh(link)
    assert n == 0
    assert link.stage_authority == "oqim"


async def test_status_with_value_sets_deal_value_when_null(db_session, workspace):
    """A value carried on an event sets conversation.deal_value (when null) and
    link.synced_value to the same amount."""
    conn = await _conn(db_session, workspace)
    link, conv = await _link(db_session, workspace, conn, deal_value=None)
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("status_lead", "200", "1002", value=4900000)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(conv)
    await db_session.refresh(link)
    assert conv.deal_value == Decimal(4900000)
    assert link.synced_value == Decimal(4900000)


async def test_value_does_not_overwrite_existing_deal_value(db_session, workspace):
    """A pre-set deal_value is never overwritten by an inbound echo; synced_value
    stays untouched too (loop-safe)."""
    conn = await _conn(db_session, workspace)
    link, conv = await _link(db_session, workspace, conn, deal_value=Decimal(3000000))
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("status_lead", "200", "1002", value=4900000)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(conv)
    await db_session.refresh(link)
    assert conv.deal_value == Decimal(3000000)
    assert link.synced_value is None


async def test_zero_price_does_not_pin_deal_value(db_session, workspace):
    """A price of 0 (amoCRM's default for a no-Sum lead, sent on every stage move)
    must NOT set deal_value — otherwise a later real human-set price is locked out."""
    conn = await _conn(db_session, workspace)
    link, conv = await _link(db_session, workspace, conn, deal_value=None)
    zero = CrmWebhookBatch("mybiz", [CrmStageEvent("status_lead", "200", "1002", value=0)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=zero)
    await db_session.refresh(conv)
    await db_session.refresh(link)
    assert conv.deal_value is None
    assert link.synced_value is None
    # a later REAL price still captures
    real = CrmWebhookBatch("mybiz", [CrmStageEvent("status_lead", "200", "1002", value=4900000)])
    await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=real)
    await db_session.refresh(conv)
    assert conv.deal_value == Decimal(4900000)


async def test_update_contact_rearms_link_and_queues_dnc_marker(db_session, workspace):
    """S4b: a human update_contact (matched by provider_contact_id) re-arms the link
    + queues a dnc_recheck marker, WITHOUT latching (a contact edit isn't a lead-stage
    takeover) so the worker inspects the mapped do-not-contact field."""
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn)
    link.provider_contact_id = "C100"
    link.sync_state = "synced"
    await db_session.flush()
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("update_contact", "C100", author_id=777)])
    n = await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(link)
    assert n == 1
    assert link.sync_state == "pending"
    assert link.stage_authority == "oqim"  # contact edit does NOT latch the lead
    assert any(o.get("kind") == "dnc_recheck" for o in link.pending_field_ops)


async def test_update_contact_author_zero_is_ignored(db_session, workspace):
    """OQIM's own contact write (author 0, e.g. the DNC write-back) must not re-arm —
    prevents a self-trigger loop."""
    conn = await _conn(db_session, workspace)
    link, _ = await _link(db_session, workspace, conn)
    link.provider_contact_id = "C100"
    link.sync_state = "synced"
    await db_session.flush()
    batch = CrmWebhookBatch("mybiz", [CrmStageEvent("update_contact", "C100", author_id=0)])
    n = await CrmWebhookService(db_session).apply(connection_id=conn.id, batch=batch)
    await db_session.refresh(link)
    assert n == 0
    assert link.sync_state == "synced"
