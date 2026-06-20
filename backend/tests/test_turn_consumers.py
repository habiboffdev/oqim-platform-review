"""Turn-consumer registry (#424 S5).

The seam tests pin the registry mechanism with fake consumers (ordering, shared
context, per-consumer isolation). The integration test drives the *real* registry
end-to-end so the reducer-then-CRM DB effect matches the old hand-wired fan-out.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection, CrmLeadLink
from app.models.customer import Customer
from app.modules.agent_runtime_v2.turn_consumers import (
    InboundContext,
    TurnContext,
    finalize_turn,
    on_inbound_message,
)
from app.modules.agent_sessions.service import AgentSessionService

pytestmark = pytest.mark.asyncio


def _ctx(**kw) -> TurnContext:
    base = dict(
        db=None,
        workspace_id=1,
        conversation_id=2,
        customer_id=3,
        agent_id=4,
        agent_session_id=5,
        hermes_run_id="run-x",
    )
    base.update(kw)
    return TurnContext(**base)


# --- seam: the registry mechanism (fake consumers, no DB) ---------------------


async def test_finalize_turn_runs_consumers_in_registry_order():
    calls: list[str] = []

    async def a(ctx):
        calls.append("a")

    async def b(ctx):
        calls.append("b")

    await finalize_turn(_ctx(), consumers=[("a", a), ("b", b)])
    assert calls == ["a", "b"]


async def test_finalize_turn_shares_one_context_across_consumers():
    """Every consumer sees the SAME ``TurnContext`` object — a producer's mutation
    is visible to a later consumer (the registry walks one shared ctx, not copies)."""
    seen: dict = {}

    async def producer(ctx):
        ctx.handoff_kinds.append("lead")

    async def reader(ctx):
        seen["v"] = list(ctx.handoff_kinds)

    await finalize_turn(_ctx(), consumers=[("producer", producer), ("reader", reader)])
    assert seen["v"] == ["lead"]


async def test_finalize_turn_isolates_consumer_failures():
    """One consumer raising is logged-and-continued — it never aborts the others or
    the turn (a registry guarantee, not a shared try/except)."""
    ran: list[str] = []

    async def boom(ctx):
        raise RuntimeError("consumer crashed")

    async def ok(ctx):
        ran.append("ok")

    # must NOT raise; the second consumer still runs
    await finalize_turn(_ctx(), consumers=[("boom", boom), ("ok", ok)])
    assert ran == ["ok"]


# --- integration: the real default registry against a real DB -----------------


async def test_finalize_turn_default_registry_reduces_facts(
    db_session, workspace, conversation, customer, agent
):
    """The real default registry (post-slice-4) is just the facts reducer: it
    derives + persists the turn's ``{facts: {...}}`` snapshot row. The CRM stage
    advance + promoter opt-out are no longer pre-commit consumers — they run
    post-commit via the records pass (covered end-to-end in
    tests/test_records_pass.py), so this test only pins the surviving reducer's DB
    write."""
    from app.models.agent_conversation_state import AgentConversationStateSnapshot

    session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel=conversation.channel,
    )

    ctx = TurnContext(
        db=db_session,
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        agent_session_id=session.id,
        hermes_run_id="run-finalize-1",
        committed_action_refs=["handoff:lead"],
        handoff_kinds=["lead"],
        reply_delivered=True,
    )
    await finalize_turn(ctx)

    # reducer ran → it persisted the turn's ``{facts: {...}}`` snapshot row (the
    # seed-role / UI stage_label projection reads this), no longer shared on ctx.
    snapshot = (
        await db_session.execute(
            select(AgentConversationStateSnapshot)
            .where(
                AgentConversationStateSnapshot.conversation_id == conversation.id,
                AgentConversationStateSnapshot.state.has_key("facts"),
            )
            .order_by(
                AgentConversationStateSnapshot.created_at.desc(),
                AgentConversationStateSnapshot.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    assert snapshot is not None
    assert snapshot.state["facts"]["handoff_recorded"] == "lead"


async def test_turn_consumers_registry_is_same_session_writes_only():
    """The pre-commit registry runs INSIDE the open dispatcher transaction, so it
    must contain ONLY same-session DB writes. After slice 4 that is just the facts
    reducer: the CRM stage sync + promoter opt-out pre-commit consumers were
    retired (the post-commit records pass is now the sole CRM/promoter writer).
    The forced records pass is NOT here — it re-invokes the engine (own-session
    deal_value write) and would deadlock on this transaction's row locks; it runs
    POST-commit via run_records_pass."""
    from app.modules.agent_runtime_v2.turn_consumers import TURN_CONSUMERS

    assert [name for name, _ in TURN_CONSUMERS] == ["reduce_facts"]


# --- inbound phase: seam + integration (#425 S6) ------------------------------


async def test_on_inbound_message_runs_consumers_in_order():
    calls: list[str] = []

    async def a(ctx):
        calls.append("a")

    async def b(ctx):
        calls.append("b")

    await on_inbound_message(
        InboundContext(db=None, workspace=None, conversation=None, customer=None),
        consumers=[("a", a), ("b", b)],
    )
    assert calls == ["a", "b"]


async def test_on_inbound_message_isolates_consumer_failures():
    ran: list[str] = []

    async def boom(ctx):
        raise RuntimeError("inbound consumer crashed")

    async def ok(ctx):
        ran.append("ok")

    await on_inbound_message(
        InboundContext(db=None, workspace=None, conversation=None, customer=None),
        consumers=[("boom", boom), ("ok", ok)],
    )
    assert ran == ["ok"]


async def test_on_inbound_message_default_registry_captures_lead(
    db_session, workspace
):
    """The real inbound registry: a first customer message creates the CRM lead
    link (ensure_lead_link), same DB effect as the old hand-wired persist hook."""
    conn = CrmConnection(
        workspace_id=workspace.id,
        provider="amocrm",
        status="active",
        provider_account_ref="biz",
        webhook_token="tok",
        pipeline_config={},
    )
    cust = Customer(workspace_id=workspace.id, display_name="Ali", contact_type="customer")
    db_session.add_all([conn, cust])
    await db_session.flush()
    conv = Conversation(
        workspace_id=workspace.id, customer_id=cust.id,
        channel="telegram_dm", pipeline_stage="new",
    )
    db_session.add(conv)
    await db_session.flush()

    await on_inbound_message(InboundContext(
        db=db_session, workspace=workspace, conversation=conv, customer=cust,
    ))

    link = (await db_session.execute(select(CrmLeadLink))).scalars().first()
    assert link is not None
    assert link.conversation_id == conv.id
