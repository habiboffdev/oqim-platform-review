import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.brain.llm as _llm
from app.brain.llm import LLMToolResponse, generate_with_tools
from app.brain.llm_policy import CONTROL_CHAIN

pytestmark = pytest.mark.asyncio


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_openai_messages_to_gemini_contents_translates_a_tool_conversation():
    # Pure synchronous translator test; module-level pytestmark adds the asyncio
    # marker, which is a no-op here (filtered to keep the suite warning-clean).
    from app.brain.llm import _openai_messages_to_gemini_contents
    msgs = [
        {"role": "user", "content": "Narxi qancha?"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "example_lookup", "arguments": '{"query": "narx"}'}}]},
        {"role": "tool", "name": "example_lookup", "tool_call_id": "c1",
         "content": '{"status": "ok", "facts": ["Narxi: 250000"]}'},
    ]
    contents = _openai_messages_to_gemini_contents(msgs)
    assert len(contents) == 3
    # user turn
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "Narxi qancha?"
    # assistant tool-call turn -> role "model" with a function_call part
    assert contents[1].role == "model"
    assert contents[1].parts[0].function_call.name == "example_lookup"
    assert contents[1].parts[0].function_call.args == {"query": "narx"}
    # tool result -> role "tool" with a function_response part (name preserved, JSON parsed into a dict)
    assert contents[2].role == "tool"
    assert contents[2].parts[0].function_response.name == "example_lookup"
    assert isinstance(contents[2].parts[0].function_response.response, dict)
    assert "facts" in contents[2].parts[0].function_response.response


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_openai_messages_to_gemini_contents_groups_parallel_tool_results():
    """Gemini requires all function_response parts for one function-call turn
    to appear in the same user Content. Hermes appends OpenAI-style tool
    results as adjacent role=tool messages, one per tool_call_id."""
    from app.brain.llm import _openai_messages_to_gemini_contents

    msgs = [
        {"role": "user", "content": "answer in bubbles"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "talk.send_msg", "arguments": '{"text": "Salom"}'},
                },
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "talk.send_msg", "arguments": '{"text": "Yordam beraymi?"}'},
                },
            ],
        },
        {
            "role": "tool",
            "name": "talk.send_msg",
            "tool_call_id": "c1",
            "content": '{"status": "queued", "index": 0}',
        },
        {
            "role": "tool",
            "name": "talk.send_msg",
            "tool_call_id": "c2",
            "content": '{"status": "queued", "index": 1}',
        },
    ]

    contents = _openai_messages_to_gemini_contents(msgs)

    assert len(contents) == 3
    assert contents[1].role == "model"
    assert len(contents[1].parts) == 2
    assert contents[2].role == "tool"
    assert len(contents[2].parts) == 2
    assert contents[2].parts[0].function_response.name == "talk.send_msg"
    assert contents[2].parts[1].function_response.name == "talk.send_msg"


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_translator_restores_gemini_thought_signature_from_extra_content():
    """Gemini 3 requires the thought_signature to be echoed back on the
    function-call part. The shim carries it as OpenAI ``extra_content``; the
    translator must rehydrate it onto the reconstructed Part as bytes."""
    from app.brain.llm import _openai_messages_to_gemini_contents
    sig = b"OPAQUE_SIG_BYTES"
    sig_b64 = base64.b64encode(sig).decode("ascii")
    msgs = [
        {"role": "assistant", "content": None,
         "tool_calls": [{
             "id": "c1", "type": "function",
             "function": {"name": "example_lookup", "arguments": '{"query": "narx"}'},
             "extra_content": {"google": {"thought_signature": sig_b64}},
         }]},
    ]
    contents = _openai_messages_to_gemini_contents(msgs)
    assert contents[0].role == "model"
    part = contents[0].parts[0]
    assert part.function_call.name == "example_lookup"
    # signature decoded back to the original bytes on the Part
    assert part.thought_signature == sig


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_translator_tolerates_missing_thought_signature():
    """No extra_content -> no thought_signature, no crash (e.g. thinking off)."""
    from app.brain.llm import _openai_messages_to_gemini_contents
    msgs = [
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "echo", "arguments": "{}"}}]},
    ]
    contents = _openai_messages_to_gemini_contents(msgs)
    assert contents[0].parts[0].function_call.name == "echo"
    assert contents[0].parts[0].thought_signature is None


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_build_tool_config_omits_tools_when_no_schemas():
    """Gemini rejects a Tool with empty function_declarations
    ('tools[0].tool_type ... must have one initialized field'). The Hermes
    iteration-limit summary call sends NO tools, so build_tool_config([]) must
    omit the tools field entirely (tools=None), not emit an empty Tool."""
    from app.brain.llm import build_tool_config
    cfg = build_tool_config([], system_instruction="sys")
    assert cfg.tools is None
    # a non-empty schema set still produces one Tool with declarations
    cfg2 = build_tool_config(
        [{"name": "echo", "parameters": {"type": "object", "properties": {}}}]
    )
    assert cfg2.tools is not None
    assert len(cfg2.tools[0].function_declarations) == 1


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_normalize_tool_response_captures_thought_signature_from_parts():
    """The raw Gemini response carries thought_signature on the function-call
    PART (response.function_calls drops it). _normalize_tool_response must read
    the parts and surface the signature on the LLMToolCall."""
    from app.brain.llm import _normalize_tool_response
    sig = b"SIG_FROM_GEMINI"
    fc_part = SimpleNamespace(
        function_call=SimpleNamespace(name="example_lookup", args={"q": "x"}),
        function_response=None,
        text=None,
        thought_signature=sig,
    )
    raw = SimpleNamespace(
        text="",
        function_calls=[fc_part.function_call],
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[fc_part]))],
        usage_metadata=None,
    )
    out = _normalize_tool_response(raw, "gemini-3-flash", "gemini")
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "example_lookup"
    assert out.tool_calls[0].thought_signature == sig


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_normalize_tool_response_separates_gemini_thought_summary_from_output():
    from app.brain.llm import _normalize_tool_response

    thought_part = SimpleNamespace(
        function_call=None,
        text="I should answer the greeting briefly.",
        thought=True,
    )
    text_part = SimpleNamespace(
        function_call=None,
        text="Assalomu alaykum!",
        thought=False,
    )
    raw = SimpleNamespace(
        text="I should answer the greeting briefly.Assalomu alaykum!",
        function_calls=[],
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(parts=[thought_part, text_part])
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=100,
            candidates_token_count=12,
            cached_content_token_count=33,
            thoughts_token_count=7,
        ),
    )

    out = _normalize_tool_response(raw, "gemini-3.5-flash", "gemini")

    assert out.text == "Assalomu alaykum!"
    assert out.thought_summaries == ["I should answer the greeting briefly."]
    assert out.usage == {
        "input_tokens": 100,
        "output_tokens": 12,
        "cached_content_tokens": 33,
        "thought_tokens": 7,
    }

