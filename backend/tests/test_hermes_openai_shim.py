import asyncio
from unittest.mock import patch

import pytest

from app.brain.llm import DEFAULT_TIMEOUT, LLMToolCall, LLMToolResponse
from app.modules.agent_runtime_v2.hermes.openai_shim import (
    ShimClient,
    _outer_timeout,
)
from app.modules.agent_runtime_v2.hermes.tool_context import (
    ToolContext,
    use_tool_context,
)
from app.modules.agent_talking.contracts import (
    TalkAction,
    TalkActionKind,
    TalkBundle,
    TalkingPolicy,
)

pytestmark = pytest.mark.asyncio


def _ctx(loop, *, chain_name="CONTROL_CHAIN", workspace_id=1) -> ToolContext:
    return ToolContext(
        workspace_id=workspace_id, agent_id=2, conversation_id=None,
        grounding=[], history=[],
        chain_name=chain_name, loop=loop,
    )


async def test_shim_create_bridges_to_generate_with_tools_and_returns_toolcall():
    loop = asyncio.get_running_loop()
    fake = LLMToolResponse(
        text="",
        tool_calls=[LLMToolCall(id="c1", name="echo", arguments='{"text":"hi"}')],
        model_used="gemini-3-flash",
        provider="gemini",
    )

    async def _fake_gwt(**kw):
        return fake

    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=1)
    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _fake_gwt,
    ):
        client = ShimClient()
        with use_tool_context(ctx):
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model="gemini",
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
    assert resp.choices[0].finish_reason == "tool_calls"
    assert resp.choices[0].message.tool_calls[0].function.name == "echo"


async def test_shim_short_circuits_after_terminal_talk_tool_without_llm_call():
    """Once talk.send_msg queued visible bubbles, the next Hermes protocol turn
    only asks the model to restate text we already have. Serve it locally so the
    live seller path does not burn an extra Gemini call per talk turn."""
    loop = asyncio.get_running_loop()
    bundle = TalkBundle(
        workspace_id=1,
        agent_id=2,
        hermes_run_id="hermes_run:test",
        conversation_id=3,
        actions=[
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="Salom!",
                requires_scope="telegram.send_message",
                risk_level="high",
            ),
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="Starter coins narxi 40 000 UZS.",
                requires_scope="telegram.send_message",
                risk_level="high",
            ),
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )
    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=1)
    ctx.talk_bundle = bundle

    async def _should_not_call_llm(**_kw):
        raise AssertionError("generate_with_tools should not run after terminal talk tools")

    messages = [
        {"role": "user", "content": "starter coins narxi qancha"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "talk.send_msg",
                        "arguments": '{"text":"Salom!"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "talk.send_msg",
            "tool_call_id": "call_1",
            "content": '{"status":"queued","action_kind":"send_msg","action_index":0}',
        },
    ]

    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _should_not_call_llm,
    ):
        client = ShimClient()
        with use_tool_context(ctx):
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model="gemini",
                messages=messages,
                tools=[{"type": "function", "function": {"name": "talk.send_msg", "parameters": {}}}],
            )

    assert resp.choices[0].finish_reason == "stop"
    assert resp.choices[0].message.tool_calls is None
    assert resp.choices[0].message.content == (
        "Salom!\n\nStarter coins narxi 40 000 UZS."
    )


async def test_shim_short_circuits_single_talk_tool_turn():
    """A queued talk bubble is already customer-visible output. Do not ask the
    model for another post-tool turn that can add unsolicited extra bubbles."""
    loop = asyncio.get_running_loop()
    bundle = TalkBundle(
        workspace_id=1,
        agent_id=2,
        hermes_run_id="hermes_run:test",
        conversation_id=3,
        actions=[
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="Salom!",
                requires_scope="telegram.send_message",
                risk_level="high",
            ),
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )
    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=1)
    ctx.talk_bundle = bundle
    llm_called = {"value": False}

    async def _fake_llm(**_kw):
        llm_called["value"] = True
        raise AssertionError("single talk tool follow-up should be served locally")

    messages = [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "talk.send_msg",
                        "arguments": '{"text":"Salom!"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "talk.send_msg",
            "tool_call_id": "call_1",
            "content": '{"status":"queued","action_kind":"send_msg","action_index":0}',
        },
    ]

    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _fake_llm,
    ):
        client = ShimClient()
        with use_tool_context(ctx):
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model="gemini",
                messages=messages,
                tools=[{"type": "function", "function": {"name": "talk.send_msg", "parameters": {}}}],
            )

    assert llm_called["value"] is False
    assert resp.choices[0].message.content == "Salom!"


