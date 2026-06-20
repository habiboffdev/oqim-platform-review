"""conversation.record_intelligence carries the opted_out judgment (promoter Slice B)."""

from __future__ import annotations

import asyncio

import app.modules.agent_runtime_v2.hermes.oqim_tools as oqim_tools
from app.modules.agent_runtime_v2.hermes.oqim_tools import (
    _CONVERSATION_RECORD_INTELLIGENCE_SCHEMA,
    conversation_record_intelligence,
)
from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext, use_tool_context


def _ctx() -> ToolContext:
    return ToolContext(
        workspace_id=1,
        agent_id=2,
        conversation_id=3,
        agent_session_id=4,
        hermes_run_id="hermes_run:ri-tool",
        grounding=[],
        history=[],
    )


def _run_coro_sync(_ctx, coro, *, error_prefix):
    return asyncio.run(coro)


def test_record_intelligence_passes_opted_out_to_turn_facts(monkeypatch) -> None:
    async def fake_async(**kwargs):
        return {"status": "ok"}

    monkeypatch.setattr(oqim_tools, "_conversation_record_intelligence_async", fake_async)
    monkeypatch.setattr(oqim_tools, "_run_knowledge_coro", _run_coro_sync)

    ctx = _ctx()
    with use_tool_context(ctx):
        conversation_record_intelligence({"lead_stage": "lost", "opted_out": True})
    assert ctx.intelligence_payloads[-1]["opted_out"] is True

    ctx2 = _ctx()
    with use_tool_context(ctx2):
        conversation_record_intelligence({"lead_stage": "interested"})
    assert ctx2.intelligence_payloads[-1]["opted_out"] is False


def test_record_intelligence_schema_describes_opted_out() -> None:
    prop = _CONVERSATION_RECORD_INTELLIGENCE_SCHEMA["parameters"]["properties"]["opted_out"]
    assert prop["type"] == "boolean"
    assert "stop" in prop["description"].lower()
