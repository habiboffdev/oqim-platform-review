"""Compaction actually FIRES at the configured context window.

The context-window knob sets `ContextCompressor.threshold_tokens`
(`max(window * threshold_percent, 64K)`). Hermes's PREFLIGHT compression
(run_agent.py:12365) compacts the loaded `conversation_history` BEFORE the loop
when it has more than `protect_first_n + protect_last_n + 1` (= 24) messages AND
the rough request-token estimate is >= `threshold_tokens`. OQIM passes the full
replayed transcript as `conversation_history`, so this is the live path.

These tests prove that trigger fires (and stays off below threshold) in OQIM's
actual run path, against the REAL threshold our 64K window produces (64000),
without synthesizing a 64K-token transcript: we drive the two real gates —
message count and the token estimate.

Context: as of 2026-06-12 no live conversation has reached 64K (peak ~43K), so
compaction has not yet fired in production; this is the automated proof that it
will when a conversation grows past the window.
"""
import asyncio

import pytest

from app.brain.llm import LLMToolResponse
from app.modules.agent_runtime_v2.hermes import openai_shim
from app.modules.agent_runtime_v2.hermes.openai_shim import (
    OQIM_SHIM_BASE_URL,
    install_shim_once,
)
from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext, use_tool_context
from app.modules.agent_runtime_v2.hermes.vendor_patches import apply_vendor_patches

pytestmark = pytest.mark.asyncio


async def _benign_reply(*args, **kwargs):
    """A no-tool final reply so the loop terminates after one shim call."""
    return LLMToolResponse(text="ok", tool_calls=[], model_used="gemini", provider="oqim")


def _build_agent(window: int):
    apply_vendor_patches()
    install_shim_once()
    from run_agent import AIAgent

    ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        context_window=window, loop=asyncio.get_running_loop(),
    )
    with use_tool_context(ctx):
        agent = AIAgent(
            base_url=OQIM_SHIM_BASE_URL, api_key="x", provider="openai",
            api_mode="chat_completions", model="gemini", enabled_toolsets=["oqim"],
            ephemeral_system_prompt="# Sotuvchi", skip_context_files=True,
            skip_memory=True, save_trajectories=False, quiet_mode=True,
            max_iterations=4, session_db=None,
        )
    agent._disable_streaming = True
    return agent, ctx


def _history(n: int) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"}
        for i in range(n)
    ]


async def test_preflight_compaction_fires_when_estimate_exceeds_window_threshold(monkeypatch):
    import run_agent

    monkeypatch.setattr(openai_shim, "generate_with_tools", _benign_reply)
    # Simulate an over-threshold request instead of synthesizing 64K real tokens.
    monkeypatch.setattr(run_agent, "estimate_request_tokens_rough", lambda *a, **k: 10_000_000)

    agent, ctx = _build_agent(window=64_000)
    # The window is what set this; compaction is gated on it.
    assert agent.context_compressor.threshold_tokens == 64_000

    calls: list[int] = []

    def _spy_compress(messages, **kwargs):
        calls.append(len(messages))
        # Return a compacted (shorter) list so preflight sees progress then stops.
        return [messages[0], {"role": "user", "content": "[summary]"}, messages[-1]]

    monkeypatch.setattr(agent.context_compressor, "compress", _spy_compress)

    history = _history(30)  # > 24 messages -> passes the message-count gate

    def _run():
        with use_tool_context(ctx):
            return agent.run_conversation("salom", conversation_history=history)

    result = await asyncio.to_thread(_run)

    assert calls, "preflight compaction did not fire when estimate exceeded the 64K threshold"
    assert (result or {}).get("final_response") == "ok"


async def test_preflight_compaction_stays_off_below_window_threshold(monkeypatch):
    import run_agent

    monkeypatch.setattr(openai_shim, "generate_with_tools", _benign_reply)
    # Estimate well under the 64K threshold -> preflight must NOT compress,
    # even with a long (>24) message history.
    monkeypatch.setattr(run_agent, "estimate_request_tokens_rough", lambda *a, **k: 10)

    agent, ctx = _build_agent(window=64_000)

    calls: list[int] = []
    monkeypatch.setattr(
        agent.context_compressor, "compress",
        lambda messages, **kw: (calls.append(len(messages)) or messages),
    )

    history = _history(30)

    def _run():
        with use_tool_context(ctx):
            return agent.run_conversation("salom", conversation_history=history)

    await asyncio.to_thread(_run)

    assert not calls, "compaction fired below the 64K threshold"
