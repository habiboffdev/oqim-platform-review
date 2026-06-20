"""Facts, not stages: chaos-proof, any order, no regression, replay-idempotent.

Founder principle: the host only RECORDS — outcome is never inferred from
chat; no fact ever triggers a host-initiated business action.
"""

from __future__ import annotations

import pytest

from app.modules.agent_conversation_state.reducer import (
    TurnSignals,
    reduce_facts,
    stage_label,
)
from app.modules.agent_conversation_state.service import AgentConversationStateService
from app.modules.agent_sessions.service import AgentSessionService


def test_facts_accrue_in_any_order_complaint_first():
    facts = reduce_facts(
        {},
        TurnSignals(handoff_kinds=["complaint"], reply_delivered=True),
    )
    assert facts["handoff_recorded"] == "complaint"
    # never qualified, never contact — and that is fine
    assert "customer_name_known" not in facts
    assert "is_support" not in facts  # S3 #422: is_support had no reader, removed
    assert stage_label(facts) == "handed_off"


def test_lead_turn_sets_judgment_and_mechanical_facts():
    signals = TurnSignals(
        reply_delivered=True,
        handoff_kinds=["lead"],
        intelligence=[{
            "lead_stage": "checkout",
            "buying_signals": ["raqam qoldirdi"],
            "owner_notes": ["Mijoz Jasur"],
            "next_best_action": "Call-markaz bog'lanadi",
            "customer_name": "Jasur",
            "need": "biznesni tizimlashtirish",
        }],
        customer_texts=["+998 90 163 52 07"],
    )
    facts = reduce_facts({}, signals)
    # S3 #422: record_intelligence never emits customer_name/need, so these facts
    # are unreachable and removed — the intelligence payload's customer_name/need
    # keys are ignored.
    assert "customer_name_known" not in facts
    assert "need_known" not in facts
    assert "is_support" not in facts
    assert facts["buying_signal_seen"] is True
    assert facts["contact_captured"] is True  # regex on persisted text
    assert facts["handoff_recorded"] == "lead"
    assert stage_label(facts) == "handed_off"


def test_facts_never_regress_and_replay_is_idempotent():
    first = reduce_facts({}, TurnSignals(reply_delivered=True, customer_texts=["+998901112233"]))
    assert first["contact_captured"] is True
    # a later quiet turn keeps every earlier fact
    second = reduce_facts(first, TurnSignals(reply_delivered=True, customer_texts=["rahmat"]))
    assert second["contact_captured"] is True
    # replaying the same signals changes nothing
    assert reduce_facts(second, TurnSignals(reply_delivered=True, customer_texts=["rahmat"])) == second


def test_outcome_is_never_inferred_from_chat():
    facts = reduce_facts(
        {},
        TurnSignals(reply_delivered=True, customer_texts=["kerak emas, sotib olmayman"]),
    )
    assert facts.get("outcome", "open") == "open"
    assert stage_label(facts) in ("engaged", "new")


def test_contact_regex_matches_phones_and_emails_only():
    yes = TurnSignals(customer_texts=["raqamim 90 163-52-07 deb yozing"])
    no = TurnSignals(customer_texts=["kurs 2026 yil 13-14 iyun kunlari bo'ladimi"])
    assert reduce_facts({}, yes)["contact_captured"] is True
    assert "contact_captured" not in reduce_facts({}, no)


def test_stage_label_funnel_order():
    assert stage_label({}) == "new"
    assert stage_label({"engaged": True}) == "engaged"
    assert stage_label({"engaged": True, "buying_signal_seen": True}) == "qualified"
    assert stage_label({"engaged": True, "contact_captured": True}) == "lead_captured"
    assert stage_label({"handoff_recorded": "lead"}) == "handed_off"


def test_stage_label_ignores_removed_outcome_won_lost():
    # S3 #422: order_paid/outcome="won" was never produced and outcome="lost" never
    # existed — the closed_won/closed_lost label paths are removed (won/lost are
    # human-owned in the CRM). A stray outcome key no longer changes the label.
    assert stage_label({"outcome": "won"}) == "new"
    assert stage_label({"handoff_recorded": "lead", "outcome": "lost"}) == "handed_off"


# ---------------------------------------------------------------------------
# Service: apply_turn_facts writes the derived snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_turn_facts_writes_snapshot_and_compact_state(
    db_session, workspace, conversation, customer, agent
):
    session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="telegram_dm",
    )
    service = AgentConversationStateService(db_session)

    snapshot = await service.apply_turn_facts(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:facts-1",
        signals=TurnSignals(
            reply_delivered=True,
            handoff_kinds=["lead"],
            customer_texts=["+998901635207"],
            intelligence=[{"next_best_action": "Call-markaz bog'lanadi", "owner_notes": ["Jasur"]}],
        ),
    )

    assert snapshot is not None
    assert snapshot.stage == "handed_off"
    assert snapshot.state["facts"]["contact_captured"] is True
    compact = await service.latest_compact_state(
        workspace_id=workspace.id, agent_session_id=session.id
    )
    assert compact  # next turn's <conversation_state> block now carries truth

    # unchanged facts -> no duplicate snapshot
    again = await service.apply_turn_facts(
        workspace_id=workspace.id,
        agent_session_id=session.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        hermes_run_id="hermes_run:facts-2",
        signals=TurnSignals(reply_delivered=True),
    )
    assert again is None


# NOTE (founder, 2026-06-10): deterministic phone-regex extraction from chat
# text into customer DATA was rejected as an anti-pattern — the AGENT records
# customer details explicitly via work.handoff(customer_name, customer_phone).
# The reducer's contact_captured regex above stays display-fact-only; it
# never mutates business data.


def test_opted_out_fact_accrues_from_intelligence_and_never_regresses():
    facts = reduce_facts({}, TurnSignals(intelligence=[{"opted_out": True}]))
    assert facts["opted_out"] is True
    # a later quiet turn never clears it (facts only accrue)
    facts2 = reduce_facts(facts, TurnSignals(intelligence=[{"opted_out": False}]))
    assert facts2["opted_out"] is True


def test_opted_out_not_set_by_default():
    facts = reduce_facts({}, TurnSignals(intelligence=[{"buying_signals": ["x"]}]))
    assert "opted_out" not in facts
