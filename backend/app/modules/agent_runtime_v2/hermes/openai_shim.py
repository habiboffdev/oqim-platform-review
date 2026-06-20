"""OpenAI-compatible shim. The packaged Hermes loop calls
``client.chat.completions.create(**api_kwargs)`` synchronously (in a worker
thread). We bridge that to the async, centralized ``generate_with_tools`` via
``run_coroutine_threadsafe`` on the main loop, and return an OpenAI-shaped
``ChatCompletion``. Keeps fallback/budget/audit; no direct provider client.

Per-run state contract: the shim is installed ONCE (``install_shim_once``) and
reads chain/workspace/loop from the ``current_tool_context`` contextvar at
``create()``-time, not from a module-global. ``asyncio.to_thread`` copies the
calling context into the worker thread, so each concurrent run sees its OWN
``ToolContext`` and the runs cannot cross-contaminate workspace/loop/chain.

Threading contract: ``_Completions.create`` MUST be invoked from a thread that
is NOT the ``ctx.loop`` thread (the adapter runs the Hermes loop via
``asyncio.to_thread``). ``run_coroutine_threadsafe`` schedules the coroutine on
``ctx.loop`` and ``fut.result()`` blocks the calling worker thread; calling this
on the loop thread itself would deadlock. ``create`` guards against that case and
raises loudly instead.
"""

from __future__ import annotations

import asyncio

from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

import app.brain.llm_policy as _policy
from app.brain.llm import (
    DEFAULT_TIMEOUT,
    LLMToolResponse,
    _thought_signature_to_extra_content,
    generate_with_tools,
)
from app.modules.agent_runtime_v2.budget import BudgetExceededError
from app.modules.agent_runtime_v2.hermes._bootstrap import ensure_hermes_runtime
from app.modules.agent_runtime_v2.hermes.tool_context import current_tool_context

# Wall-clock slack added on top of the per-model budget so the outer
# ``fut.result`` ceiling never fires before the inner fallback chain has had a
# fair chance at every model (network jitter, trace/budget overhead).
_TIMEOUT_SLACK_S = 15
# Sentinel base_url for the OQIM shim. Never hit over HTTP — the shim intercepts
# chat.completions.create. Shared with engine.py (AIAgent construction) and
# vendor_patches.py (the context-length resolver) so the magic string lives once.
OQIM_SHIM_BASE_URL = "http://oqim.invalid"
_TERMINAL_TALK_TOOLS = frozenset(
    {
        "talk.send_msg",
        "talk.send_msgs",
        "talk.send_media",
        "talk.send_reaction",
        "talk.reply_to_msg",
        "talk.delete_message",
    }
)


class BudgetExceededFailFastError(ValueError):
    """Local non-retryable wrapper so Hermes does not retry OQIM budget blocks."""


def _outer_timeout(chain) -> float:
    """Wall-clock ceiling for the bridge, derived from the fallback chain.

    ``generate_with_tools`` applies its ``timeout`` PER MODEL across the chain,
    so the single ``run_coroutine_threadsafe`` ceiling must cover every model
    (else a long chain would orphan in-flight work mid-coroutine). Empty chains
    still get one model's worth of headroom.
    """
    return DEFAULT_TIMEOUT * max(1, len(chain or [])) + _TIMEOUT_SLACK_S


def _openai_tools_to_schemas(tools: list[dict] | None) -> list[dict]:
    """Flatten OpenAI tool specs (``{"type":"function","function":{...}}``) into
    the bare ``{name, description, parameters}`` schema ``generate_with_tools``
    expects."""
    out: list[dict] = []
    for t in tools or []:
        fn = t.get("function", t)  # OpenAI nests under "function"
        if fn.get("name"):
            out.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
            )
    return out


