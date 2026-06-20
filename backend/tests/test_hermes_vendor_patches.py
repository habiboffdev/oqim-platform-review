"""The agent's identity must be ONLY our AGENT.md, with no upstream branding."""
import asyncio
import logging

import pytest

from app.modules.agent_runtime_v2.budget import BudgetExceededError
from app.modules.agent_runtime_v2.hermes import openai_shim
from app.modules.agent_runtime_v2.hermes.openai_shim import install_shim_once
from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext, use_tool_context
from app.modules.agent_runtime_v2.hermes.vendor_patches import apply_vendor_patches


def _effective_system_prompt(agent) -> str:
    """The system prompt the LLM actually receives.

    The Hermes engine deliberately keeps ``ephemeral_system_prompt`` out of
    ``_build_system_prompt()`` (the cached prefix) and appends it only at
    API-call time — see run_agent.py:6208-6209 and the call sites at
    run_agent.py:11903-11905 / 12745-12747. We reconstruct it the exact same
    way so the assertions cover the real, end-to-end identity the model sees,
    not just the cached prefix."""
    cached = agent._build_system_prompt()
    if agent.ephemeral_system_prompt:
        return (cached + "\n\n" + agent.ephemeral_system_prompt).strip()
    return cached


def test_system_prompt_has_no_upstream_identity():
    apply_vendor_patches()
    from run_agent import AIAgent
    agent = AIAgent(
        base_url="http://oqim.invalid", api_key="x", provider="openai",
        api_mode="chat_completions", model="gemini", enabled_toolsets=["oqim"],
        ephemeral_system_prompt="# Sotuvchi\nSen OQIM sotuvchisisan. Faqat faktlarga tayan.",
        skip_context_files=True, skip_memory=True, save_trajectories=False,
        quiet_mode=True, max_iterations=4, session_db=None,
    )
    prompt = _effective_system_prompt(agent)
    assert "Nous Research" not in prompt
    assert "Hermes Agent" not in prompt
    assert "hermes-agent.nousresearch.com" not in prompt
    # Broader sweep: no upstream branding leaks via any block, in any casing.
    lower = prompt.lower()
    assert "nous" not in lower
    assert "hermes" not in lower
    assert "Sotuvchi" in prompt  # our AGENT.md identity survives — and is the only identity


def test_apply_vendor_patches_is_idempotent():
    apply_vendor_patches()
    apply_vendor_patches()  # no raise, still empty
    import run_agent
    assert run_agent.DEFAULT_AGENT_IDENTITY == ""
    assert run_agent.HERMES_AGENT_HELP_GUIDANCE == ""


def test_vendor_patch_propagates_contextvars_into_hermes_inner_thread():
    """run_agent spawns the actual API call on a raw threading.Thread
    (run_agent.py:7822); a raw Thread drops contextvars, so the OQIM shim would
    see no ToolContext. The patch rebinds run_agent.threading.Thread to a
    context-copying subclass so the contextvar (workspace/chain/loop) survives."""
    import contextvars

    apply_vendor_patches()
    import run_agent

    probe: contextvars.ContextVar = contextvars.ContextVar("probe", default=None)
    seen: dict = {}

    def _target():
        seen["v"] = probe.get()

    token = probe.set("ws-7")
    try:
        t = run_agent.threading.Thread(target=_target, daemon=True)
        t.start()
        t.join()
    finally:
        probe.reset(token)

    # A bare threading.Thread would yield None here; the patched one propagates.
    assert seen["v"] == "ws-7"
    # The rebind is scoped to run_agent only — the process-wide stdlib is intact.
    import threading as _std
    assert run_agent.threading.Thread is not _std.Thread


@pytest.mark.asyncio
async def test_budget_exceeded_from_oqim_shim_fails_fast_without_hermes_retries(monkeypatch):
    apply_vendor_patches()
    install_shim_once()
    import run_agent
    from run_agent import AIAgent

    calls = 0

    async def _raise_budget(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise BudgetExceededError("workspace daily token cap reached")

    monkeypatch.setattr(openai_shim, "generate_with_tools", _raise_budget)
    monkeypatch.setattr(run_agent, "jittered_backoff", lambda *args, **kwargs: 0)

    agent = AIAgent(
        base_url="http://oqim.invalid", api_key="x", provider="openai",
        api_mode="chat_completions", model="gemini", enabled_toolsets=["oqim"],
        ephemeral_system_prompt="# Sotuvchi\nSen OQIM sotuvchisisan.",
        skip_context_files=True, skip_memory=True, save_trajectories=False,
        quiet_mode=True, max_iterations=4, session_db=None,
    )
    agent._disable_streaming = True
    agent._api_max_retries = 3

    ctx = ToolContext(
        workspace_id=1,
        agent_id=1,
        conversation_id=None,
        grounding=[],
        history=[],
        chain_name="FLASH_CHAIN",
        loop=asyncio.get_running_loop(),
    )

    def _run_sync():
        with use_tool_context(ctx):
            return agent.run_conversation("salom")

    result = await asyncio.to_thread(_run_sync)

    assert calls == 1
    assert result["failed"] is True
    assert "workspace daily token cap reached" in result["error"]


def test_context_length_resolver_returns_ctx_window_for_shim_base_url():
    apply_vendor_patches()
    import agent.model_metadata as mm
    from app.modules.agent_runtime_v2.hermes.openai_shim import OQIM_SHIM_BASE_URL

    ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        context_window=64_000,
    )
    with use_tool_context(ctx):
        assert mm.get_model_context_length("gemini", base_url=OQIM_SHIM_BASE_URL) == 64_000


