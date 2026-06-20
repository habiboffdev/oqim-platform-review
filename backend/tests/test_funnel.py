"""Canonical funnel: one facts→stage source of truth (#426 S7). Pure, no DB."""
from __future__ import annotations

import itertools

import pytest

from app.modules.agent_conversation_state.funnel import (
    FunnelStage,
    display_label,
    funnel_stage,
)


def test_funnel_stage_priority_highest_fact_wins():
    assert funnel_stage({}) is FunnelStage.NEW
    assert funnel_stage({"engaged": True}) is FunnelStage.ENGAGED
    assert funnel_stage({"buying_signal_seen": True}) is FunnelStage.QUALIFIED
    assert funnel_stage({"contact_captured": True}) is FunnelStage.LEAD_CAPTURED
    assert funnel_stage({"handoff_recorded": "lead"}) is FunnelStage.HANDED_OFF
    # contact_captured outranks buying_signal_seen (preserves stage_label order)
    assert funnel_stage({"buying_signal_seen": True, "contact_captured": True}) is FunnelStage.LEAD_CAPTURED
    # handoff dominates everything
    assert funnel_stage({"engaged": True, "buying_signal_seen": True,
                         "contact_captured": True, "handoff_recorded": "x"}) is FunnelStage.HANDED_OFF
    # opted_out is never read
    assert funnel_stage({"opted_out": True}) is FunnelStage.NEW
    assert funnel_stage({"opted_out": True, "engaged": True}) is FunnelStage.ENGAGED


def test_display_label_covers_every_stage():
    assert {display_label(s) for s in FunnelStage} == {
        "new", "engaged", "qualified", "lead_captured", "handed_off"}
    assert display_label(FunnelStage.HANDED_OFF) == "handed_off"


def test_crm_role_covers_every_stage_and_collapses_correctly():
    from app.modules.crm_connector.contracts import crm_role
    assert crm_role(FunnelStage.NEW) == "new"
    assert crm_role(FunnelStage.ENGAGED) == "new"            # engaged never advances CRM
    assert crm_role(FunnelStage.QUALIFIED) == "negotiation"
    assert crm_role(FunnelStage.LEAD_CAPTURED) == "negotiation"  # collapses with QUALIFIED
    assert crm_role(FunnelStage.HANDED_OFF) == "qualified"
    assert {crm_role(s) for s in FunnelStage} == {"new", "negotiation", "qualified"}


_FACT_KEYS = ["engaged", "buying_signal_seen", "opted_out", "contact_captured", "handoff_recorded"]


def _oracle_stage_label(f):
    if f.get("handoff_recorded"):
        return "handed_off"
    if f.get("contact_captured"):
        return "lead_captured"
    if f.get("buying_signal_seen"):
        return "qualified"
    if f.get("engaged"):
        return "engaged"
    return "new"


def _oracle_role(f):
    if f.get("handoff_recorded"):
        return "qualified"
    if f.get("buying_signal_seen") or f.get("contact_captured"):
        return "negotiation"
    return "new"


@pytest.mark.parametrize("present", [
    frozenset(c) for r in range(len(_FACT_KEYS) + 1) for c in itertools.combinations(_FACT_KEYS, r)
])
def test_delegated_views_match_oracle_over_full_powerset(present):
    from app.modules.agent_conversation_state.reducer import stage_label
    from app.modules.crm_connector.contracts import target_role_for_facts
    facts = {k: (True if k != "handoff_recorded" else "lead") for k in present}
    assert stage_label(facts) == _oracle_stage_label(facts)
    assert target_role_for_facts(facts) == _oracle_role(facts)