_TOOLS = [{"name": "echo", "description": "Echo.",
           "parameters": {"type": "object",
                          "properties": {"text": {"type": "string"}}, "required": ["text"]}}]


def _resp_with_usage(*, prompt=12, candidates=4):
    """A fake Gemini response carrying usage_metadata (no function calls)."""
    return SimpleNamespace(
        text="Salom!",
        function_calls=[],
        candidates=[],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=candidates
        ),
    )


async def test_returns_tool_calls_when_model_emits_function_call():
    fake_fc = SimpleNamespace(name="echo", args={"text": "hi"})
    fake_resp = SimpleNamespace(text="", function_calls=[fake_fc],
                                candidates=[], usage_metadata=None)
    with patch("app.brain.llm.LLMClient._call_one_model", new_callable=AsyncMock) as m:
        m.return_value = (fake_resp, "gemini-3-flash", "gemini")
        out = await generate_with_tools(
            chain=CONTROL_CHAIN,
            contents=[{"role": "user", "content": "say hi"}],
            tools=_TOOLS, operation="test_tools", workspace_id=None)
    assert isinstance(out, LLMToolResponse)
    assert out.text == ""
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "echo"
    assert json.loads(out.tool_calls[0].arguments) == {"text": "hi"}


async def test_returns_text_when_model_emits_no_function_call():
    fake_resp = SimpleNamespace(text="Salom!", function_calls=[], candidates=[], usage_metadata=None)
    with patch("app.brain.llm.LLMClient._call_one_model", new_callable=AsyncMock) as m:
        m.return_value = (fake_resp, "gemini-3-flash", "gemini")
        out = await generate_with_tools(chain=CONTROL_CHAIN,
            contents=[{"role": "user", "content": "hi"}], tools=_TOOLS, operation="test_tools")
    assert out.text == "Salom!"
    assert out.tool_calls == []


