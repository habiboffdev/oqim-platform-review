from __future__ import annotations

from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler


def test_engine_module_has_no_baseline_or_talk_constants():
    import app.modules.agent_runtime_v2.hermes.engine as engine
    assert not hasattr(engine, "_BASELINE_MODEL_CONFIDENCE")
    assert not hasattr(engine, "_TALK_ONLY_REPLY_AGENT_KINDS")
    assert not hasattr(engine, "_TALK_TOOL_NAMES")


def test_profile_drives_allowed_tools_for_agent_kind_spellings():
    compiler = RuntimeProfileCompiler()
    base = dict(agent_id=1, workspace_id=1, name="x", trust_mode="autopilot", auto_send_threshold=0.0, agent_md="#")
    seller = compiler.compile_agent(config=AgentConfig(**base), agent_kind="seller")
    seller_agent = compiler.compile_agent(config=AgentConfig(**base), agent_kind="seller_agent")
    assert seller.allowed_tool_names == seller_agent.allowed_tool_names
    # pilot hardening (2026-06-18): interactive seller is talk-only, no retrieval.
    assert "talk.send_msgs" in seller.allowed_tool_names
    assert not any(t.startswith("knowledge_") for t in seller.allowed_tool_names)
