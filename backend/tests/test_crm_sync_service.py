"""CrmSyncService — DB-only desired-state hooks (ensure link, monotonic stage)."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.agent_session import AgentSession
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.models.customer import Customer
from app.modules.crm_connector.sync_service import CrmSyncService

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


async def _customer(db_session, workspace, contact_type="customer", phone=None):
    cust = Customer(
        workspace_id=workspace.id,
        display_name="Ali",
        contact_type=contact_type,
        phone_number=phone,
    )
    db_session.add(cust)
    await db_session.flush()
    return cust


async def _conversation(db_session, workspace, customer, channel="telegram_dm"):
    conv = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        channel=channel,
        pipeline_stage="new",
    )
    db_session.add(conv)
    await db_session.flush()
    return conv


async def _count_links(db_session) -> int:
    return (
        await db_session.execute(select(func.count()).select_from(CrmLeadLink))
    ).scalar_one()


async def _links(db_session):
    return (await db_session.execute(select(CrmLeadLink))).scalars().all()


async def test_ensure_no_connection_no_row(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer)
    await CrmSyncService(db_session).ensure_lead_link(
        workspace=workspace, conversation=conv, customer=customer
    )
    assert await _count_links(db_session) == 0


async def test_ensure_creates_one_link_idempotent(db_session, workspace, customer):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    for _ in range(5):
        await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    links = await _links(db_session)
    assert len(links) == 1
    link = links[0]
    assert link.desired_stage_role == "new"
    assert link.sync_state == "pending"
    assert len(link.pending_notes) == 1


async def test_ensure_instagram_dm_accepted(db_session, workspace, customer):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer, channel="instagram_dm")
    await CrmSyncService(db_session).ensure_lead_link(
        workspace=workspace, conversation=conv, customer=customer
    )
    assert await _count_links(db_session) == 1


async def test_ensure_skips_supplier(db_session, workspace):
    await _conn(db_session, workspace)
    cust = await _customer(db_session, workspace, contact_type="supplier")
    conv = await _conversation(db_session, workspace, cust)
    await CrmSyncService(db_session).ensure_lead_link(
        workspace=workspace, conversation=conv, customer=cust
    )
    assert await _count_links(db_session) == 0


async def test_ensure_skips_non_dm_channel(db_session, workspace, customer):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer, channel="dm")
    await CrmSyncService(db_session).ensure_lead_link(
        workspace=workspace, conversation=conv, customer=customer
    )
    assert await _count_links(db_session) == 0


async def test_on_turn_facts_monotonic_advance(db_session, workspace, customer):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    link = (await _links(db_session))[0]

    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={"buying_signal_seen": True}
    )
    await db_session.refresh(link)
    assert link.desired_stage_role == "negotiation"
    assert link.sync_state == "pending"
    # creation breadcrumb + advance breadcrumb + the rich context note (#428)
    assert len(link.pending_notes) == 3

    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={"handoff_recorded": "lead"}
    )
    await db_session.refresh(link)
    assert link.desired_stage_role == "qualified"

    # negotiation-mapping facts after qualified → stays qualified (monotonic)
    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={"buying_signal_seen": True}
    )
    await db_session.refresh(link)
    assert link.desired_stage_role == "qualified"

    # facts mapping to "new" → no write at all
    notes_before = len(link.pending_notes)
    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={"engaged": True}
    )
    await db_session.refresh(link)
    assert link.desired_stage_role == "qualified"
    assert len(link.pending_notes) == notes_before


async def test_on_turn_facts_no_link_is_noop(db_session, workspace, customer):
    conv = await _conversation(db_session, workspace, customer)
    await CrmSyncService(db_session).on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={"handoff_recorded": "lead"}
    )
    assert await _count_links(db_session) == 0


async def test_degraded_link_rearmed_on_advance(db_session, workspace, customer):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    link = (await _links(db_session))[0]
    link.sync_state = "degraded"
    link.attempts = 8
    await db_session.flush()

    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={"buying_signal_seen": True}
    )
    await db_session.refresh(link)
    assert link.sync_state == "pending"
    assert link.attempts == 0


async def test_ensure_lead_link_backfills_stage_from_existing_facts(db_session, workspace, customer, agent):
    """A pre-existing conversation with handoff facts must NOT land at 'new'."""
    from app.models.agent_conversation_state import AgentConversationStateSnapshot
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    # Create a real AgentSession to satisfy the FK on AgentConversationStateSnapshot
    sess = AgentSession(
        workspace_id=workspace.id,
        conversation_id=conv.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="telegram_dm",
        session_key="test-sess-key",
        hermes_session_id="test-hermes-sess-1",
    )
    db_session.add(sess)
    await db_session.flush()
    # facts say the customer is already a qualified lead (handoff recorded)
    db_session.add(AgentConversationStateSnapshot(
        workspace_id=workspace.id,
        agent_session_id=sess.id,
        conversation_id=conv.id,
        agent_id=agent.id,
        stage="qualified",
        state={"facts": {"engaged": True, "buying_signal_seen": True,
                         "handoff_recorded": "lead"}},
        idempotency_key="snap-1",
    ))
    await db_session.flush()

    await CrmSyncService(db_session).ensure_lead_link(
        workspace=workspace, conversation=conv, customer=customer)
    link = (await _links(db_session))[0]
    assert link.desired_stage_role == "qualified"  # backfilled, not "new"


async def test_ensure_lead_link_backfill_ignores_newer_set_state_packet(
    db_session, workspace, customer, agent
):
    """S4 (#423): a NEWER ``conversation.set_state`` snapshot (which carries no
    ``facts`` key) must not hide the conversation's accrued facts from the CRM
    stage backfill. The lead must still land at 'qualified', not re-enter at 'new'.
    """
    from datetime import timedelta

    from app.db.base import utc_now
    from app.models.agent_conversation_state import AgentConversationStateSnapshot

    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    sess = AgentSession(
        workspace_id=workspace.id,
        conversation_id=conv.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="telegram_dm",
        session_key="test-sess-key",
        hermes_session_id="test-hermes-sess-1",
    )
    db_session.add(sess)
    await db_session.flush()

    base = utc_now()
    # 1) the facts snapshot (older) — already a qualified lead
    db_session.add(AgentConversationStateSnapshot(
        workspace_id=workspace.id,
        agent_session_id=sess.id,
        conversation_id=conv.id,
        agent_id=agent.id,
        stage="qualified",
        state={"facts": {"engaged": True, "buying_signal_seen": True,
                         "handoff_recorded": "lead"}},
        idempotency_key="snap-facts",
        created_at=base,
    ))
    # 2) a NEWER set_state packet — the real-world shape: no "facts" key
    db_session.add(AgentConversationStateSnapshot(
        workspace_id=workspace.id,
        agent_session_id=sess.id,
        conversation_id=conv.id,
        agent_id=agent.id,
        stage="unknown",
        state={"selected_items": [], "shown_prices": [], "customer_details": {},
               "payment": {}, "fulfillment": {}, "missing_authority": [],
               "next_best_action": None, "risk_flags": []},
        idempotency_key="snap-setstate",
        created_at=base + timedelta(seconds=5),
    ))
    await db_session.flush()

    await CrmSyncService(db_session).ensure_lead_link(
        workspace=workspace, conversation=conv, customer=customer)
    link = (await _links(db_session))[0]
    assert link.desired_stage_role == "qualified"  # facts survive the set_state row


# --- #428 amoCRM lead-context enrichment (rich note on advance/handoff) --------


async def _set_state_packet(db_session, workspace, conv, customer, agent, *, state, key):
    """Persist a set_state-shape snapshot (no 'facts' key) for note read-back."""
    from app.models.agent_conversation_state import AgentConversationStateSnapshot
    from app.models.agent_session import AgentSession
    sess = AgentSession(
        workspace_id=workspace.id, conversation_id=conv.id, customer_id=customer.id,
        agent_id=agent.id, channel="telegram_dm", session_key=f"sk-{key}",
        hermes_session_id=f"hs-{key}",
    )
    db_session.add(sess)
    await db_session.flush()
    db_session.add(AgentConversationStateSnapshot(
        workspace_id=workspace.id, agent_session_id=sess.id, conversation_id=conv.id,
        agent_id=agent.id, stage="checkout", state=state, idempotency_key=key,
    ))
    await db_session.flush()


def _ctx_notes(link, conv):
    return [n for n in link.pending_notes if n["key"].startswith(f"{conv.id}:ctx:")]


async def test_on_turn_facts_appends_rich_context_note_on_advance(
    db_session, workspace, customer, agent
):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    link = (await _links(db_session))[0]
    await _set_state_packet(
        db_session, workspace, conv, customer, agent,
        state={
            "selected_items": [{"title": "HR kursi"}],
            "shown_prices": [{"amount": 4900000, "currency": "UZS"}],
            "next_best_action": "to'lov havolasini yuborish",
        },
        key="setstate-rich",
    )

    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id,
        facts={"buying_signal_seen": True},
        intelligence=[{"objections": ["narx qimmat"], "owner_notes": ["ertaga"],
                       "next_best_action": ""}],
    )
    await db_session.refresh(link)
    assert link.desired_stage_role == "negotiation"
    ctx = _ctx_notes(link, conv)
    assert len(ctx) == 1
    text = ctx[0]["text"]
    assert "OQIM (Muzokara):" in text
    assert "Mahsulot: HR kursi" in text
    assert "Narx: 4 900 000 so'm" in text
    assert "E'tiroz: narx qimmat" in text
    assert "Keyingi qadam: to'lov havolasini yuborish" in text
    assert "Izoh: ertaga" in text
    # the legacy advance breadcrumb still coexists (distinct key namespace)
    assert any(n["key"] == f"{conv.id}:negotiation" for n in link.pending_notes)


async def test_on_turn_facts_handoff_without_advance_still_appends_note(
    db_session, workspace, customer
):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    link = (await _links(db_session))[0]
    # already at qualified, no prior context note
    link.desired_stage_role = "qualified"
    link.pending_notes = []
    await db_session.flush()

    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id,
        facts={"handoff_recorded": "lead"},  # target == current → NOT advanced, but handoff
        intelligence=[{"objections": ["operatorga ulang"]}],
    )
    await db_session.refresh(link)
    assert link.desired_stage_role == "qualified"  # no advance
    ctx = _ctx_notes(link, conv)
    assert len(ctx) == 1
    assert "OQIM (Malakali):" in ctx[0]["text"]
    assert "E'tiroz: operatorga ulang" in ctx[0]["text"]
    # no NEW advance breadcrumb on the non-advancing path
    assert not any(n["key"] == f"{conv.id}:qualified" for n in link.pending_notes)
    assert link.sync_state == "pending"  # re-armed so the worker drains the note


async def test_on_turn_facts_no_advance_no_handoff_appends_nothing(
    db_session, workspace, customer
):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    link = (await _links(db_session))[0]
    notes_before = len(link.pending_notes)

    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={"engaged": True}
    )
    await db_session.refresh(link)
    assert len(link.pending_notes) == notes_before  # nothing appended
    assert link.desired_stage_role == "new"


async def test_on_turn_facts_context_note_dedups_per_stage(
    db_session, workspace, customer
):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    link = (await _links(db_session))[0]
    link.desired_stage_role = "qualified"
    link.pending_notes = []
    await db_session.flush()

    for _ in range(2):
        await svc.on_turn_facts(
            workspace_id=workspace.id, conversation_id=conv.id,
            facts={"handoff_recorded": "lead"},
            intelligence=[{"objections": ["x"]}],
        )
    await db_session.refresh(link)
    assert len([n for n in link.pending_notes if n["key"] == f"{conv.id}:ctx:qualified"]) == 1


async def test_crm_lead_link_has_value_and_tasks_columns(db_session, workspace):
    conn = await _conn(db_session, workspace)
    cust = await _customer(db_session, workspace)
    conv = await _conversation(db_session, workspace, cust)
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id,
        conversation_id=conv.id, customer_id=cust.id,
    )
    db_session.add(link)
    await db_session.flush()
    await db_session.refresh(link)
    assert link.synced_value is None
    assert link.pending_tasks == []


# --- Slice A: deal_value + handoff task at the turn-facts hook -----------------


async def test_on_turn_facts_writes_deal_value_from_shown_price(
    db_session, workspace, customer, agent
):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    await _set_state_packet(
        db_session, workspace, conv, customer, agent,
        state={"selected_items": [], "shown_prices": [{"amount": 4900000, "currency": "UZS"}],
               "customer_details": {}, "next_best_action": ""},
        key="p1",
    )
    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id,
        facts={"handoff_recorded": True}, intelligence=None,
    )
    await db_session.refresh(conv)
    assert conv.deal_value == Decimal("4900000")


async def test_on_turn_facts_empty_facts_still_writes_deal_value(
    db_session, workspace, customer, agent
):
    # The forced finalize set_state records shown_prices on a steady-state turn where
    # the reducer facts did NOT change (empty facts — the headline live case: an
    # already-engaged lead the seller quotes a price to). deal_value MUST still be read
    # from the snapshot, with NO stage advance. (cross-seam review blocking fix)
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    await _set_state_packet(
        db_session, workspace, conv, customer, agent,
        state={"selected_items": [], "shown_prices": [{"amount": 9790000, "currency": "UZS"}],
               "customer_details": {}, "next_best_action": ""},
        key="p1",
    )
    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={},
    )
    await db_session.refresh(conv)
    assert conv.deal_value == Decimal("9790000")  # read despite empty facts
    link = (await _links(db_session))[0]
    assert link.desired_stage_role == "new"  # empty-facts turn never advances the stage


async def test_on_turn_facts_queues_one_handoff_task_deduped(
    db_session, workspace, customer, agent
):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    await _set_state_packet(
        db_session, workspace, conv, customer, agent,
        state={"selected_items": [], "shown_prices": [], "customer_details": {},
               "next_best_action": "qo'ng'iroq qiling"},
        key="p1",
    )
    for _ in range(2):  # two handoff turns must not double-queue
        await svc.on_turn_facts(
            workspace_id=workspace.id, conversation_id=conv.id,
            facts={"handoff_recorded": True}, intelligence=None,
        )
    link = (await _links(db_session))[0]
    handoff_tasks = [t for t in link.pending_tasks if t.get("key", "").endswith(":task:handoff")]
    assert len(handoff_tasks) == 1
    assert "bog'laning" in handoff_tasks[0]["text"]


async def test_on_turn_facts_no_price_leaves_deal_value_none(
    db_session, workspace, customer, agent
):
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    await _set_state_packet(
        db_session, workspace, conv, customer, agent,
        state={"selected_items": [], "shown_prices": [], "customer_details": {}, "next_best_action": ""},
        key="p1",
    )
    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id,
        facts={"handoff_recorded": True}, intelligence=None,
    )
    await db_session.refresh(conv)
    assert conv.deal_value is None


async def test_on_turn_facts_writes_deal_value_without_advance_or_handoff(
    db_session, workspace, customer, agent
):
    """Spec §5.2 'any stage': a price revision on a steady-state lead (no stage
    advance, no handoff) must still write deal_value and re-arm the worker."""
    await _conn(db_session, workspace)
    conv = await _conversation(db_session, workspace, customer)
    svc = CrmSyncService(db_session)
    await svc.ensure_lead_link(workspace=workspace, conversation=conv, customer=customer)
    link = (await _links(db_session))[0]
    link.desired_stage_role = "qualified"  # already advanced earlier
    link.sync_state = "synced"
    await db_session.flush()
    await _set_state_packet(
        db_session, workspace, conv, customer, agent,
        state={"selected_items": [], "shown_prices": [{"amount": 4410000, "currency": "UZS"}],
               "customer_details": {}, "next_best_action": ""},
        key="p2",
    )
    await svc.on_turn_facts(
        workspace_id=workspace.id, conversation_id=conv.id, facts={}, intelligence=None
    )
    await db_session.refresh(conv)
    await db_session.refresh(link)
    assert conv.deal_value == Decimal("4410000")
    assert link.sync_state == "pending"  # re-armed so the worker pushes the revised price


async def _conn_with_config(db_session, workspace, config):
    conn = CrmConnection(
        workspace_id=workspace.id, provider="amocrm", status="active",
        provider_account_ref="mybiz", webhook_token="tok-cfg", pipeline_config=config,
    )
    db_session.add(conn)
    await db_session.flush()
    return conn


async def test_ensure_lead_link_pins_default_pipeline(db_session, workspace, customer):
    await _conn_with_config(
        db_session, workspace,
        {"pipeline_id": "777", "stage_map": {"new": {"stage_id": "1001", "sort": 10}}},
    )
    conv = await _conversation(db_session, workspace, customer)
    await CrmSyncService(db_session).ensure_lead_link(
        workspace=workspace, conversation=conv, customer=customer
    )
    link = (await _links(db_session))[0]
    assert link.pipeline_id == "777"     # captured from the connection's default pipeline


async def _routing_conn(db_session, workspace, *, default="111"):
    from app.models.crm_connection import CrmConnection
    conn = CrmConnection(
        workspace_id=workspace.id, provider="amocrm", status="active",
        provider_account_ref=f"acct-{workspace.id}", webhook_token=f"wt-{workspace.id}",
        pipeline_config={
            "schema_version": 2,
            "snapshot": {"pipelines": [{"id": "111"}, {"id": "222"}]},
            "mapping": {"default_pipeline_id": default, "pipelines": {}},
        })
    db_session.add(conn)
    await db_session.flush()
    return conn


async def _routing_link(db_session, workspace, customer, conn, *, pipeline_id="111",
                        role="new", authority="oqim"):
    from app.models.crm_connection import CrmLeadLink
    link = CrmLeadLink(
        workspace_id=workspace.id, connection_id=conn.id, conversation_id=1,
        customer_id=customer.id, pipeline_id=pipeline_id, desired_stage_role=role,
        stage_authority=authority, sync_state="synced", attempts=0)
    db_session.add(link)
    await db_session.flush()
    return link


async def test_route_lead_moves_from_default(db_session, workspace, customer):
    from app.modules.crm_connector.sync_service import CrmSyncService
    conn = await _routing_conn(db_session, workspace)
    link = await _routing_link(db_session, workspace, customer, conn)
    status = await CrmSyncService(db_session).route_lead(
        workspace_id=workspace.id, conversation_id=1, target_pipeline_id="222")
    assert status == "moved"
    assert link.pipeline_id == "222"
    assert link.sync_state == "pending"   # rearmed for the worker to push


async def test_route_lead_guards(db_session, workspace, customer):
    from app.modules.crm_connector.sync_service import CrmSyncService

    def svc():
        return CrmSyncService(db_session)

    conn = await _routing_conn(db_session, workspace)
    link = await _routing_link(db_session, workspace, customer, conn)
    assert await svc().route_lead(workspace_id=workspace.id, conversation_id=1,
                                  target_pipeline_id="111") == "noop"     # target == default
    assert await svc().route_lead(workspace_id=workspace.id, conversation_id=1,
                                  target_pipeline_id="999") == "vanished"  # not in snapshot
    link.stage_authority = "human"
    await db_session.flush()
    assert await svc().route_lead(workspace_id=workspace.id, conversation_id=1,
                                  target_pipeline_id="222") == "latched"
    assert link.pipeline_id == "111"


async def test_route_lead_route_once_and_terminal(db_session, workspace, customer):
    from app.modules.crm_connector.sync_service import CrmSyncService
    conn = await _routing_conn(db_session, workspace)
    link = await _routing_link(db_session, workspace, customer, conn, pipeline_id="222")
    assert await CrmSyncService(db_session).route_lead(
        workspace_id=workspace.id, conversation_id=1, target_pipeline_id="222") in ("noop", "routed")
    link.pipeline_id = "111"
    link.desired_stage_role = "won"
    await db_session.flush()
    assert await CrmSyncService(db_session).route_lead(
        workspace_id=workspace.id, conversation_id=1, target_pipeline_id="222") == "terminal"
    assert link.pipeline_id == "111"
