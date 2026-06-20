"""Turn state reducer: facts from committed events. Pure — no DB, no I/O.

Spec: docs/superpowers/specs/2026-06-10-conversation-state-handoff-design.md.
The host only records; nothing here may initiate a business action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Deterministic mechanical extraction (bookkeeping, not interpretation):
# 9+ digit runs with optional separators = phone; standard email shape.
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-()]{7,}\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


@dataclass(frozen=True)
class TurnSignals:
    """What committedly happened in one turn."""

    reply_delivered: bool = False
    handoff_kinds: list[str] = field(default_factory=list)
    intelligence: list[dict[str, Any]] = field(default_factory=list)
    customer_texts: list[str] = field(default_factory=list)


def reduce_facts(previous: dict[str, Any], signals: TurnSignals) -> dict[str, Any]:
    """Accrue facts: any order, no regression, replay-idempotent."""
    facts: dict[str, Any] = dict(previous or {})

    if signals.reply_delivered:
        facts["engaged"] = True
    for payload in signals.intelligence:
        if payload.get("buying_signals"):
            facts["buying_signal_seen"] = True
        if payload.get("opted_out") is True:
            facts["opted_out"] = True
    if any(
        (_PHONE_RE.search(text) and sum(ch.isdigit() for ch in text) >= 9)
        or _EMAIL_RE.search(text)
        for text in signals.customer_texts
    ):
        facts["contact_captured"] = True
    if signals.handoff_kinds:
        facts["handoff_recorded"] = signals.handoff_kinds[-1]
    return facts


def stage_label(facts: dict[str, Any]) -> str:
    """Display label — a projection over facts, NOT stored state. Delegates to the
    canonical funnel so it can never diverge from the CRM role view (#426). No
    ``closed_won``/``closed_lost``: outcome was never produced (won/lost are
    human-owned in the CRM; see S3 / #422)."""
    from app.modules.agent_conversation_state.funnel import display_label, funnel_stage
    return display_label(funnel_stage(facts))
