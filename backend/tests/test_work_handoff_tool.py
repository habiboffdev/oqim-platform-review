"""work.handoff tool: validation, idempotency stem, ctx refs, schema triggers."""

from __future__ import annotations

import asyncio
import json

import app.modules.agent_runtime_v2.hermes.oqim_tools as oqim_tools
from app.modules.agent_runtime_v2.hermes.oqim_tools import (
    _WORK_HANDOFF_SCHEMA,
    work_handoff,
)
from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext, use_tool_context


def _ctx() -> ToolContext:
    return ToolContext(
        workspace_id=1,
        agent_id=2,
        conversation_id=3,
        agent_session_id=4,
        hermes_run_id="hermes_run:handoff-tool",
        grounding=[],
        history=[],
    )


def _run_coro_sync(_ctx, coro, *, error_prefix):
    """Drive the (monkeypatched) async op synchronously — repo test idiom."""
    return asyncio.run(coro)


def test_work_handoff_queues_atomic_handoff(monkeypatch) -> None:
    captured: dict = {}

    async def fake_async(**kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "kind": kwargs["kind"],
            "task_ref": "owner_task:abc",
            "notification_ref": "owner_notification:def",
        }

    monkeypatch.setattr(oqim_tools, "_work_handoff_async", fake_async)
    monkeypatch.setattr(oqim_tools, "_run_knowledge_coro", _run_coro_sync)
    ctx = _ctx()
    with use_tool_context(ctx):
        result = json.loads(
            work_handoff({"kind": "lead", "title": "Yangi lid: Jasur", "detail": "Raqam: +998..."})
        )

    assert result["status"] == "ok"
    assert captured["kind"] == "lead"
    assert captured["idempotency_key"].startswith("work.handoff:1:4:")
    assert "owner_task:abc" in ctx.business_action_refs
    assert "owner_notification:def" in ctx.business_action_refs
    # the dispatcher reads the kind off committed refs (spec: turn-state facts)
    assert "handoff:lead" in ctx.business_action_refs


def test_work_handoff_validates_inputs() -> None:
    ctx = _ctx()
    with use_tool_context(ctx):
        bad_kind = json.loads(work_handoff({"kind": "party", "title": "t", "detail": "d"}))
        missing = json.loads(work_handoff({"kind": "lead", "title": "", "detail": ""}))

    assert bad_kind["status"] == "blocked"
    assert bad_kind["reason"] == "invalid_kind"
    assert missing["status"] == "empty"


def test_work_handoff_schema_carries_trigger_list() -> None:
    description = _WORK_HANDOFF_SCHEMA["description"]
    # the strongest steering lever we have: triggers live in the schema
    assert "kind=lead" in description
    assert "kind=human_requested" in description
    assert "kind=complaint" in description
    assert "never promise" in description.lower()
    assert _WORK_HANDOFF_SCHEMA["parameters"]["required"] == [
        "kind", "title", "detail", "customer_name", "customer_phone",
    ]  # required so the lite model fills them; "" when truly unknown


def test_record_intelligence_payload_is_captured_on_ctx(monkeypatch) -> None:
    from app.modules.agent_runtime_v2.hermes.oqim_tools import conversation_record_intelligence

    async def fake_async(**kwargs):
        return {"status": "ok", "intelligence_ref": "customer_intelligence:x"}

    monkeypatch.setattr(
        oqim_tools, "_conversation_record_intelligence_async", fake_async
    )
    monkeypatch.setattr(oqim_tools, "_run_knowledge_coro", _run_coro_sync)
    ctx = _ctx()
    with use_tool_context(ctx):
        conversation_record_intelligence(
            {
                "lead_stage": "checkout",
                "buying_signals": ["raqam qoldirdi"],
                "owner_notes": ["Mijoz Jasur, IT sohasi"],
            }
        )

    assert ctx.intelligence_payloads
    assert ctx.intelligence_payloads[0]["buying_signals"] == ["raqam qoldirdi"]


def test_schema_tells_agent_to_rehandoff_returning_leads() -> None:
    """Founder call (2026-06-10): a handed-off customer who returns still
    waiting IS a new signal — really interested + operator never reached
    them. The agent must record a fresh handoff, not just reassure."""
    description = _WORK_HANDOFF_SCHEMA["description"]
    assert "comes back" in description or "returns" in description
    assert "call this again" in description.lower()


def test_work_handoff_passes_customer_details(monkeypatch) -> None:
    captured: dict = {}

    async def fake_async(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "kind": kwargs["kind"], "task_ref": "t", "notification_ref": "n"}

    monkeypatch.setattr(oqim_tools, "_work_handoff_async", fake_async)
    monkeypatch.setattr(oqim_tools, "_run_knowledge_coro", _run_coro_sync)
    with use_tool_context(_ctx()):
        work_handoff(
            {
                "kind": "lead",
                "title": "Yangi lid: Jasur",
                "detail": "Raqam berdi",
                "customer_name": "Jasur",
                "customer_phone": "+998 90 163 52 07",
            }
        )

    assert captured["customer_name"] == "Jasur"
    assert captured["customer_phone"] == "+998 90 163 52 07"


def test_schema_asks_for_customer_details() -> None:
    props = _WORK_HANDOFF_SCHEMA["parameters"]["properties"]
    assert "customer_name" in props
    assert "customer_phone" in props
    assert "exactly as the customer" in _WORK_HANDOFF_SCHEMA["description"]


def test_schema_limits_one_handoff_per_turn() -> None:
    """Live over-fire (2026-06-10): 'pulni qaytarib bering' matched both the
    returning-lead and complaint triggers -> two cards for one message."""
    description = _WORK_HANDOFF_SCHEMA["description"]
    assert "at most once per customer turn" in description.lower()


def test_schema_never_handoffs_on_greetings() -> None:
    """Live noise (2026-06-10): 'salom' (turn 1) triggered a returning-lead
    re-handoff before the customer's real message arrived (turn 2) -> two
    cards 11s apart. Greetings are not handoff signals."""
    description = _WORK_HANDOFF_SCHEMA["description"]
    assert "greeting" in description.lower()
    assert "same need" in description.lower()
