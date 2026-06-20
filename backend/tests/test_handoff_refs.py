"""Canonical handoff-ref parser (#421 S2 dedup).

One parser for ``handoff:{kind}`` source-refs, validated against HANDOFF_KINDS,
shared by the dispatcher (plural), the runtime context builder, and the
owner-control-bot service + worker (singular). Pure — no DB.
"""
from __future__ import annotations

from app.modules.agent_business_actions.service import (
    handoff_kind_from_refs,
    handoff_kinds_from_refs,
)


def test_handoff_kinds_from_refs_returns_all_known_kinds_in_order():
    refs = ["conversation:3", "handoff:lead", "hermes_run:x", "handoff:support"]
    assert handoff_kinds_from_refs(refs) == ["lead", "support"]


def test_handoff_kinds_from_refs_filters_unknown_kinds():
    # an unknown kind never reaches a consumer (canonical-kind authority)
    assert handoff_kinds_from_refs(["handoff:bogus", "handoff:lead"]) == ["lead"]


def test_handoff_kinds_from_refs_handles_none_and_empty():
    assert handoff_kinds_from_refs(None) == []
    assert handoff_kinds_from_refs([]) == []


def test_handoff_kind_from_refs_returns_first_known_kind():
    assert handoff_kind_from_refs(["handoff:complaint", "handoff:lead"]) == "complaint"


def test_handoff_kind_from_refs_none_when_absent():
    assert handoff_kind_from_refs(["conversation:3", "hermes_run:x"]) is None
    assert handoff_kind_from_refs(None) is None