async def test_records_token_usage_to_token_tracker():
    """Audit parity: a tool turn carrying usage_metadata is recorded in the token ledger."""
    tracker = MagicMock()
    tracker.record = AsyncMock()
    with patch("app.brain.llm.LLMClient._call_one_model", new_callable=AsyncMock) as m, \
            patch("app.brain.token_tracker.get_token_tracker", return_value=tracker), \
            patch("app.brain.token_tracker.get_token_context", return_value=(None, None)):
        m.return_value = (_resp_with_usage(prompt=12, candidates=4), "gemini-3-flash", "gemini")
        await generate_with_tools(
            chain=CONTROL_CHAIN,
            contents=[{"role": "user", "content": "hi"}],
            tools=_TOOLS, operation="test_tools", workspace_id=None)
    tracker.record.assert_awaited_once()
    kwargs = tracker.record.await_args.kwargs
    assert kwargs["operation"] == "test_tools"
    assert kwargs["provider"] == "gemini"
    assert kwargs["input_tokens"] == 12
    assert kwargs["output_tokens"] == 4
    assert kwargs["workspace_id"] == 1  # legacy fallback when no explicit/ctx workspace


async def test_emits_trace_events_for_attempt_and_success():
    """Audit parity: tool turns appear in the trace timeline (attempt + success)."""
    events: list[tuple[str, str]] = []

    async def _fake_emit(stage, event, /, **payload):
        events.append((stage, event))
        return None

    with patch("app.brain.llm.LLMClient._call_one_model", new_callable=AsyncMock) as m, \
            patch(
                "app.modules.agent_runtime_v2.trace.emit_trace_event",
                new=_fake_emit,
            ):
        m.return_value = (_resp_with_usage(), "gemini-3-flash", "gemini")
        await generate_with_tools(
            chain=CONTROL_CHAIN,
            contents=[{"role": "user", "content": "hi"}],
            tools=_TOOLS, operation="test_tools", workspace_id=None)
    assert ("llm", "attempt") in events
    assert ("llm", "success") in events


async def test_generate_with_tools_forwards_prompt_cache_to_gemini_and_trace():
    """Hermes tool-loop calls should use the same Gemini cached-content path as
    normal generation, and the trace should expose cache metadata."""
    attempt_payload: dict = {}

    async def _fake_emit(stage, event, /, **payload):
        if stage == "llm" and event == "attempt":
            attempt_payload.update(payload)
        return None

    prompt_cache = {
        "provider_strategy": "gemini_cached_content",
        "cacheable": True,
        "runtime_context": {
            "cache_key": "hermes-agent-prompt:v1:1:2:seller:abc",
            "material_hash": "abc123",
            "stable_payload": {"schema_version": "hermes_agent_static_context.v1"},
        },
    }

    with patch("app.brain.llm.LLMClient._call_one_model", new_callable=AsyncMock) as m, \
            patch(
                "app.modules.agent_runtime_v2.trace.emit_trace_event",
                new=_fake_emit,
            ):
        m.return_value = (_resp_with_usage(), "gemini-3-flash", "gemini")
        await generate_with_tools(
            chain=CONTROL_CHAIN,
            contents=[{"role": "user", "content": "hi"}],
            tools=_TOOLS,
            operation="test_tools",
            workspace_id=None,
            prompt_cache=prompt_cache,
        )

    assert m.await_args.kwargs["prompt_cache"] == prompt_cache
    cache_summary = attempt_payload["request_summary"]["prompt_cache"]
    assert cache_summary["provider_strategy"] == "gemini_cached_content"
    assert cache_summary["cacheable"] is True
    assert cache_summary["cache_key"] == "hermes-agent-prompt:v1:1:2:seller:abc"
    assert cache_summary["material_hash"] == "abc123"


