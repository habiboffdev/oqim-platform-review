import inspect
import types

import pytest

from app.modules.agent_runtime_v2 import runtime_service as rs
from app.modules.agent_runtime_v2.hermes.engine import HermesEngineAdapter

pytestmark = pytest.mark.asyncio


async def test_gather_turn_context_accepts_current_turn_media_param():
    sig = inspect.signature(rs.AgentRuntimeService.gather_turn_context)
    assert "current_turn_media" in sig.parameters
    assert sig.parameters["current_turn_media"].default is None


async def test_run_from_context_forwards_current_turn_media(monkeypatch):
    captured: dict = {}

    class _StopError(Exception):
        pass

    async def fake_run(self, **kwargs):
        captured.update(kwargs)
        raise _StopError  # capture-and-stop before the post-engine path runs

    monkeypatch.setattr(HermesEngineAdapter, "run", fake_run)

    sentinel = [{"sentinel": True}]
    gathered = types.SimpleNamespace(
        runtime_profile=types.SimpleNamespace(
            action_policy=types.SimpleNamespace(faithfulness_required=False),
        ),
        grounding=[], history=[], voice_examples=[], authority_warnings=[],
        agent_kind="custom_agent",
    )
    ctx = rs._AgentTurnContext(
        config=types.SimpleNamespace(workspace_id=1),
        gathered=gathered,
        agent_id=1,
        customer_message="salom",
        current_turn_media=sentinel,
    )

    with pytest.raises(_StopError):
        await rs.AgentRuntimeService(None).run_from_context(ctx)
    assert captured["current_turn_media"] == sentinel


def test_dispatcher_source_passes_live_media_text_to_gather_turn_context():
    """Source-level test: the dispatcher must pass live_media_text= to
    gather_turn_context. A full dispatch_agent_turn test requires a DB/ORM
    session stack that is impractical in a pure unit test, so we verify the
    wiring exists in the source — complemented by the _burst_prompt_text
    bare=True tests in test_message_prompt_text.py for the rendering logic."""
    import app.modules.agent_runtime_v2.dispatcher as _disp

    src = inspect.getsource(_disp.dispatch_agent_turn)
    assert "live_media_text=live_media_text" in src, (
        "dispatcher.dispatch_agent_turn must pass live_media_text= to gather_turn_context"
    )
    # Also verify the bare render is actually computed (not a bare literal).
    assert "_burst_prompt_text(" in src and "bare=True" in src, (
        "dispatcher must call _burst_prompt_text(..., bare=True) to produce live_media_text"
    )


async def test_run_from_context_forwards_live_media_text(monkeypatch):
    """Behavioral: run_from_context passes ctx.live_media_text to engine.run."""
    captured: dict = {}

    class _StopError(Exception):
        pass

    async def fake_run(self, **kwargs):
        captured.update(kwargs)
        raise _StopError

    monkeypatch.setattr(HermesEngineAdapter, "run", fake_run)

    gathered = types.SimpleNamespace(
        runtime_profile=types.SimpleNamespace(
            action_policy=types.SimpleNamespace(faithfulness_required=False),
        ),
        grounding=[], history=[], voice_examples=[], authority_warnings=[],
        agent_kind="custom_agent",
    )
    ctx = rs._AgentTurnContext(
        config=types.SimpleNamespace(workspace_id=1),
        gathered=gathered,
        agent_id=1,
        customer_message="salom",
        current_turn_media=[],
        live_media_text="[Voice message]",
    )

    with pytest.raises(_StopError):
        await rs.AgentRuntimeService(None).run_from_context(ctx)
    assert captured.get("live_media_text") == "[Voice message]"