async def test_shim_short_circuits_batch_talk_tool_turn():
    loop = asyncio.get_running_loop()
    bundle = TalkBundle(
        workspace_id=1,
        agent_id=2,
        hermes_run_id="hermes_run:test",
        conversation_id=3,
        actions=[
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="ha, SATStation haqida aytaman",
                requires_scope="telegram.send_message",
                risk_level="high",
            ),
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="platforma narxi hali tasdiqlanmagan, lekin starter coins 5 tasi 40 000 so'm",
                requires_scope="telegram.send_message",
                risk_level="high",
            ),
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )
    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=1)
    ctx.talk_bundle = bundle

    async def _should_not_call_llm(**_kw):
        raise AssertionError("batch talk follow-up should be served locally")

    messages = [
        {"role": "user", "content": "what do u have and what is cost"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "talk.send_msgs",
                        "arguments": (
                            '{"bubbles":["ha, SATStation haqida aytaman",'
                            '"platforma narxi hali tasdiqlanmagan, lekin starter coins 5 tasi 40 000 so\\u0027m"]}'
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "talk.send_msgs",
            "tool_call_id": "call_1",
            "content": '{"status":"queued","action_kind":"send_msg_batch","action_indexes":[0,1]}',
        },
    ]

    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _should_not_call_llm,
    ):
        client = ShimClient()
        with use_tool_context(ctx):
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model="gemini",
                messages=messages,
                tools=[{"type": "function", "function": {"name": "talk.send_msgs", "parameters": {}}}],
            )

    assert resp.choices[0].finish_reason == "stop"
    assert resp.choices[0].message.content == bundle.text_preview()


async def test_shim_surfaces_thought_signature_as_extra_content():
    """A tool-call carrying a Gemini 3 thought_signature must surface it as
    OpenAI ``extra_content`` so the Hermes loop ferries it back next turn.
    Hermes ``_build_assistant_message`` reads ``getattr(tc,
    "extra_content")`` — so it must be a model_extra attribute, base64-encoded."""
    import base64

    loop = asyncio.get_running_loop()
    sig = b"OPAQUE_SIG"
    fake = LLMToolResponse(
        text="",
        tool_calls=[
            LLMToolCall(
                id="c1", name="echo", arguments='{"text":"hi"}',
                thought_signature=sig,
            )
        ],
        model_used="gemini-3-flash",
        provider="gemini",
    )

    async def _fake_gwt(**kw):
        return fake

    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=1)
    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _fake_gwt,
    ):
        client = ShimClient()
        with use_tool_context(ctx):
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model="gemini",
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
    tc = resp.choices[0].message.tool_calls[0]
    extra = getattr(tc, "extra_content", None)
    assert extra == {
        "google": {"thought_signature": base64.b64encode(sig).decode("ascii")}
    }


async def test_shim_omits_extra_content_when_no_signature():
    """No signature -> no extra_content attribute (thinking-off / non-Gemini-3)."""
    loop = asyncio.get_running_loop()
    fake = LLMToolResponse(
        text="",
        tool_calls=[LLMToolCall(id="c1", name="echo", arguments="{}")],
        model_used="gemini-3-flash",
        provider="gemini",
    )

    async def _fake_gwt(**kw):
        return fake

    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=1)
    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _fake_gwt,
    ):
        client = ShimClient()
        with use_tool_context(ctx):
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model="gemini",
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
    tc = resp.choices[0].message.tool_calls[0]
    assert getattr(tc, "extra_content", None) is None


async def test_shim_create_returns_text_when_no_toolcalls():
    loop = asyncio.get_running_loop()
    fake = LLMToolResponse(text="Salom!", tool_calls=[], model_used="m", provider="gemini")

    async def _fake_gwt(**kw):
        return fake

    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=1)
    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _fake_gwt,
    ):
        client = ShimClient()
        with use_tool_context(ctx):
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model="gemini",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )
    assert resp.choices[0].message.content == "Salom!"
    assert resp.choices[0].finish_reason == "stop"


