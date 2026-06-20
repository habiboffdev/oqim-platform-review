"""Two concurrent adapter runs in different workspaces must NOT cross-contaminate
(the per-run run_agent.OpenAI mutation race is fixed via the contextvar)."""
import asyncio
import pytest
from unittest.mock import patch
from app.brain.llm import LLMToolResponse
from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.hermes.engine import HermesEngineAdapter
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler

pytestmark = pytest.mark.asyncio

def _cfg(ws):
    return AgentConfig(agent_id=ws*10, workspace_id=ws, name="S", trust_mode="disabled",
                       auto_send_threshold=0.85, agent_md="# S\nSen sotuvchisan.")

def _profile(ws):
    return RuntimeProfileCompiler().compile_agent(config=_cfg(ws), agent_kind="seller")

async def test_concurrent_runs_keep_workspace_isolation():
    seen = []
    async def _fake_gwt(**kw):
        seen.append(kw.get("workspace_id"))
        await asyncio.sleep(0.05)  # force interleaving
        return LLMToolResponse(text=f"ws{kw.get('workspace_id')}", tool_calls=[],
                               model_used="m", provider="gemini")
    with patch("app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt):
        r1, r2 = await asyncio.gather(
            HermesEngineAdapter().run(config=_cfg(1), profile=_profile(1),
                                      customer_message="hi",
                                      grounding=["f1"], history=[], agent_kind="seller"),
            HermesEngineAdapter().run(config=_cfg(2), profile=_profile(2),
                                      customer_message="hi",
                                      grounding=["f2"], history=[], agent_kind="seller"),
        )
    assert set(seen) == {1, 2}
    assert "ws1" in r1.reply_text and "ws2" in r2.reply_text
