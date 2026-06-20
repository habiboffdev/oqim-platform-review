import asyncio

import pytest

from app.modules.agent_runtime_v2.hermes import openai_shim
from app.modules.agent_runtime_v2.hermes.tool_context import (
    ToolContext,
    use_tool_context,
)

pytestmark = pytest.mark.asyncio


async def test_shim_forwards_current_turn_media(monkeypatch):
    captured = {}

    async def fake_generate_with_tools(**kwargs):
        captured.update(kwargs)
        from app.brain.llm import LLMToolResponse
        return LLMToolResponse(text="ok", tool_calls=[], model_used="gemini", provider="t")

    monkeypatch.setattr(openai_shim, "generate_with_tools", fake_generate_with_tools)

    loop = asyncio.get_running_loop()
    ctx = ToolContext(
        workspace_id=1, agent_id=2, conversation_id=3, grounding=[], history=[],
        loop=loop, chain_name="FLASH_CHAIN", allowed_tool_names=frozenset(),
    )
    ctx.current_turn_media = [{"sentinel": True}]

    # create() blocks the loop thread on run_coroutine_threadsafe, so drive it
    # off-thread exactly like the real Hermes loop does.
    def _call():
        with use_tool_context(ctx):
            return openai_shim._Completions().create(
                model="gemini",
                messages=[{"role": "user", "content": "salom"}],
            )

    await asyncio.to_thread(_call)
    assert captured.get("current_turn_media") == [{"sentinel": True}]


async def test_shim_forwards_live_media_text(monkeypatch):
    captured = {}

    async def fake_generate_with_tools(**kwargs):
        captured.update(kwargs)
        from app.brain.llm import LLMToolResponse
        return LLMToolResponse(text="ok", tool_calls=[], model_used="gemini", provider="t")

    monkeypatch.setattr(openai_shim, "generate_with_tools", fake_generate_with_tools)

    loop = asyncio.get_running_loop()
    ctx = ToolContext(
        workspace_id=1, agent_id=2, conversation_id=3, grounding=[], history=[],
        loop=loop, chain_name="FLASH_CHAIN", allowed_tool_names=frozenset(),
    )
    ctx.current_turn_media = [{"sentinel": True}]
    ctx.live_media_text = "[Voice message]"

    def _call():
        with use_tool_context(ctx):
            return openai_shim._Completions().create(
                model="gemini",
                messages=[{"role": "user", "content": "salom"}],
            )

    await asyncio.to_thread(_call)
    assert captured.get("live_media_text") == "[Voice message]"
