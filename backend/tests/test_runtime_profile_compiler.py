from __future__ import annotations

import pytest

from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.context_config import CONTEXT_WINDOW_DEFAULT
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler


def _config(
    trust_mode: str = "autopilot",
    threshold: float = 0.0,
    context_window: int = CONTEXT_WINDOW_DEFAULT,
) -> AgentConfig:
    return AgentConfig(
        agent_id=13,
        workspace_id=1,
        name="Agent",
        trust_mode=trust_mode,
        auto_send_threshold=threshold,
        agent_md="# AGENT",
        context_window=context_window,
    )


def test_interactive_agent_profile_grants_talk_retrieval_and_handoff_actions():
    profile = RuntimeProfileCompiler().compile_agent(
        config=_config(),
        agent_kind="seller_agent",
    )

    assert profile.profile_kind == "agent"
    assert profile.execution_mode == "interactive"
    assert "talk.send_msgs" in profile.allowed_tool_names
    assert "talk.send_media" in profile.allowed_tool_names
    assert "talk.send_reaction" in profile.allowed_tool_names
    # pilot hardening (2026-06-18): RAG/knowledge retrieval removed from the talk
    # loop — the seller answers only from AGENT.md (no bio->offering fabrication).
    assert not any(t.startswith("knowledge_") for t in profile.allowed_tool_names)
    # slice 3 (2026-06-14): the records pass is the sole commercial-state
    # recorder, so the interactive lane no longer grants bookkeeping tools —
    # work.handoff and conversation.record_intelligence are gone; the seller
    # sells and speaks honestly, the post-reply records pass captures the rest.
    assert "conversation.set_state" not in profile.allowed_tool_names
    assert "conversation.record_intelligence" not in profile.allowed_tool_names
    assert "work.create_task" not in profile.allowed_tool_names
    assert "owner.notify" not in profile.allowed_tool_names
    assert "work.handoff" not in profile.allowed_tool_names
    # slice 5: commerce.create_order left the interactive lane (the records pass
    # captures items post-reply); it stays in the action agent grant.
    assert "commerce.create_order" not in profile.allowed_tool_names
    assert "crm.context" not in profile.allowed_tool_names
    assert "commerce.create_checkout_intent" not in profile.allowed_tool_names
    assert "knowledge_extract_candidates" not in profile.allowed_tool_names
    assert "knowledge_propose_catalog_update" not in profile.allowed_tool_names
    assert "talk.delete_message" not in profile.allowed_tool_names
    assert "talk.send_sticker" not in profile.allowed_tool_names
    assert profile.retrieval_policy.max_in_loop_catalog_calls == 0
    assert profile.hermes_settings.chain == "FLASH_CHAIN"
    assert profile.hermes_settings.max_iterations == 4
    assert profile.hermes_settings.skip_memory is False
    assert profile.action_policy.faithfulness_required is False


def test_profile_hash_stable_then_changes_with_trust_mode_and_execution_mode():
    compiler = RuntimeProfileCompiler()
    a = compiler.compile_agent(config=_config(), agent_kind="seller_agent")
    b = compiler.compile_agent(config=_config(), agent_kind="seller_agent")
    c = compiler.compile_agent(config=_config(trust_mode="disabled"), agent_kind="seller_agent")
    d = compiler.compile_agent(
        config=_config(),
        agent_kind="seller_agent",
        execution_mode="action",
    )

    assert a.profile_hash == b.profile_hash
    assert a.profile_hash != c.profile_hash
    assert a.profile_hash != d.profile_hash


def test_action_agent_profile_can_mutate_business_state_without_customer_talk():
    profile = RuntimeProfileCompiler().compile_agent(
        config=_config(),
        agent_kind="seller_agent",
        execution_mode="action",
    )

    assert profile.profile_kind == "agent"
    assert profile.execution_mode == "action"
    assert "conversation.set_state" in profile.allowed_tool_names
    assert "conversation.record_intelligence" in profile.allowed_tool_names
    assert "work.create_task" in profile.allowed_tool_names
    assert "owner.notify" in profile.allowed_tool_names
    assert "commerce.create_order" in profile.allowed_tool_names
    assert "commerce.create_checkout_intent" in profile.allowed_tool_names
    assert "knowledge_extract_candidates" in profile.allowed_tool_names
    assert "knowledge_propose_catalog_update" in profile.allowed_tool_names
    assert not any(tool.startswith("talk.") for tool in profile.allowed_tool_names)
    assert profile.action_policy.faithfulness_required is False
    assert profile.retrieval_policy.enable_agentic_search is True
    assert profile.hermes_settings.max_iterations == 8