def test_context_length_resolver_defaults_to_1m_without_ctx():
    apply_vendor_patches()
    import agent.model_metadata as mm
    from app.modules.agent_runtime_v2.hermes.openai_shim import OQIM_SHIM_BASE_URL

    # No ToolContext active -> the shim default (gemini's true window).
    assert mm.get_model_context_length("gemini", base_url=OQIM_SHIM_BASE_URL) == 1_048_576


def test_context_length_resolver_clamps_to_safe_range():
    apply_vendor_patches()
    import agent.model_metadata as mm
    from app.modules.agent_runtime_v2.hermes.openai_shim import OQIM_SHIM_BASE_URL

    def _ctx(window):
        return ToolContext(
            workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
            context_window=window,
        )

    with use_tool_context(_ctx(10)):
        assert mm.get_model_context_length("gemini", base_url=OQIM_SHIM_BASE_URL) == 64_000
    with use_tool_context(_ctx(9_000_000)):
        assert mm.get_model_context_length("gemini", base_url=OQIM_SHIM_BASE_URL) == 1_048_576


def test_context_length_resolver_delegates_for_non_shim_base_url():
    apply_vendor_patches()
    import agent.model_metadata as mm

    # ctx window is 64K, but a non-sentinel base_url must NOT use it. Empty
    # base_url is not a custom endpoint, so Hermes resolves "gemini" to 1M via its
    # own hardcoded table -- no network, and proves the ctx window was ignored.
    ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        context_window=64_000,
    )
    with use_tool_context(ctx):
        assert mm.get_model_context_length("gemini", base_url="") == 1_048_576


def test_context_length_resolver_honors_explicit_config_override():
    apply_vendor_patches()
    import agent.model_metadata as mm
    from app.modules.agent_runtime_v2.hermes.openai_shim import OQIM_SHIM_BASE_URL

    # An explicit config override wins even at the sentinel base_url (delegates to
    # upstream step 0, which returns the override verbatim).
    ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        context_window=64_000,
    )
    with use_tool_context(ctx):
        assert mm.get_model_context_length(
            "gemini", base_url=OQIM_SHIM_BASE_URL, config_context_length=500_000
        ) == 500_000


def test_context_length_resolver_patched_on_both_bindings_and_idempotent():
    apply_vendor_patches()
    apply_vendor_patches()  # second call must not double-wrap
    import agent.context_compressor as cc
    import agent.model_metadata as mm

    assert getattr(mm.get_model_context_length, "_oqim_patched", False) is True
    # context_compressor imports the symbol BY VALUE, so both bindings must point
    # at the same patched callable.
    assert mm.get_model_context_length is cc.get_model_context_length


def test_aiagent_construction_uses_ctx_window_and_logs_no_probe_down(caplog):
    apply_vendor_patches()
    from run_agent import AIAgent
    from app.modules.agent_runtime_v2.hermes.openai_shim import OQIM_SHIM_BASE_URL

    ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        context_window=200_000,
    )
    # The "Could not detect context length ... defaulting to 256,000" line is a
    # logger.info on agent.model_metadata (model_metadata.py:1582). Capture INFO.
    with caplog.at_level(logging.INFO), use_tool_context(ctx):
        agent = AIAgent(
            base_url=OQIM_SHIM_BASE_URL, api_key="x", provider="openai",
            api_mode="chat_completions", model="gemini", enabled_toolsets=["oqim"],
            ephemeral_system_prompt="# Sotuvchi", skip_context_files=True,
            skip_memory=True, save_trajectories=False, quiet_mode=True,
            max_iterations=4, session_db=None,
        )

    cc = agent.context_compressor
    assert cc.context_length == 200_000
    # threshold relationship holds regardless of the per-model threshold percent.
    assert cc.threshold_tokens == max(int(200_000 * cc.threshold_percent), 64_000)
    assert "Could not detect context length" not in caplog.text


def test_aiagent_construction_defaults_to_1m_window(caplog):
    apply_vendor_patches()
    from run_agent import AIAgent
    from app.modules.agent_runtime_v2.hermes.openai_shim import OQIM_SHIM_BASE_URL

    ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
    )  # no override -> 1M default
    with caplog.at_level(logging.INFO), use_tool_context(ctx):
        agent = AIAgent(
            base_url=OQIM_SHIM_BASE_URL, api_key="x", provider="openai",
            api_mode="chat_completions", model="gemini", enabled_toolsets=["oqim"],
            ephemeral_system_prompt="# Sotuvchi", skip_context_files=True,
            skip_memory=True, save_trajectories=False, quiet_mode=True,
            max_iterations=4, session_db=None,
        )

    assert agent.context_compressor.context_length == 1_048_576
    assert "Could not detect context length" not in caplog.text
