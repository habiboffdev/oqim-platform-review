"""Proves the packaged Hermes AIAgent runs one tool-calling turn as a library.

No CLI, no network: this is the Phase 1 package-source regression guard.
"""
import json
from pathlib import Path

from app.modules.agent_runtime_v2.hermes._bootstrap import ensure_hermes_runtime

ensure_hermes_runtime()


def test_hermes_runtime_resolves_from_installed_package_not_vendor_snapshot():
    runtime_root = Path(ensure_hermes_runtime()).resolve()
    vendor_root = Path(__file__).resolve().parents[1] / "vendor" / "hermes"

    assert runtime_root != vendor_root.resolve()
    import run_agent

    assert "backend/vendor/hermes" not in str(Path(run_agent.__file__).resolve())


def test_packaged_aiagent_runs_one_tool_turn(monkeypatch):
    import run_agent
    from openai.types.chat import ChatCompletion
    from openai.types.chat.chat_completion import Choice
    from openai.types.chat.chat_completion_message import ChatCompletionMessage
    from openai.types.chat.chat_completion_message_tool_call import (
        ChatCompletionMessageToolCall,
        Function,
    )
    from tools.registry import registry

    registry.register(
        name="echo", toolset="spike",
        schema={"name": "echo", "description": "Echo text.",
                "parameters": {"type": "object",
                               "properties": {"text": {"type": "string"}},
                               "required": ["text"]}},
        handler=lambda args, **kw: json.dumps({"status": "ok", "echo": args.get("text", "")}),
        check_fn=lambda: True, requires_env=[], override=True,
    )

    state = {"calls": 0}

    def _toolcall():
        tc = ChatCompletionMessageToolCall(id="c1", type="function",
                function=Function(name="echo", arguments=json.dumps({"text": "hi"})))
        return ChatCompletion(id="x1", created=0, model="stub", object="chat.completion",
                choices=[Choice(index=0, finish_reason="tool_calls",
                                message=ChatCompletionMessage(role="assistant", content=None, tool_calls=[tc]))])

    def _final():
        return ChatCompletion(id="x2", created=0, model="stub", object="chat.completion",
                choices=[Choice(index=0, finish_reason="stop",
                                message=ChatCompletionMessage(role="assistant", content="Echoed: hi"))])

    class _Completions:
        def create(self, **kwargs):
            state["calls"] += 1
            return _toolcall() if state["calls"] == 1 else _final()

    class StubClient:
        def __init__(self, **kw): self.chat = type("C", (), {"completions": _Completions()})()
        def with_options(self, **kw): return self
        def close(self): pass

    monkeypatch.setattr(run_agent, "OpenAI", lambda **kw: StubClient(**kw))
    from run_agent import AIAgent

    agent = AIAgent(base_url="http://localhost:1", api_key="sk-stub", provider="openai",
                    api_mode="chat_completions", model="gpt-4o-mini",
                    enabled_toolsets=["spike"], ephemeral_system_prompt="You are a test agent.",
                    skip_context_files=True, skip_memory=True, save_trajectories=False,
                    quiet_mode=True, max_iterations=5, session_db=None)
    agent._disable_streaming = True
    result = agent.run_conversation("Salom")

    assert isinstance(result, dict)
    assert state["calls"] >= 2, "expected tool turn + text turn"
    assert "Echoed" in result.get("final_response", "")