def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Separate ``{"role":"system"}`` turn(s) from the conversation.

    Gemini takes the system prompt via ``config.system_instruction``, not as a
    content turn, so we pull system messages out and join their text.
    """
    sys_parts: list[str] = []
    rest: list[dict] = []
    for m in messages or []:
        if m.get("role") == "system":
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                sys_parts.append(c)
        else:
            rest.append(m)
    return ("\n\n".join(sys_parts) or None), rest


def _terminal_talk_preview(messages: list[dict] | None, ctx) -> str | None:
    """Return queued talk text when Hermes is only asking for a post-tool
    restatement after terminal talking tools.

    Hermes's normal protocol does: assistant(tool_call) -> tool(result) -> model
    final text. For OQIM talking tools, the tool arguments are already the
    customer-visible output and the tool handler already queued audited actions,
    so the follow-up model call is pure restatement cost and can invent extra
    bubbles. We only short-circuit when the most recent assistant tool batch
    contains talk tools and every following tool result belongs to that batch.
    If Hermes wants multiple bubbles, it should emit multiple talk tool calls in
    that one assistant turn.
    """
    bundle = getattr(ctx, "talk_bundle", None)
    if bundle is None or not getattr(bundle, "actions", None):
        return None

    tail = list(messages or [])
    while tail and tail[-1].get("role") == "tool":
        tail.pop()
    if not tail:
        return None
    assistant = tail[-1]
    if assistant.get("role") != "assistant":
        return None

    tool_calls = assistant.get("tool_calls") or []
    if not tool_calls:
        return None
    tool_names: list[str] = []
    tool_ids: set[str] = set()
    for tc in tool_calls:
        fn = tc.get("function") if isinstance(tc, dict) else None
        name = fn.get("name") if isinstance(fn, dict) else None
        tc_id = tc.get("id") if isinstance(tc, dict) else None
        if not name or not tc_id:
            return None
        tool_names.append(name)
        tool_ids.add(str(tc_id))
    if not tool_names or any(name not in _TERMINAL_TALK_TOOLS for name in tool_names):
        return None

    following = (messages or [])[len(tail):]
    if len(following) != len(tool_ids):
        return None
    answered_ids = {
        str(msg.get("tool_call_id"))
        for msg in following
        if msg.get("role") == "tool" and msg.get("name") in _TERMINAL_TALK_TOOLS
    }
    if answered_ids != tool_ids:
        return None
    preview = bundle.text_preview()
    return preview.strip() if preview else None


def _to_chat_completion(resp) -> ChatCompletion:
    """Build an OpenAI ``ChatCompletion`` from an ``LLMToolResponse`` so the
    Hermes loop can read ``.choices[0].message.{content,tool_calls}`` and
    ``.choices[0].finish_reason``."""
    if resp.tool_calls:
        tcs = []
        for tc in resp.tool_calls:
            tc_kwargs: dict = {
                "id": tc.id,
                "type": "function",
                "function": Function(name=tc.name, arguments=tc.arguments),
            }
            # Ferry the Gemini 3 thought_signature through the loop as the
            # documented OpenAI ``extra_content`` passthrough. The Hermes
            # ``_build_assistant_message`` reads ``getattr(tool_call,
            # "extra_content")`` and persists it onto the appended history
            # message; ``_openai_messages_to_gemini_contents`` then rehydrates it
            # onto the function-call Part on the next turn (else Gemini 400s with
            # "missing a thought_signature"). ChatCompletionMessageToolCall has
            # extra="allow", so this rides as a model_extra field.
            extra_content = _thought_signature_to_extra_content(
                getattr(tc, "thought_signature", None)
            )
            if extra_content is not None:
                tc_kwargs["extra_content"] = extra_content
            tcs.append(ChatCompletionMessageToolCall(**tc_kwargs))
        msg = ChatCompletionMessage(
            role="assistant", content=resp.text or None, tool_calls=tcs
        )
        finish = "tool_calls"
    else:
        msg = ChatCompletionMessage(role="assistant", content=resp.text or "")
        finish = "stop"
    # ``usage`` is intentionally omitted: per-call token accounting (budget +
    # TokenTracker) already happens inside ``generate_with_tools``; surfacing it
    # here too would risk double-counting.
    return ChatCompletion(
        id="oqim-shim",
        created=0,
        model=resp.model_used or "gemini",
        object="chat.completion",
        choices=[Choice(index=0, finish_reason=finish, message=msg)],
    )


class _Completions:
    def create(self, **kwargs):
        # Reject streaming. The Hermes loop prefers streaming and only
        # auto-disables it for a Mock client, so in a headless OQIM run it would
        # call create(stream=True) and then ITERATE a non-iterable ChatCompletion
        # (TypeError). Raising with "stream"+"not supported" trips the loop's
        # self-healing (it sets _disable_streaming=True and retries
        # non-streaming) instead of crashing.
        if kwargs.get("stream"):
            raise RuntimeError("streaming is not supported by the OQIM shim")

        # Per-run state comes from the contextvar (copied into this worker thread
        # by asyncio.to_thread), NOT from a module-global. This is what kills the
        # concurrency race: two interleaved runs each read their own ToolContext.
        ctx = current_tool_context.get()
        if ctx is None or ctx.loop is None:
            raise RuntimeError(
                "OQIM shim.create requires an active ToolContext with a loop"
            )

        # Off-thread guard. run_coroutine_threadsafe(...).result() blocks the
        # caller until the loop runs the coroutine; if create() is ever called
        # ON the loop thread it would deadlock. Fail loud instead.
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is ctx.loop:
            raise RuntimeError("OQIM shim.create must run off the event-loop thread")

        chain = getattr(_policy, ctx.chain_name)
        system_instruction, convo = _split_system(kwargs.get("messages", []))
        tools = _openai_tools_to_schemas(kwargs.get("tools"))
        allowed_tool_names = getattr(ctx, "allowed_tool_names", None)
        if allowed_tool_names is not None:
            tools = [tool for tool in tools if tool.get("name") in allowed_tool_names]
        terminal_talk = _terminal_talk_preview(convo, ctx)
        if terminal_talk:
            return _to_chat_completion(
                LLMToolResponse(
                    text=terminal_talk,
                    tool_calls=[],
                    model_used=str(kwargs.get("model") or "gemini"),
                    provider="oqim-local",
                )
            )
        # When talk tools are granted, the terminal customer-visible output IS
        # a tool call — force function calling (Gemini mode=ANY) so the model
        # cannot answer in plain text and bypass bubbles/pacing/audit (live
        # failure 2026-06-11: flash-lite skipped talk.send_msgs). The talk
        # restatement shortcut above ends the loop once talk has emitted.
        talk_tools_present = any(
            str(tool.get("name") or "").startswith("talk.") for tool in tools
        )
        # The forced commercial-finalization pass grants only
        # conversation.set_state (no talk tool); force the named tool so mode=ANY
        # cannot answer in plain text. allowed_function_names pins ANY to the
        # finalize grant so it cannot wander to another tool. Both default
        # off/None -> the existing talk-forcing path is unchanged.
        force_named_tool = bool(getattr(ctx, "force_tool_call", False))
        force_function_calling = talk_tools_present or force_named_tool
        allowed_function_names = (
            [tool["name"] for tool in tools] if force_named_tool else None
        )
        coro = generate_with_tools(
            chain=chain,
            contents=convo,
            tools=tools,
            workspace_id=ctx.workspace_id,
            operation="hermes_reply",
            system_instruction=system_instruction,
            prompt_cache=getattr(ctx, "prompt_cache", None),
            force_function_calling=force_function_calling,
            allowed_function_names=allowed_function_names,
            current_turn_media=getattr(ctx, "current_turn_media", None) or None,
            live_media_text=getattr(ctx, "live_media_text", None),
            timeout=DEFAULT_TIMEOUT,  # per-model; outer ceiling covers the chain
        )
        fut = asyncio.run_coroutine_threadsafe(coro, ctx.loop)
        # Ceiling derived from chain length so a multi-model fallback chain
        # (operation "hermes_reply" is NOT a TASK_POLICY, so it is not collapsed)
        # is never orphaned mid-coroutine by a hardcoded wall-clock timeout.
        try:
            return _to_chat_completion(fut.result(timeout=_outer_timeout(chain)))
        except BudgetExceededError as exc:
            raise BudgetExceededFailFastError(str(exc)) from exc


class ShimClient:
    def __init__(self, **_ignored):
        self.chat = type("_Chat", (), {"completions": _Completions()})()

    def with_options(self, **kw):
        return self

    def close(self):
        pass


def oqim_aux_text_client_or_none(*, async_mode: bool, is_vision: bool) -> ShimClient | None:
    """Return an OQIM ``ShimClient`` for Hermes's AUXILIARY text work when we are
    inside an OQIM agent run, else ``None`` (caller falls through to the upstream
    provider router).

    "Inside a run" = an OQIM ``ToolContext`` is active in this context. The shim
    reads workspace/chain/loop from that contextvar at create()-time and routes
    through OQIM's Gemini (budget + fallback), so auxiliary tasks (compression,
    summarization, the boot-time feasibility probe, the main-loop fallback) stop
    hitting Nous/OpenRouter/direct-api-key.

    Guarded to the contract the SYNC shim can serve: ``async_mode`` clients and
    ``is_vision`` (image) calls fall through to the upstream router untouched.
    """
    if async_mode or is_vision:
        return None
    if current_tool_context.get() is None:
        return None
    return ShimClient()


def install_shim_once() -> None:
    """Install the OQIM shim as run_agent.OpenAI ONCE. The shim reads per-run
    chain/workspace/loop from the contextvar at create()-time, so concurrent
    runs don't race a per-run module-global (the prior bug)."""
    ensure_hermes_runtime()
    import run_agent
    if getattr(run_agent, "_oqim_shim_installed", False):
        return
    run_agent.OpenAI = lambda **kw: ShimClient(**kw)
    run_agent._oqim_shim_installed = True