def test_setup_agent_profile_uses_generic_agent_profile_without_customer_talk():
    profile = RuntimeProfileCompiler().compile_agent(
        config=_config(trust_mode="disabled"),
        agent_kind="setup_agent",
    )

    assert profile.profile_kind == "agent"
    assert profile.execution_mode == "setup"
    assert profile.hermes_settings.skip_memory is False
    assert profile.hermes_settings.skip_context_files is False
    assert profile.hermes_settings.save_trajectories is True
    assert "knowledge_create_source_doc" in profile.allowed_tool_names
    assert "knowledge_extract_candidates" in profile.allowed_tool_names
    assert "knowledge_propose_candidate" in profile.allowed_tool_names
    assert "knowledge_save_note" in profile.allowed_tool_names
    assert not any(tool.startswith("talk.") for tool in profile.allowed_tool_names)
    assert profile.action_policy.faithfulness_required is False


def test_role_specific_runtime_profiles_are_retired():
    compiler = RuntimeProfileCompiler()
    retired_profile_kind = "seller_" + "fast"

    with pytest.raises(ValueError, match="role-specific runtime profiles are retired"):
        compiler.compile_profile(
            config=_config(),
            agent_kind="seller_agent",
            profile_kind=retired_profile_kind,  # type: ignore[arg-type]
        )


def test_interactive_profile_is_lazy_grounding():
    profile = RuntimeProfileCompiler().compile_agent(config=_config(), agent_kind="seller")
    assert profile.retrieval_policy.enable_eager_grounding is False


def test_action_profile_is_eager_grounding():
    profile = RuntimeProfileCompiler().compile_profile(
        config=_config(), agent_kind="seller", execution_mode="action"
    )
    assert profile.retrieval_policy.enable_eager_grounding is True


def test_setup_profile_is_eager_grounding():
    profile = RuntimeProfileCompiler().compile_agent(config=_config(), agent_kind="setup_agent")
    assert profile.retrieval_policy.enable_eager_grounding is True


def test_interactive_lane_has_no_bookkeeping_tools():
    """Slice 3 (2026-06-14): the records pass is the sole commercial-state
    recorder, so the interactive lane drops work.handoff AND
    conversation.record_intelligence. Slice 5 further drops commerce.create_order
    (records pass captures items) and crm.context (deal_value pre-injected). Pilot
    hardening (2026-06-18) drops RAG retrieval too, leaving talk only (3 tools)."""
    from app.modules.agent_runtime_v2.runtime_profile import _INTERACTIVE_AGENT_TOOLS

    assert "work.handoff" not in _INTERACTIVE_AGENT_TOOLS
    assert "conversation.record_intelligence" not in _INTERACTIVE_AGENT_TOOLS
    assert "conversation.set_state" not in _INTERACTIVE_AGENT_TOOLS
    assert "work.create_task" not in _INTERACTIVE_AGENT_TOOLS
    assert "owner.notify" not in _INTERACTIVE_AGENT_TOOLS
    assert "commerce.create_order" not in _INTERACTIVE_AGENT_TOOLS
    assert "crm.context" not in _INTERACTIVE_AGENT_TOOLS
    assert len(_INTERACTIVE_AGENT_TOOLS) == 3  # talk only


def test_hermes_settings_default_context_length_is_gemini_true_window():
    profile = RuntimeProfileCompiler().compile_agent(
        config=_config(), agent_kind="seller_agent"
    )
    assert profile.hermes_settings.context_length == 1_048_576


def test_per_agent_context_window_threads_into_hermes_settings_all_modes():
    compiler = RuntimeProfileCompiler()
    interactive = compiler.compile_agent(
        config=_config(context_window=64_000), agent_kind="seller_agent"
    )
    setup = compiler.compile_agent(
        config=_config(context_window=64_000), agent_kind="setup_agent"
    )
    action = compiler.compile_profile(
        config=_config(context_window=64_000),
        agent_kind="seller_agent",
        execution_mode="action",
    )
    assert interactive.hermes_settings.context_length == 64_000
    assert setup.hermes_settings.context_length == 64_000
    assert action.hermes_settings.context_length == 64_000


def test_crm_context_tool_retired_from_all_grants():
    """Slice 5: crm.context is retired — the lead's stage/deal_value are
    pre-injected into conversation_state["crm"] (no LLM round-trip), so no
    profile grants the tool any longer."""
    from app.modules.agent_runtime_v2.runtime_profile import (
        _ACTION_AGENT_TOOLS,
        _INTERACTIVE_AGENT_TOOLS,
        _RECORD_AGENT_TOOLS,
        _SETUP_AGENT_TOOLS,
    )
    assert "crm.context" not in _INTERACTIVE_AGENT_TOOLS
    assert "crm.context" not in _ACTION_AGENT_TOOLS
    assert "crm.context" not in _SETUP_AGENT_TOOLS
    assert "crm.context" not in _RECORD_AGENT_TOOLS