async def test_system_message_is_split_into_system_instruction():
    loop = asyncio.get_running_loop()
    fake = LLMToolResponse(text="ok", tool_calls=[], model_used="m", provider="gemini")
    captured: dict = {}

    async def _fake_gwt(**kw):
        captured.update(kw)
        return fake

    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=7)
    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _fake_gwt,
    ):
        client = ShimClient()
        with use_tool_context(ctx):
            await asyncio.to_thread(
                client.chat.completions.create,
                model="gemini",
                messages=[
                    {"role": "system", "content": "You are a helpful seller."},
                    {"role": "user", "content": "Mahsulot bormi?"},
                ],
                tools=[],
            )

    assert captured["system_instruction"] == "You are a helpful seller."
    # The contextvar's workspace_id is forwarded, not a factory-bound one.
    assert captured["workspace_id"] == 7
    # Only the non-system turn is forwarded as contents.
    assert captured["contents"] == [{"role": "user", "content": "Mahsulot bormi?"}]
    assert all(m.get("role") != "system" for m in captured["contents"])


async def test_shim_forwards_prompt_cache_from_tool_context():
    loop = asyncio.get_running_loop()
    fake = LLMToolResponse(text="ok", tool_calls=[], model_used="m", provider="gemini")
    captured: dict = {}

    async def _fake_gwt(**kw):
        captured.update(kw)
        return fake

    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=7)
    ctx.prompt_cache = {
        "provider_strategy": "gemini_cached_content",
        "cacheable": True,
        "runtime_context": {
            "cache_key": "hermes-agent-prompt:v1:7:2:seller:abc",
            "material_hash": "abc",
            "stable_payload": {"schema_version": "hermes_agent_static_context.v1"},
        },
    }
    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _fake_gwt,
    ):
        client = ShimClient()
        with use_tool_context(ctx):
            await asyncio.to_thread(
                client.chat.completions.create,
                model="gemini",
                messages=[
                    {"role": "system", "content": "AGENT.md + prompt rules"},
                    {"role": "user", "content": "hello"},
                ],
                tools=[],
            )

    assert captured["prompt_cache"] is ctx.prompt_cache


async def test_create_rejects_streaming_with_self_healing_signal():
    loop = asyncio.get_running_loop()
    fake = LLMToolResponse(text="x", tool_calls=[], model_used="m", provider="gemini")

    async def _fake_gwt(**kw):
        return fake

    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=1)
    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _fake_gwt,
    ):
        client = ShimClient()

        def _call_stream():
            return client.chat.completions.create(
                model="gemini",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                stream=True,
            )

        # The stream check is first, so it raises before needing the loop — but
        # set a ctx anyway for realism.
        with use_tool_context(ctx), pytest.raises(RuntimeError) as ei:
            await asyncio.to_thread(_call_stream)
    # Hermes loop self-healing detects "stream" + "not supported".
    msg = str(ei.value).lower()
    assert "stream" in msg
    assert "not supported" in msg


async def test_create_guards_against_on_loop_thread_call():
    # Calling create() directly on the loop thread (not via to_thread) must fail
    # loud instead of deadlocking on run_coroutine_threadsafe.
    loop = asyncio.get_running_loop()
    fake = LLMToolResponse(text="x", tool_calls=[], model_used="m", provider="gemini")

    async def _fake_gwt(**kw):
        return fake

    ctx = _ctx(loop, chain_name="CONTROL_CHAIN", workspace_id=1)
    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools",
        _fake_gwt,
    ):
        client = ShimClient()
        with use_tool_context(ctx), pytest.raises(
            RuntimeError, match="off the event-loop thread"
        ):
            client.chat.completions.create(
                model="gemini",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )


async def test_create_without_active_context_raises():
    # With no ToolContext set on the contextvar, create() must fail loud.
    client = ShimClient()

    def _call():
        return client.chat.completions.create(
            model="gemini",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )

    with pytest.raises(RuntimeError, match="requires an active ToolContext"):
        await asyncio.to_thread(_call)


async def test_outer_timeout_scales_with_chain_length():
    # ceiling = per-model (DEFAULT_TIMEOUT) * chain length + 15s slack.
    assert _outer_timeout([("gemini", "a")]) == DEFAULT_TIMEOUT * 1 + 15
    assert _outer_timeout([("gemini", "a"), ("gemini", "b"), ("gemini", "c")]) == (
        DEFAULT_TIMEOUT * 3 + 15
    )
    # Empty/None chains still get one model's worth of headroom.
    assert _outer_timeout([]) == DEFAULT_TIMEOUT * 1 + 15
    assert _outer_timeout(None) == DEFAULT_TIMEOUT * 1 + 15
