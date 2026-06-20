"""Canonical funnel — the single facts→stage projection (#426 S7).

stage_label (display) and target_role_for_facts (CRM) used to project the same
facts in two vocabularies that could silently drift. They now both derive from
funnel_stage; this module owns the facts→stage decision + the display view, and
crm_connector owns the CRM-role view (both keyed by FunnelStage).
"""
from __future__ import annotations

from enum import IntEnum


class FunnelStage(IntEnum):
    """Ordered funnel position (higher = further along). The order is stage_label's
    verified priority: handoff > contact_captured > buying_signal > engaged > none."""

    NEW = 0
    ENGAGED = 1
    QUALIFIED = 2
    LEAD_CAPTURED = 3
    HANDED_OFF = 4


def funnel_stage(facts: dict) -> FunnelStage:
    """The single facts→stage projection. Highest-priority present fact wins."""
    if facts.get("handoff_recorded"):
        return FunnelStage.HANDED_OFF
    if facts.get("contact_captured"):
        return FunnelStage.LEAD_CAPTURED
    if facts.get("buying_signal_seen"):
        return FunnelStage.QUALIFIED
    if facts.get("engaged"):
        return FunnelStage.ENGAGED
    return FunnelStage.NEW


_DISPLAY = {
    FunnelStage.NEW: "new",
    FunnelStage.ENGAGED: "engaged",
    FunnelStage.QUALIFIED: "qualified",
    FunnelStage.LEAD_CAPTURED: "lead_captured",
    FunnelStage.HANDED_OFF: "handed_off",
}


def display_label(stage: FunnelStage) -> str:
    """The UI/telemetry display label for a funnel stage."""
    return _DISPLAY[stage]