def test_interactive_drops_commerce_action_keeps_it_in_action():
    """Slice 5 (Task 3): commerce.create_order leaves the interactive seller (the
    records pass captures items post-reply) but stays in the action agent grant."""
    interactive = set(
        RuntimeProfileCompiler()
        .compile_profile(
            config=_config(), agent_kind="seller", execution_mode="interactive"
        )
        .allowed_tool_names
    )
    action = set(
        RuntimeProfileCompiler()
        .compile_profile(
            config=_config(), agent_kind="seller", execution_mode="action"
        )
        .allowed_tool_names
    )
    assert "commerce.create_order" not in interactive
    assert "commerce.create_order" in action


def test_record_mode_grants_only_conversation_record():
    """The forced records pass grants EXACTLY conversation.record (single-tool
    grant forces it under mode=ANY), runs one iteration, keeps the oqim toolset,
    faithfulness off, and grants NO talk tools (it runs AFTER the reply,
    post-commit, off the customer-facing path)."""
    profile = RuntimeProfileCompiler().compile_profile(
        config=_config(),
        agent_kind="seller",
        execution_mode="record",
    )

    assert profile.profile_kind == "agent"
    assert profile.execution_mode == "record"
    assert profile.allowed_tool_names == ("conversation.record",)
    assert profile.hermes_settings.max_iterations == 1
    assert "oqim" in profile.hermes_settings.enabled_toolsets
    assert not any(tool.startswith("talk.") for tool in profile.allowed_tool_names)
    assert profile.action_policy.faithfulness_required is False
    assert profile.retrieval_policy.enable_eager_grounding is False
    assert profile.retrieval_policy.enable_agentic_search is False


def test_record_mode_threads_per_agent_context_window():
    profile = RuntimeProfileCompiler().compile_profile(
        config=_config(context_window=64_000),
        agent_kind="seller_agent",
        execution_mode="record",
    )
    assert profile.hermes_settings.context_length == 64_000


def test_record_tools_constant_is_conversation_record_only():
    from app.modules.agent_runtime_v2.runtime_profile import _RECORD_AGENT_TOOLS

    assert _RECORD_AGENT_TOOLS == ("conversation.record",)


def test_interactive_lane_still_excludes_set_state_after_record_mode_added():
    """Back-compat guard: adding the record lane must NOT leak set_state into
    the talk loop (it must stay out of _INTERACTIVE_AGENT_TOOLS)."""
    from app.modules.agent_runtime_v2.runtime_profile import _INTERACTIVE_AGENT_TOOLS

    assert "conversation.set_state" not in _INTERACTIVE_AGENT_TOOLS


def test_interactive_excludes_bookkeeping_tools():
    """Slice 3: the records pass is the sole commercial-state recorder, so the
    interactive seller no longer grants work.handoff or
    conversation.record_intelligence; it sells and speaks honestly only."""
    profile = RuntimeProfileCompiler().compile_profile(
        config=_config(), agent_kind="seller", execution_mode="interactive"
    )
    tools = set(profile.allowed_tool_names)
    assert "work.handoff" not in tools
    assert "conversation.record_intelligence" not in tools
    # talk remains; retrieval removed (pilot hardening 2026-06-18):
    assert "talk.send_msgs" in tools
    assert "crm.context" not in tools


def test_interactive_lane_grants_no_retrieval_tools():
    """Pilot hardening (2026-06-18): the interactive seller answers ONLY from
    AGENT.md. RAG/knowledge retrieval tools are removed from the talk loop so the
    agent cannot pull raw source material (e.g. a speaker bio) and fabricate a
    priced offering from it — prod run 182: a knowledge_search returned Murabbiy's
    speaker bio, the model promoted it to a 'consulting service' and invented a
    price comparison to the real course. AGENT.md is the single source of truth;
    the action/setup lanes keep retrieval for deliberate catalog work."""
    from app.modules.agent_runtime_v2.runtime_profile import _INTERACTIVE_AGENT_TOOLS

    profile = RuntimeProfileCompiler().compile_profile(
        config=_config(), agent_kind="seller", execution_mode="interactive"
    )
    tools = set(profile.allowed_tool_names)
    assert tools == {"talk.send_msgs", "talk.send_media", "talk.send_reaction"}
    assert not any(t.startswith("knowledge_") for t in tools)
    assert _INTERACTIVE_AGENT_TOOLS == (
        "talk.send_msgs",
        "talk.send_media",
        "talk.send_reaction",
    )
    # no retrieval round-trips left to bound
    assert profile.retrieval_policy.max_in_loop_catalog_calls == 0
