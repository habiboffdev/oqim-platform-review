"""Forced single-tool finalization: the shim must force a tool call (Gemini
mode=ANY) pinned to conversation.set_state when ToolContext.force_tool_call is
set, even with NO talk tool present — and leave the existing talk-forcing path
untouched when force_tool_call is False (back-compat)."""
import asyncio

import pytest

from app.modules.agent_runtime_v2.hermes import openai_shim
from app.modules.agent_runtime_v2.hermes.tool_context import (
    ToolContext,
    use_tool_context,
)

pytestmark = pytest.mark.asyncio


_SET_STATE_TOOL = {
    "type": "function",
    "function": {
        "name": "conversation.set_state",
        "description": "record structured commercial state",
        "parameters": {"type": "object", "properties": {}},
    },
}

_TALK_TOOL = {
    "type": "function",
    "function": {
        "name": "talk.send_msgs",
        "description": "send bubbles",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _run_create(ctx: ToolContext, tools: list[dict]):
    def _call():
        with use_tool_context(ctx):
            return openai_shim._Completions().create(
                model="gemini",
                messages=[{"role": "user", "content": "salom"}],
                tools=tools,
            )

    return asyncio.to_thread(_call)


async def test_force_tool_call_forces_set_state_with_no_talk_tool(monkeypatch):
    captured = {}

    async def fake_generate_with_tools(**kwargs):
        captured.update(kwargs)
        from app.brain.llm import LLMToolResponse

        return LLMToolResponse(text="ok", tool_calls=[], model_used="gemini", provider="t")

    monkeypatch.setattr(openai_shim, "generate_with_tools", fake_generate_with_tools)

    loop = asyncio.get_running_loop()
    ctx = ToolContext(
        workspace_id=1, agent_id=2, conversation_id=3, grounding=[], history=[],
        loop=loop, chain_name="FLASH_CHAIN",
        allowed_tool_names=frozenset({"conversation.set_state"}),
        force_tool_call=True,
    )

    await _run_create(ctx, [_SET_STATE_TOOL])

    assert captured["force_function_calling"] is True
    assert captured["allowed_function_names"] == ["conversation.set_state"]
    assert {t["name"] for t in captured["tools"]} == {"conversation.set_state"}


async def test_force_tool_call_false_keeps_talk_forcing_unchanged(monkeypatch):
    """Back-compat: with force_tool_call False, a present talk tool still forces
    function calling but WITHOUT a pinned allowed_function_names (unchanged
    behavior for every existing agent)."""
    captured = {}

    async def fake_generate_with_tools(**kwargs):
        captured.update(kwargs)
        from app.brain.llm import LLMToolResponse

        return LLMToolResponse(text="ok", tool_calls=[], model_used="gemini", provider="t")

    monkeypatch.setattr(openai_shim, "generate_with_tools", fake_generate_with_tools)

    loop = asyncio.get_running_loop()
    ctx = ToolContext(
        workspace_id=1, agent_id=2, conversation_id=3, grounding=[], history=[],
        loop=loop, chain_name="FLASH_CHAIN",
        allowed_tool_names=frozenset({"talk.send_msgs"}),
        # force_tool_call defaults to False
    )

    await _run_create(ctx, [_TALK_TOOL])

    assert captured["force_function_calling"] is True
    assert captured["allowed_function_names"] is None


async def test_no_force_and_no_talk_tool_does_not_force(monkeypatch):
    """Back-compat: a plain non-talk tool turn with force_tool_call False must
    NOT force function calling and must NOT pin a tool name."""
    captured = {}

    async def fake_generate_with_tools(**kwargs):
        captured.update(kwargs)
        from app.brain.llm import LLMToolResponse

        return LLMToolResponse(text="ok", tool_calls=[], model_used="gemini", provider="t")

    monkeypatch.setattr(openai_shim, "generate_with_tools", fake_generate_with_tools)

    loop = asyncio.get_running_loop()
    ctx = ToolContext(
        workspace_id=1, agent_id=2, conversation_id=3, grounding=[], history=[],
        loop=loop, chain_name="FLASH_CHAIN",
        allowed_tool_names=frozenset({"knowledge_search"}),
    )

    await _run_create(
        ctx,
        [
            {
                "type": "function",
                "function": {
                    "name": "knowledge_search",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert captured["force_function_calling"] is False
    assert captured["allowed_function_names"] is None


def test_tool_context_force_flag_defaults_false():
    """The new force_tool_call field defaults False so every existing
    ToolContext construction is byte-for-byte unchanged in behavior."""
    ctx = ToolContext(
        workspace_id=1, agent_id=2, conversation_id=3, grounding=[], history=[],
    )
    assert ctx.force_tool_call is False
