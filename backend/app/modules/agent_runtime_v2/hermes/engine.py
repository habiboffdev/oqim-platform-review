"""HermesEngineAdapter: OQIM's adapter around the packaged Hermes AIAgent loop."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.hermes._bootstrap import ensure_hermes_runtime
from app.modules.agent_runtime_v2.hermes.openai_shim import (
    OQIM_SHIM_BASE_URL,
    install_shim_once,
)
from app.modules.agent_runtime_v2.hermes.oqim_tools import register_oqim_tools
from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext, use_tool_context
from app.modules.agent_runtime_v2.hermes.vendor_patches import apply_vendor_patches
from app.modules.agent_runtime_v2.reply_runtime import (
    ManagedRuntimePrompt,
    ReplyResult,
    compose_hermes_system_prompt,
    compose_hermes_turn,
    compose_owner_operator_system_prompt,
    load_hermes_reply_prompt,
)
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfile
from app.modules.agent_talking.contracts import TalkBundle, TalkingPolicy
from app.modules.agent_talking.output_normalize import normalize_outgoing_text
from app.modules.conversation_turns.active_runs import active_turn_run_registry

logger = logging.getLogger(__name__)

_GEMINI_CACHED_CONTENT_MIN_TOKENS = 4096
_APPROX_CHARS_PER_TOKEN = 4


def _normalize_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(tool_call, dict):
        return tool_call
    function = tool_call.get("function")
    if isinstance(function, dict) and isinstance(function.get("arguments"), str):
        return {
            **tool_call,
            "function": {
                **function,
                "arguments": normalize_outgoing_text(function["arguments"]),
            },
        }
    return tool_call


def _normalize_history_message(message: dict[str, Any]) -> dict[str, Any]:
    """Normalize the model's OWN prior text on replay (A3) so it does not
    re-learn banned output form (em-dashes) from its own past turns. The seller's
    reply text lives in three places on an assistant turn: the `content` field,
    the `talk.*` tool-call arguments (a JSON string), and the tool-result
    `content`. Customer (user) turns are left verbatim. em-dash -> comma does not
    break the arguments JSON (the em-dash only appears inside string values)."""
    if message.get("role") not in ("assistant", "tool"):
        return message
    updated = dict(message)
    content = updated.get("content")
    if isinstance(content, str):
        updated["content"] = normalize_outgoing_text(content)
    tool_calls = updated.get("tool_calls")
    if isinstance(tool_calls, list):
        updated["tool_calls"] = [_normalize_tool_call(tc) for tc in tool_calls]
    return updated


# Bookkeeping recording tools that run in a forced finalization pass AFTER the
# reply (set_state records the turn's commercial state for the action/setup read
# path; conversation.record drives the seller records pass). Their assistant
# tool-call + tool-result exchange is pure bookkeeping; replaying it pollutes the
# next customer turn's context, so it is filtered out of the replay window (it
# never reached the customer and Hermes has the live facts).
_NON_REPLAYED_RECORDING_TOOLS = frozenset(
    {"conversation.set_state", "conversation.record"}
)


def _tool_call_names(message: dict[str, Any]) -> list[str]:
    """Names of the tools an assistant turn called. Handles both stored shapes:
    flat ``{"name": ...}`` (run_agent's session writeback) and nested
    ``{"function": {"name": ...}}`` (OpenAI message form)."""
    names: list[str] = []
    for tc in message.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        name = fn.get("name") if isinstance(fn, dict) else tc.get("name")
        if name:
            names.append(str(name))
    return names


def _is_recording_bookkeeping_message(message: dict[str, Any]) -> bool:
    """True for the assistant tool-call turn (or its paired tool result) of a
    non-replayed recording tool — and ONLY those. A talk/customer turn, or a
    mixed assistant turn that also called a real tool, is never dropped."""
    role = message.get("role")
    if role == "assistant":
        names = _tool_call_names(message)
        return bool(names) and all(
            name in _NON_REPLAYED_RECORDING_TOOLS for name in names
        )
    if role == "tool":
        return str(message.get("tool_name") or "") in _NON_REPLAYED_RECORDING_TOOLS
    return False


def _build_replay_history(
    session_db: Any | None, hermes_session_id: str | None
) -> list[dict[str, Any]]:
    """Normalized replay window for run_conversation. Strips system turns, drops
    the forced set_state bookkeeping exchange (replay hygiene — it never reached
    the customer), drops None-valued storage fields, and normalizes the model's
    own prior text (A3). Length is intentionally NOT capped here: Hermes owns
    conversation memory and its context compressor manages length (see
    run_conversation below)."""
    if session_db is None or not hermes_session_id:
        return []
    stored = getattr(session_db, "messages", {}).get(hermes_session_id) or []
    replayed = [
        {k: v for k, v in message.items() if v is not None}
        for message in stored
        if message.get("role") != "system"
        and not _is_recording_bookkeeping_message(message)
    ]
    return [_normalize_history_message(message) for message in replayed]


def _live_turn_override(
    turn: str, customer_message: str, live_media_text: str | None
) -> str | None:
    """Bare-media variant of the composed turn, for the LIVE Gemini call only.

    The stored/replayed turn keeps the labeled ``[Voice message: "transcript"]``
    inside its ``<turn_context>`` wrapper (conversation_state + ``<current_message>``
    framing) for recall. For the live call we keep that SAME wrapper but show only
    the bare ``[Voice message]`` marker — the audio/image Part carries the content.
    So swap ONLY the inner media rendering, never the whole turn: replacing the
    whole turn stripped conversation_state and made the model "address the voice
    message" instead of perceiving it (2026-06-13 live bug). Returns None when
    there is nothing to swap (text-only turns / no staged media), leaving the live
    turn identical to the stored one.
    """
    if not live_media_text or live_media_text == customer_message:
        return None
    if customer_message not in turn:
        return None  # defensive: wrapper shape changed; don't corrupt the turn
    return turn.replace(customer_message, live_media_text)


class HermesEngineAdapter:
    async def run(self, *, config: AgentConfig, profile: RuntimeProfile, customer_message: str,
                  grounding: list[str], history: list[str] | None = None,
                  voice_examples: list[str] | None = None,
                  authority_warnings: list[str] | None = None,
                  conversation_id: int | None = None,
                  hermes_run_id: str | None = None,
                  reply_to_message_ref: str | None = None,
                  turn_session_id: int | None = None,
                  turn_revision_start: int | None = None,
                  agent_kind: str = "custom_agent",
                  hermes_session_id: str | None = None,
                  session_db: Any | None = None,
                  agent_session_id: int | None = None,
                  conversation_state: dict[str, Any] | None = None,
                  current_turn_media: list | None = None,
                  live_media_text: str | None = None) -> ReplyResult:
        ensure_hermes_runtime()
        apply_vendor_patches()
        register_oqim_tools()
        install_shim_once()
        from run_agent import AIAgent

        grounding = grounding or []
        loop = asyncio.get_running_loop()
        managed_prompt = load_hermes_reply_prompt()
        talking_policy = TalkingPolicy.for_agent(**(config.talking_overrides or {}))
        if profile.execution_mode == "setup":
            # The Owner Agent is the owner's operator, not the customer seller:
            # its own identity + Hermes operator behavior, never the seller
            # hermes_reply prompt + seller AGENT.md persona (#455 decision A).
            system_prompt = compose_owner_operator_system_prompt(
                config.agent_md,
                voice_block=config.voice_block,
            )
        else:
            system_prompt = compose_hermes_system_prompt(
                config.agent_md,
                agent_kind,
                prompt=managed_prompt,
                emoji_usage=talking_policy.emoji_usage,
                seller_playbook_override=config.seller_playbook_override,
                voice_block=config.voice_block,
            )
        prompt_cache = _hermes_prompt_cache_payload(
            config=config,
            agent_kind=agent_kind,
            system_prompt=system_prompt,
            prompt=managed_prompt,
            allowed_tool_names=profile.allowed_tool_names,
        )
        talk_bundle = TalkBundle(
            workspace_id=config.workspace_id,
            agent_id=config.agent_id,
            hermes_run_id=hermes_run_id
            or f"reply:{config.workspace_id}:{config.agent_id}:{conversation_id or 'none'}",
            trigger_ref=reply_to_message_ref,
            conversation_id=conversation_id,
            actions=[],
            talking_policy_snapshot=talking_policy,
        )
        ctx = ToolContext(
            workspace_id=config.workspace_id,
            agent_id=config.agent_id,
            agent_kind=agent_kind,
            agent_session_id=agent_session_id,
            conversation_id=conversation_id,
            grounding=grounding,
            history=history or [],
            hermes_run_id=hermes_run_id,
            voice_examples=voice_examples or [],
            authority_warnings=authority_warnings or [],
            chain_name=profile.hermes_settings.chain,
            loop=loop,
            talk_bundle=talk_bundle,
            allowed_tool_names=frozenset(profile.allowed_tool_names),
            # The record profile grants only conversation.record; force the
            # named tool so mode=ANY records the commercial state. Every other
            # mode keeps force_tool_call False (talk-forcing unchanged).
            force_tool_call=(profile.execution_mode == "record"),
            max_catalog_searches=profile.retrieval_policy.max_in_loop_catalog_calls,
            catalog_enable_semantic=profile.retrieval_policy.enable_contextual_rank,
            catalog_enable_rerank=profile.retrieval_policy.enable_rerank,
            prompt_cache=prompt_cache,
            context_window=profile.hermes_settings.context_length,
            current_turn_media=current_turn_media or [],
            live_media_text=live_media_text,
        )

        # Hermes-native continuity: replay the stored session transcript as
        # run_conversation's conversation_history so Hermes owns the
        # conversation memory (and its context compressor manages length).
        # System material is supplied via the composed system prompt and is
        # never replayed; None-valued storage fields are stripped so the
        # messages match what Hermes itself emitted. Normalized (A3) so the
        # model never re-reads banned output form from its own past turns.
        prior_history = _build_replay_history(session_db, hermes_session_id)

        # Plane B: the pre-fetched grounding rides INSIDE the turn as observed
        # context, so the model can answer in one iteration. Without the
        # grounding here the model burned the whole tool-loop budget
        # re-querying old retrieval tools. The Telegram transcript is NOT
        # re-pasted — continuity comes from the resumed Hermes session above.
        turn = compose_hermes_turn(
            customer_message,
            grounding=grounding,
            voice_examples=voice_examples or [],
            conversation_state=conversation_state or {},
            current_message_ref=reply_to_message_ref,
        )
        # Boundary swap (live call only): keep the full <turn_context> wrapper but
        # show the bare media marker inside <current_message>; the stored/replayed
        # turn keeps the labeled transcript for recall. See _live_turn_override.
        ctx.live_media_text = _live_turn_override(turn, customer_message, live_media_text)

        def _run_sync() -> dict:
            agent = AIAgent(
                base_url=OQIM_SHIM_BASE_URL, api_key="oqim-shim", provider="openai",
                api_mode="chat_completions", model=profile.hermes_settings.model,
                enabled_toolsets=list(profile.hermes_settings.enabled_toolsets),
                ephemeral_system_prompt=system_prompt,
                skip_context_files=profile.hermes_settings.skip_context_files,
                skip_memory=profile.hermes_settings.skip_memory,
                save_trajectories=profile.hermes_settings.save_trajectories,
                quiet_mode=True, max_iterations=profile.hermes_settings.max_iterations,
                session_id=hermes_session_id,
                session_db=session_db,
            )
            agent._disable_streaming = True
            handle = None
            if (
                turn_session_id is not None
                and conversation_id is not None
                and config.agent_id is not None
            ):
                handle = active_turn_run_registry.register(
                    workspace_id=config.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=config.agent_id,
                    turn_session_id=turn_session_id,
                    hermes_run_id=hermes_run_id
                    or f"reply:{config.workspace_id}:{config.agent_id}:{conversation_id}",
                    agent=agent,
                    turn_revision_start=int(turn_revision_start or 1),
                )
            run_result: dict | None = None
            try:
                run_result = agent.run_conversation(
                    turn, conversation_history=prior_history
                )
                if run_result is None:
                    run_result = {}
                return run_result
            finally:
                if handle is not None:
                    pending_steer = (
                        (run_result or {}).get("pending_steer")
                        if isinstance(run_result, dict)
                        else None
                    )
                    turn_details = active_turn_run_registry.finish(
                        handle,
                        pending_steer_text=pending_steer,
                    )
                    if isinstance(run_result, dict):
                        run_result["_oqim_turn_details"] = turn_details

        loop_started = time.monotonic()
        try:
            with use_tool_context(ctx):
                result = await asyncio.to_thread(_run_sync)
            reply_text = (result or {}).get("final_response", "") or ""
            failed = bool((result or {}).get("failed"))
            if _is_engine_fallback_text(reply_text):
                # Engine fallback sentinel: a failure in sentence form.
                failed = True
                reply_text = ""
            elif failed and _looks_like_provider_error(reply_text):
                reply_text = ""
            if talk_bundle.actions:
                reply_text = talk_bundle.text_preview() or reply_text
            reply_text = normalize_outgoing_text(reply_text)
            turn_details = (
                (result or {}).get("_oqim_turn_details")
                if isinstance(result, dict)
                else None
            )
        except Exception:
            logger.exception("hermes loop failed for agent %s", config.agent_id)
            result, reply_text, failed, turn_details = {}, "", True, None
        loop_ms = round((time.monotonic() - loop_started) * 1000)
        # Telemetry: surface the loop's reasoning trace + api_calls so the live
        # reply path can persist it for benchmarking. No-op when no trace session
        # is active (e.g. unit tests), so this never changes reply behavior.
        from app.modules.agent_runtime_v2.trace import emit_trace_event

        await emit_trace_event(
            "hermes",
            "loop",
            api_calls=int((result or {}).get("api_calls") or 0),
            loop_ms=loop_ms,
            failed=failed,
            reasoning=_distill_reasoning(result),
            talk_actions=len(talk_bundle.actions),
        )

        tool_errors = len(ctx.tool_errors) + (1 if failed or not reply_text else 0)
        warnings = list(authority_warnings or [])
        for warning in ctx.authority_warnings:
            if warning not in warnings:
                warnings.append(warning)
        return ReplyResult(
            reply_text=reply_text,
            confidence=0.0,
            grounding_hits=len(grounding),
            tool_errors=tool_errors,
            authority_warnings=warnings,
            talk_bundle=talk_bundle if talk_bundle.actions else None,
            committed_action_refs=list(ctx.business_action_refs),
            tool_authority_lines=list(dict.fromkeys(ctx.tool_authority_lines)),
            intelligence_payloads=list(ctx.intelligence_payloads),
            turn_details=turn_details,
            record_payload=getattr(ctx, "record_payload", None),
        )


# The packaged Hermes engine returns these EXACT fallback strings as
# final_response when the iteration-limit summarizer fails (run_agent.py,
# iteration-limit path) — with NO failed flag. They are engine error codes in
# sentence form and must never reach a customer (live leak 2026-06-09).
_ENGINE_FALLBACK_PREFIXES = (
    "I reached the iteration limit",
    "I reached the maximum iterations",
)


def _is_engine_fallback_text(text: str) -> bool:
    return (text or "").strip().startswith(_ENGINE_FALLBACK_PREFIXES)


def _looks_like_provider_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "api call failed",
            "generate_with_tools",
            "invalid_argument",
            "chain exhausted",
            "provider:",
            "request debug dump",
        )
    )


def _hermes_prompt_cache_payload(
    *,
    config: AgentConfig,
    agent_kind: str,
    system_prompt: str,
    prompt: ManagedRuntimePrompt,
    allowed_tool_names: list[str] | frozenset[str] | set[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Cache only static agent prompt material.

    The customer turn, retrieved evidence, chat memory, and tool results stay
    dynamic. Gemini cached-content stores the stable system prompt, while this
    payload gives the cache a traceable static context without duplicating the
    customer conversation.
    """
    tool_names = sorted(str(name) for name in (allowed_tool_names or []) if name)
    agent_md_hash = _sha256_text(config.agent_md)
    system_prompt_hash = _sha256_text(system_prompt)
    stable_payload: dict[str, Any] = {
        "schema_version": "hermes_agent_static_context.v1",
        "note": "Static OQIM agent runtime metadata. This is not customer speech.",
        "managed_prompt": {
            "prompt_id": prompt.prompt_id,
            "version": prompt.version,
            "digest": prompt.digest,
            "cache_key": prompt.cache_key,
            "cache_policy": prompt.cache_policy,
        },
        "agent": {
            "workspace_id": config.workspace_id,
            "agent_id": config.agent_id,
            "agent_name": config.name,
            "agent_kind": agent_kind,
        },
        "prompt_hashes": {
            "agent_md_sha256": agent_md_hash,
            "system_prompt_sha256": system_prompt_hash,
        },
        "tool_grants": tool_names,
    }
    material_hash = _sha256_json(
        {
            "system_prompt": system_prompt,
            "stable_payload": stable_payload,
        }
    )
    estimated_tokens = _estimate_cache_tokens(
        system_prompt=system_prompt,
        stable_payload=stable_payload,
    )
    cacheable = estimated_tokens >= _GEMINI_CACHED_CONTENT_MIN_TOKENS
    cache_key = (
        "hermes-agent-prompt:v1:"
        f"{config.workspace_id}:{config.agent_id}:{agent_kind}:{material_hash[:16]}"
    )
    return {
        "schema_version": "llm_prompt_cache.v1",
        "provider_strategy": "gemini_cached_content" if cacheable else "none",
        "cacheable": cacheable,
        "skip_reason": None if cacheable else "gemini_min_cache_tokens",
        "estimated_tokens": estimated_tokens,
        "min_cache_tokens": _GEMINI_CACHED_CONTENT_MIN_TOKENS,
        "ttl_seconds": 3600,
        "runtime_context": {
            "cache_scope": "hermes_agent_prompt",
            "cache_key": cache_key,
            "material_hash": material_hash,
            "stable_payload": stable_payload,
            "dynamic_payload_keys": [
                "turn_context",
                "conversation",
                "retrieved_evidence",
                "customer_message",
                "tool_results",
            ],
            "stable_payload_keys": [
                "agent",
                "prompt_hashes",
                "tool_grants",
                "managed_prompt",
            ],
            "invalidation_refs": [
                f"workspace:{config.workspace_id}",
                f"agent:{config.agent_id}",
                f"agent_md:{agent_md_hash[:16]}",
                f"prompt:{prompt.prompt_id}:{prompt.version}:{prompt.digest[:16]}",
            ],
        },
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _sha256_json(payload: dict[str, Any]) -> str:
    material = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return _sha256_text(material)


def _estimate_cache_tokens(
    *,
    system_prompt: str,
    stable_payload: dict[str, Any],
) -> int:
    material = json.dumps(
        {
            "system_prompt": system_prompt,
            "stable_payload": stable_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return max(1, len(material) // _APPROX_CHARS_PER_TOKEN)


def _distill_reasoning(result: dict | None) -> list[dict]:
    """Compact the Hermes loop's message history into a reasoning trace: each
    assistant turn (content + tool calls it issued) and each tool result,
    previewed. Used for benchmarking/study, so it is best-effort and bounded."""
    from app.modules.agent_runtime_v2.trace import trace_preview

    out: list[dict] = []
    for message in (result or {}).get("messages", []) or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("assistant", "tool"):
            continue
        entry: dict = {"role": role}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            entry["content"] = trace_preview(content, limit=500)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            distilled: list[dict] = []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                distilled.append(
                    {
                        "name": fn.get("name"),
                        "arguments": trace_preview(fn.get("arguments"), limit=300),
                    }
                )
            if distilled:
                entry["tool_calls"] = distilled
        if "content" in entry or "tool_calls" in entry:
            out.append(entry)
    return out