async def test_emits_error_trace_event_when_chain_exhausts():
    """A failing model emits an 'error' trace event and the chain raises."""
    events: list[tuple[str, str]] = []

    async def _fake_emit(stage, event, /, **payload):
        events.append((stage, event))
        return None

    with patch("app.brain.llm.LLMClient._call_one_model", new_callable=AsyncMock) as m, \
            patch(
                "app.modules.agent_runtime_v2.trace.emit_trace_event",
                new=_fake_emit,
            ):
        m.side_effect = RuntimeError("model down")
        with pytest.raises(RuntimeError, match="chain exhausted"):
            await generate_with_tools(
                chain=CONTROL_CHAIN,
                contents=[{"role": "user", "content": "hi"}],
                tools=_TOOLS, operation="test_tools", workspace_id=None)
    assert ("llm", "error") in events
    assert ("llm", "success") not in events


async def test_system_instruction_threaded_into_build_tool_config():
    """system_instruction is passed through to build_tool_config (Gemini system prompt path)."""
    real_resp = SimpleNamespace(text="ok", function_calls=[], candidates=[], usage_metadata=None)
    with patch("app.brain.llm.build_tool_config") as bt, \
            patch("app.brain.llm.LLMClient._call_one_model", new_callable=AsyncMock) as m:
        bt.return_value = SimpleNamespace(system_instruction="SYS")  # opaque config object
        m.return_value = (real_resp, "gemini-3-flash", "gemini")
        out = await generate_with_tools(
            chain=CONTROL_CHAIN,
            contents=[{"role": "user", "content": "hi"}],
            tools=_TOOLS, operation="test_tools",
            system_instruction="SYS", workspace_id=None)
    assert out.text == "ok"
    bt.assert_called_once()
    # the system prompt is forwarded as the build_tool_config system_instruction kwarg
    assert bt.call_args.kwargs.get("system_instruction") == "SYS"
    # and the config it returned is the one handed to the model seam
    assert m.await_args.kwargs["config"].system_instruction == "SYS"


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_cached_content_config_strips_tools_and_system_instruction():
    """Gemini cached content owns system_instruction + tool declarations.
    Repeating any of them on the generate request is a provider 400
    ('Tool config, tools and system instruction should not be set...') —
    the exact error that hit the live pilot chat on 2026-06-08."""
    from google.genai import types

    from app.brain.llm import _with_cached_content_config

    config = types.GenerateContentConfig(
        system_instruction="sys",
        tools=[
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(name="t", description="d")
                ]
            )
        ],
        temperature=1.0,
    )
    out = _with_cached_content_config(config, "cachedContents/abc")
    assert out.cached_content == "cachedContents/abc"
    assert out.system_instruction is None
    assert out.tools is None
    assert out.tool_config is None
    assert out.automatic_function_calling is None
    assert out.temperature == 1.0


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_build_tool_config_force_function_calling_sets_any_mode():
    """Reply turns whose terminal output IS a talk tool must force Gemini
    function calling (mode=ANY) — flash-lite skipped talk.send_msgs on a live
    turn (2026-06-11) and a plain-text wall reached the customer, bypassing
    bubbles/pacing/audit. Forcing only applies when tools exist; the
    iteration-limit summary call (no tools) must stay unforced."""
    from app.brain.llm import build_tool_config

    schema = [{"name": "talk.send_msgs", "parameters": {"type": "object", "properties": {}}}]
    forced = build_tool_config(schema, force_function_calling=True)
    assert forced.tool_config is not None
    assert str(forced.tool_config.function_calling_config.mode) .endswith("ANY")

    unforced = build_tool_config(schema)
    assert unforced.tool_config is None

    no_tools = build_tool_config([], force_function_calling=True)
    assert no_tools.tool_config is None


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_build_tool_config_force_with_allowed_function_names():
    """The forced finalization pass must pin Gemini to a SINGLE named tool
    (conversation.set_state) so mode=ANY cannot wander to another granted tool.
    allowed_function_names rides into FunctionCallingConfig; omitting it keeps
    the field unset (back-compat with existing talk-forcing)."""
    from app.brain.llm import build_tool_config

    schema = [
        {
            "name": "conversation.set_state",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    forced = build_tool_config(
        schema,
        force_function_calling=True,
        allowed_function_names=["conversation.set_state"],
    )
    assert forced.tool_config is not None
    fcc = forced.tool_config.function_calling_config
    assert str(fcc.mode).endswith("ANY")
    assert list(fcc.allowed_function_names) == ["conversation.set_state"]

    # Back-compat: omitting allowed_function_names leaves it unset.
    forced_no_names = build_tool_config(schema, force_function_calling=True)
    assert forced_no_names.tool_config is not None
    assert not forced_no_names.tool_config.function_calling_config.allowed_function_names


def test_build_tool_config_threads_temperature():
    """Pilot hardening (2026-06-18): the seller turn runs at a lower temperature
    to cut Gemini 3 Flash confabulation (Google guidance: low temp sharply
    reduces invention on factual tasks). build_tool_config must thread it; the
    default stays 1.0 so every other tool loop is unchanged."""
    from app.brain.llm import build_tool_config

    schema = [{"name": "talk.send_msgs", "parameters": {"type": "object", "properties": {}}}]
    assert build_tool_config(schema).temperature == 1.0
    assert build_tool_config(schema, temperature=0.3).temperature == 0.3


def test_seller_turn_operation_resolves_to_low_temperature():
    """operation 'hermes_reply' aliases to the agent_turn_generation policy, which
    now pins temperature 0.3 (factual). Every other operation keeps 1.0, so only
    the customer-facing seller reply is made more deterministic."""
    from app.brain.llm import _tool_loop_temperature

    assert _tool_loop_temperature("hermes_reply") == 0.3
    assert _tool_loop_temperature("agent_turn_generation") == 0.3
    assert _tool_loop_temperature("structured_json") == 1.0
    assert _tool_loop_temperature(None) == 1.0


@pytest.mark.asyncio
async def test_generate_with_tools_forwards_live_media_text(monkeypatch):
    """live_media_text is forwarded from the module-level wrapper through the
    method boundary to _openai_messages_to_gemini_contents (behavioral test).
    Monkeypatches the boundary function so we can observe what it receives."""
    captured = {}

    def fake_boundary(messages, current_turn_media=None, live_media_text=None):
        captured["live_media_text"] = live_media_text
        captured["media"] = current_turn_media
        import google.genai.types as t
        return [t.Content(role="user", parts=[t.Part.from_text(text="x")])]

    monkeypatch.setattr(_llm, "_openai_messages_to_gemini_contents", fake_boundary)

    async def fake_call_one_model(self, **kwargs):
        class _Resp:
            class usage_metadata:  # noqa: N801
                prompt_token_count = 1
                candidates_token_count = 1
            candidates = []
        return _Resp(), kwargs["model"], kwargs["provider"]

    monkeypatch.setattr(_llm.LLMClient, "_call_one_model", fake_call_one_model)

    client = _llm.LLMClient()
    await client.generate_with_tools(
        chain=[("gemini", "gemini-3-flash-preview")],
        contents=[{"role": "user", "content": "hi"}],
        tools=[],
        live_media_text="[Voice message]",
    )
    assert captured["live_media_text"] == "[Voice message]"


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_native_media_summary_built_for_trace():
    from app.brain.llm import _native_media_trace_summary
    from app.brain.media_parts import TurnMediaPart

    parts = [TurnMediaPart(message_ref="message:7", kind="audio",
                           mime_type="audio/ogg", source="inline", data=b"OggS12")]
    summary = _native_media_trace_summary(parts)
    assert summary == [{"ref": "message:7", "kind": "audio",
                        "mime": "audio/ogg", "bytes": 6, "source": "inline"}]
    assert _native_media_trace_summary(None) == []
