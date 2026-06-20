"""Unified LLM client — Gemini-first runtime with control-lane helpers.

Every brain module should use generate_with_fallback() instead of
calling provider SDKs directly. This gives automatic model fallback,
provider-aware routing, think-tag stripping, and rate-limit cooldown.

Usage:
    from app.brain.llm import generate_with_fallback
    from app.brain.llm_policy import FLASH_CHAIN
    response = await generate_with_fallback(chain=FLASH_CHAIN, contents=prompt, config=config)
    text = response.text  # normalized LLMResponse
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
from google import genai
from google.genai import types
from openai import AsyncOpenAI

from app.brain.llm_policy import (
    FLASH_LITE_CHAIN as _FLASH_LITE_CHAIN,
)
from app.brain.llm_policy import (
    ChainItem,
    get_task_policy,
    llm_policy_key,
    resolve_chain_for_operation,
    sanitize_llm_policy_overrides,
)
from app.core.config import get_settings
from app.core.google_auth import build_genai_client_kwargs, log_google_auth_status

logger = logging.getLogger(__name__)

_GEMINI_CACHED_CONTENT_MIN_TOKENS = 4096
_APPROX_CHARS_PER_TOKEN = 4

# ── Response normalization ──


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""

    text: str
    model_used: str
    provider: str  # "gemini" or "cerebras"
    usage: dict[str, int] | None = None
    parsed: Any | None = None


@dataclass
class LLMToolCall:
    """A single function/tool call emitted by the model."""

    id: str
    name: str
    arguments: str  # JSON string
    # Gemini 3 thinking models attach an opaque ``thought_signature`` (bytes) to
    # each function-call PART. The API REQUIRES that exact signature be echoed
    # back on the same part in the conversation history on the next turn, or it
    # rejects with 400 INVALID_ARGUMENT ("missing a thought_signature"). We carry
    # it here so the Hermes OpenAI shim can ferry it (as base64 in
    # ``extra_content``) and the translator can rehydrate it on replay.
    thought_signature: bytes | None = None


@dataclass
class LLMToolResponse:
    """Normalized response from a tool-enabled (function-calling) LLM call.

    Unlike LLMResponse, this preserves function-call parts that
    ``_normalize_gemini`` drops. Used by the Hermes tool-loop path.
    """

    text: str
    tool_calls: list[LLMToolCall]
    model_used: str
    provider: str
    usage: dict[str, int] | None = None
    thought_summaries: list[str] = field(default_factory=list)


# ── Think-tag stripping ──

_THINK_TAG_RE = re.compile(r"<think>[\s\S]*?</think>")


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks from model output."""
    return _THINK_TAG_RE.sub("", text).strip()


# ── Chain types ──
# Chain constants are imported from app.brain.llm_policy. Keep model routing in
# one file so runtime behavior and admin UI never drift apart.

# Timeout per model attempt (seconds)
DEFAULT_TIMEOUT = 45

def build_control_json_config(
    *,
    response_schema: Any,
    max_output_tokens: int,
    temperature: float = 1.0,
    system_instruction: str | None = None,
) -> types.GenerateContentConfig:
    """Config for small structured control nodes.

    Uses Gemini 3 control behavior:
    - minimal thinking for short control outputs
    - automatic function calling disabled
    - explicit JSON schema payload rather than SDK-side parsed-object reliance
    """
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
        response_json_schema=_as_json_schema(response_schema),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
    )


def build_control_text_config(
    *,
    max_output_tokens: int,
    temperature: float = 1.0,
    system_instruction: str | None = None,
) -> types.GenerateContentConfig:
    """Config for tagged/text fallbacks on control nodes."""
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
    )


def build_reply_text_config(
    *,
    max_output_tokens: int,
    temperature: float = 1.0,
    system_instruction: str | None = None,
    thinking_level: str = "low",
) -> types.GenerateContentConfig:
    """Config for customer-facing Gemini text generation.

    Gemini 3 thinks by default. For short seller replies we want lower-latency,
    lower-drift visible output, so keep thinking explicit and light.
    """
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
    )


def _tool_loop_thinking_level(operation: str | None, include_thoughts: bool) -> str | None:
    """Thinking level for the function-calling loop, owned by the task policy.

    The task policy (llm_policy.LLMTaskPolicy.thinking_level) decides; when it
    is silent, keep the historical behavior: "low" for the Hermes reply loop,
    provider default otherwise.
    """
    policy_level = get_task_policy(operation).thinking_level if operation else None
    if policy_level:
        return policy_level
    return "low" if include_thoughts else None


def _tool_loop_temperature(operation: str | None) -> float:
    """Resolve the sampling temperature for a tool-loop operation.

    The per-task policy may pin a temperature (e.g. the seller reply runs at 0.3
    to stay grounded); when it does not, fall back to Gemini's 1.0 default so no
    other tool loop changes behavior.
    """
    policy_temp = get_task_policy(operation).temperature if operation else None
    return policy_temp if policy_temp is not None else 1.0


def build_tool_config(
    tool_schemas: list[dict],
    *,
    system_instruction: str | None = None,
    include_thoughts: bool = False,
    thinking_level: str | None = None,
    temperature: float = 1.0,
    force_function_calling: bool = False,
    allowed_function_names: list[str] | None = None,
) -> types.GenerateContentConfig:
    """Config for Gemini function-calling (the Hermes tool-loop path).

    Builds a single types.Tool from OpenAI-style tool schemas and keeps
    automatic function calling DISABLED so the caller drives the tool loop
    explicitly (matching every other control-lane builder in this module).
    """
    decls = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t.get("description", ""),
            parameters=t.get("parameters"),
        )
        for t in tool_schemas
    ]
    # Gemini rejects a Tool with empty function_declarations
    # ("tools[0].tool_type: required one_of 'tool_type' must have one
    # initialized field"). The Hermes iteration-limit summary call sends no
    # tools, so omit the field entirely rather than emit an empty Tool.
    tools = [types.Tool(function_declarations=decls)] if decls else None
    thinking_config = None
    if include_thoughts or thinking_level:
        thinking_config = types.ThinkingConfig(
            include_thoughts=True if include_thoughts else None,
            thinking_level=thinking_level,
        )
    # mode=ANY: the model MUST answer with a tool call. Used for reply turns
    # whose terminal customer-visible output IS a talk tool — plain-text
    # answers there bypass bubbles/pacing/audit (live failure 2026-06-11:
    # flash-lite skipped talk.send_msgs and a wall of text reached the
    # customer). The talk-tool restatement shortcut in the Hermes shim ends
    # the loop after talk emits, so ANY cannot force an infinite tool loop.
    # allowed_function_names pins mode=ANY to a SINGLE named tool — the forced
    # commercial-finalization pass grants only conversation.set_state, so ANY
    # cannot wander to another granted tool. Default None leaves the field unset
    # (the existing talk-forcing path is unchanged).
    tool_config = None
    if force_function_calling and tools:
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode="ANY",
                allowed_function_names=allowed_function_names or None,
            )
        )
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=tools,
        tool_config=tool_config,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=temperature,
        thinking_config=thinking_config,
    )


@dataclass(frozen=True)
class LLMPolicy:
    """Operation-level model and config policy.

    This keeps callers from hand-rolling Gemini config details and makes model
    choice testable at the business-operation boundary.
    """

    operation: str
    chain: list[ChainItem]
    temperature: float = 1.0
    max_output_tokens: int = 4096
    timeout: float = DEFAULT_TIMEOUT
    thinking_level: str | None = None
    disable_automatic_function_calling: bool = True


_DEFAULT_STRUCTURED_JSON_POLICY = LLMPolicy(
    operation="structured_json",
    chain=_FLASH_LITE_CHAIN,
    temperature=0.2,
    thinking_level="minimal",
)

_LLM_POLICIES: dict[str, LLMPolicy] = {
    # Onboarding/control extraction should be cheap, bounded, and stable.
    "contact_classification": LLMPolicy(
        operation="contact_classification",
        chain=_FLASH_LITE_CHAIN,
        temperature=0.1,
        max_output_tokens=1024,
        thinking_level="minimal",
    ),
    "batch_contact_classification": LLMPolicy(
        operation="batch_contact_classification",
        chain=_FLASH_LITE_CHAIN,
        temperature=0.1,
        max_output_tokens=4096,
        thinking_level="minimal",
    ),
}


def get_llm_policy(
    operation: str,
    *,
    fallback_chain: list[ChainItem] | None = None,
) -> LLMPolicy:
    """Return the effective policy for an operation."""
    policy = _LLM_POLICIES.get(operation)
    if policy is not None:
        return policy
    if fallback_chain is None:
        return _DEFAULT_STRUCTURED_JSON_POLICY
    return LLMPolicy(
        operation=operation or _DEFAULT_STRUCTURED_JSON_POLICY.operation,
        chain=fallback_chain,
        temperature=_DEFAULT_STRUCTURED_JSON_POLICY.temperature,
        max_output_tokens=_DEFAULT_STRUCTURED_JSON_POLICY.max_output_tokens,
        timeout=_DEFAULT_STRUCTURED_JSON_POLICY.timeout,
        thinking_level=_DEFAULT_STRUCTURED_JSON_POLICY.thinking_level,
        disable_automatic_function_calling=(
            _DEFAULT_STRUCTURED_JSON_POLICY.disable_automatic_function_calling
        ),
    )


def build_structured_json_config(
    *,
    policy: LLMPolicy,
    system_instruction: str,
    response_schema: Any | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
) -> types.GenerateContentConfig:
    """Build Gemini JSON config from one policy boundary."""
    config_kwargs: dict[str, Any] = {
        "system_instruction": system_instruction,
        "temperature": policy.temperature if temperature is None else temperature,
        "max_output_tokens": (
            policy.max_output_tokens if max_output_tokens is None else max_output_tokens
        ),
        "response_mime_type": "application/json",
    }
    if response_schema is not None:
        config_kwargs["response_json_schema"] = _as_json_schema(response_schema)
    if policy.disable_automatic_function_calling:
        config_kwargs["automatic_function_calling"] = (
            types.AutomaticFunctionCallingConfig(disable=True)
        )
    if policy.thinking_level:
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=policy.thinking_level,
        )
    return types.GenerateContentConfig(**config_kwargs)


def _as_json_schema(schema: Any) -> Any:
    """Convert Pydantic models into JSON Schema for GenAI SDK structured output.

    The SDK docs show `response_json_schema` + app-side validation as the
    canonical pattern. Keep this helper tolerant so callers can still pass a raw
    JSON Schema dict when needed.
    """
    if hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    return schema


def _trace_preview(value: Any, *, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str, indent=2).strip()
        except Exception:
            text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _last_user_message_preview(contents: str | list, limit: int = 500) -> str | None:
    """Preview of the LAST user message — the live customer turn.

    ``contents`` for tool-loop calls is an OpenAI-shaped message array whose
    head is session history; only the final user message is "now".
    """
    if not isinstance(contents, list):
        return None
    for message in reversed(contents):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return _trace_preview(content, limit=limit)
    return None


def _build_llm_request_summary(
    *,
    contents: str | list,
    config: types.GenerateContentConfig | None,
    prompt_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt_text = contents if isinstance(contents, str) else _trace_preview(contents, limit=1000)
    system_instruction = getattr(config, "system_instruction", None) if config is not None else None
    system_instruction_text = _trace_preview(system_instruction, limit=100_000)
    thinking_config = getattr(config, "thinking_config", None) if config is not None else None
    afc = getattr(config, "automatic_function_calling", None) if config is not None else None

    summary = {
        "prompt_chars": len(prompt_text or ""),
        "stable_system_instruction_chars": len(system_instruction_text or ""),
        "total_uncached_chars": len(prompt_text or "") + len(system_instruction_text or ""),
        "prompt_preview": _trace_preview(prompt_text, limit=1000),
        # The head of a long message array is HISTORY, not the live turn —
        # reading it as "what the customer just said" caused a wrong live
        # model decision (run 32, 2026-06-09). Surface the actual turn.
        "last_user_message_preview": _last_user_message_preview(contents),
        "system_instruction_preview": _trace_preview(system_instruction, limit=700),
        "config": {
            "temperature": getattr(config, "temperature", None) if config is not None else None,
            "max_output_tokens": getattr(config, "max_output_tokens", None) if config is not None else None,
            "response_mime_type": getattr(config, "response_mime_type", None) if config is not None else None,
            "thinking_level": getattr(thinking_config, "thinking_level", None) if thinking_config is not None else None,
            "automatic_function_calling_disabled": bool(getattr(afc, "disable", False)) if afc is not None else False,
        },
    }
    if prompt_cache:
        runtime_context = (
            prompt_cache.get("runtime_context")
            if isinstance(prompt_cache.get("runtime_context"), dict)
            else {}
        )
        summary["prompt_cache"] = {
            "provider_strategy": prompt_cache.get("provider_strategy"),
            "cacheable": prompt_cache.get("cacheable"),
            "cache_key": runtime_context.get("cache_key"),
            "material_hash": runtime_context.get("material_hash"),
        }
    return summary


def _has_thinking_config(config: types.GenerateContentConfig | None) -> bool:
    return getattr(config, "thinking_config", None) is not None if config is not None else False


def _without_thinking_config(
    config: types.GenerateContentConfig | None,
) -> types.GenerateContentConfig | None:
    if config is None or not _has_thinking_config(config):
        return config
    if hasattr(config, "model_copy"):
        return config.model_copy(update={"thinking_config": None})
    data = config.model_dump(exclude_none=True) if hasattr(config, "model_dump") else {}
    data.pop("thinking_config", None)
    return types.GenerateContentConfig(**data)


def _with_cached_content_config(
    config: types.GenerateContentConfig | None,
    cache_name: str,
) -> types.GenerateContentConfig:
    data = config.model_dump(exclude_none=True) if hasattr(config, "model_dump") else {}
    # Gemini forbids mixing cached_content with a repeated system_instruction.
    # Tool-enabled cached content also owns tool declarations/config. Repeating
    # any of these on the generate request yields a provider 400.
    data.pop("system_instruction", None)
    data.pop("tools", None)
    data.pop("tool_config", None)
    data.pop("automatic_function_calling", None)
    data["cached_content"] = cache_name
    return types.GenerateContentConfig(**data)


def _prompt_cache_wants_gemini(prompt_cache: dict[str, Any] | None) -> bool:
    if not isinstance(prompt_cache, dict):
        return False
    if not prompt_cache.get("cacheable", False):
        return False
    strategy = str(prompt_cache.get("provider_strategy") or "").strip()
    return strategy == "gemini_cached_content"


def _prompt_cache_ttl_seconds(prompt_cache: dict[str, Any] | None) -> int:
    if not isinstance(prompt_cache, dict):
        return 3600
    raw = prompt_cache.get("ttl_seconds")
    runtime_context = prompt_cache.get("runtime_context")
    if raw is None and isinstance(runtime_context, dict):
        raw = runtime_context.get("ttl_seconds")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 3600
    return max(60, min(value, 86_400))


def _prompt_cache_min_tokens(prompt_cache: dict[str, Any] | None) -> int:
    if not isinstance(prompt_cache, dict):
        return _GEMINI_CACHED_CONTENT_MIN_TOKENS
    raw = prompt_cache.get("min_cache_tokens")
    runtime_context = prompt_cache.get("runtime_context")
    if raw is None and isinstance(runtime_context, dict):
        raw = runtime_context.get("min_cache_tokens")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _GEMINI_CACHED_CONTENT_MIN_TOKENS
    return max(1, value)


def _gemini_cache_min_tokens_for_model(model: str) -> int:
    normalized = (model or "").lower().removeprefix("models/")
    if "3.1-flash-lite" in normalized:
        return 1024
    if "3.5-flash" in normalized:
        return 4096
    if "3.1-pro" in normalized:
        return 4096
    if "3-pro" in normalized:
        return 2048
    if "2.5-flash" in normalized or "2.5-pro" in normalized:
        return 2048
    return _GEMINI_CACHED_CONTENT_MIN_TOKENS


def _prompt_cache_estimated_tokens(
    *,
    prompt_cache: dict[str, Any] | None,
    stable_payload: dict[str, Any],
    system_instruction: str | None,
) -> int:
    if isinstance(prompt_cache, dict):
        raw = prompt_cache.get("estimated_tokens")
        runtime_context = prompt_cache.get("runtime_context")
        if raw is None and isinstance(runtime_context, dict):
            raw = runtime_context.get("estimated_tokens")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value

    material = json.dumps(
        {
            "stable_payload": stable_payload,
            "system_instruction": system_instruction,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return max(1, len(material) // _APPROX_CHARS_PER_TOKEN)


def _cache_system_instruction(config: types.GenerateContentConfig | None) -> str | None:
    instruction = getattr(config, "system_instruction", None) if config is not None else None
    if instruction is None:
        return None
    if isinstance(instruction, str):
        return instruction
    text = getattr(instruction, "text", None)
    if isinstance(text, str):
        return text
    return _trace_preview(instruction, limit=100_000) or None


def _tools_digest(tools: Any) -> str:
    """Digest of the concrete tool schemas baked into Gemini cached content.

    The upstream cache key hashes the system prompt + tool NAMES only — a
    tool-schema edit (new param, new description) would otherwise reuse a
    cached content carrying the OLD schemas for up to the TTL.
    """
    if not tools:
        return "notools"
    try:
        material = json.dumps(
            [
                tool.model_dump() if hasattr(tool, "model_dump") else repr(tool)
                for tool in tools
            ],
            sort_keys=True,
            default=str,
        )
    except Exception:
        material = repr(tools)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _cache_display_name(cache_key: str, material_hash: str) -> str:
    raw = cache_key or material_hash or "oqim-agent-runtime-context"
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", raw).strip("-")
    if not safe:
        safe = "oqim-agent-runtime-context"
    return safe[:128]


def _is_unsupported_thinking_config_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "thinking_level" in text
        and ("not supported" in text or "unsupported" in text)
    ) or (
        "thinkingconfig" in text
        and ("not supported" in text or "unsupported" in text)
    )


# ── Gemini thought-signature round-trip (Gemini 3 function calling) ──
# The signature is opaque bytes on the function-call Part. It cannot ride inside
# the OpenAI ``arguments`` JSON (Gemini wants it back verbatim on the part), so
# the Hermes loop's documented passthrough field ``extra_content`` carries it as
# base64 under ``{"google": {"thought_signature": "<b64>"}}`` (the same shape the
# Hermes chat_completions transport uses). These two helpers are the single
# encode/decode boundary.


def _thought_signature_to_extra_content(signature: bytes | None) -> dict | None:
    """Wrap a thought-signature (bytes) into the OpenAI ``extra_content`` shape."""
    if not signature:
        return None
    return {
        "google": {"thought_signature": base64.b64encode(signature).decode("ascii")}
    }


def _extra_content_to_thought_signature(extra_content: Any) -> bytes | None:
    """Pull the thought-signature bytes back out of an ``extra_content`` dict.

    Tolerant of shape drift: accepts the canonical
    ``{"google": {"thought_signature": "<b64>"}}`` and a flat
    ``{"thought_signature": "<b64>"}``. Returns None on anything unexpected
    rather than crashing the tool loop.
    """
    if not isinstance(extra_content, dict):
        return None
    google = extra_content.get("google")
    raw = None
    if isinstance(google, dict):
        raw = google.get("thought_signature")
    if raw is None:
        raw = extra_content.get("thought_signature")
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        try:
            return base64.b64decode(raw)
        except (ValueError, TypeError):
            return None
    return None


def _gemini_function_call_part_from_openai_tool_call(tc: dict) -> types.Part:
    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
    name = fn.get("name") or "tool"
    raw_args = fn.get("arguments")
    try:
        args = json.loads(raw_args) if raw_args else {}
    except (json.JSONDecodeError, ValueError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}

    part = types.Part.from_function_call(name=name, args=args)
    signature = _extra_content_to_thought_signature(
        tc.get("extra_content") if isinstance(tc, dict) else None
    )
    if signature is not None:
        part.thought_signature = signature
    return part


def _gemini_function_response_part_from_openai_tool_message(msg: dict) -> types.Part:
    name = msg.get("name") or msg.get("tool_name") or "tool"
    content = msg.get("content")
    try:
        response = json.loads(content)
        if not isinstance(response, dict):
            response = {"result": response}
    except (json.JSONDecodeError, ValueError, TypeError):
        response = {"result": str(content)}
    return types.Part.from_function_response(name=name, response=response)


def _attach_current_turn_media(
    contents: list[types.Content],
    current_turn_media: list | None,
    live_user_text: str | None = None,
) -> None:
    """Attach the CURRENT turn's media Parts to the live (last) user Content.

    Injected below Hermes (vision-only, never sees it), for THIS turn only —
    never into replayed history -> structural pay-once.

    When ``live_user_text`` is given (the boundary swap), the live user turn's
    TEXT is replaced with it (a bare ``[Voice message]`` marker) before the
    media Parts are appended — so the model perceives the audio/image and never
    sees the transcript next to it (the text-dominance confound). The stored
    Hermes session keeps the labeled transcript; only this per-call translation
    is swapped. See the media-perception spec (2026-06-13).
    """
    if not current_turn_media:
        return
    from app.brain.media_parts import to_gemini_part

    media_parts = [to_gemini_part(p) for p in current_turn_media]
    logger.info(
        "media_perception_attached parts=%d kinds=%s",
        len(media_parts),
        ",".join(sorted({str(getattr(p, "kind", "?")) for p in current_turn_media})),
    )
    last_user = next((c for c in reversed(contents) if c.role == "user"), None)
    if last_user is not None:
        if live_user_text is not None:
            base_parts: list[types.Part] = [types.Part.from_text(text=live_user_text)]
        else:
            base_parts = list(last_user.parts)
        last_user.parts = base_parts + media_parts
    else:
        contents.append(types.Content(role="user", parts=media_parts))


def _native_media_trace_summary(current_turn_media: list | None) -> list[dict]:
    """Compact per-Part record for the hermes_run trace: proves whether (and
    what) native media actually reached the Gemini call. The request_summary
    only sees PRE-injection contents, so without this we are blind to the very
    hop that failed across 4 deploys. See the media-perception spec §6.2."""
    out: list[dict] = []
    for p in current_turn_media or []:
        out.append({
            "ref": getattr(p, "message_ref", None),
            "kind": getattr(p, "kind", None),
            "mime": getattr(p, "mime_type", None),
            "bytes": len(getattr(p, "data", b"") or b""),
            "source": getattr(p, "source", None),
        })
    return out


def _openai_messages_to_gemini_contents(
    messages: list[dict],
    current_turn_media: list | None = None,
    live_media_text: str | None = None,
) -> list[types.Content]:
    """Translate OpenAI-format chat messages into Gemini ``types.Content``.

    google-genai rejects raw OpenAI dicts as ``contents`` — it wants a string
    (single turn) or ``list[types.Content]``. The Hermes OpenAI shim drives the
    tool loop in OpenAI format (its job); this is the Gemini boundary that
    converts those messages before the per-model call. Role mapping:

    - ``user`` -> ``Content(role="user", [Part.from_text])``
    - ``assistant`` with ``tool_calls`` -> ``Content(role="model", [function_call parts])``
      (plus a leading text part if the assistant also produced visible text)
    - ``assistant`` without ``tool_calls`` -> ``Content(role="model", [Part.from_text])``
    - adjacent ``tool`` messages -> one ``Content(role="tool", [function_response parts])``
    - ``system`` -> skipped (the shim already lifts it to system_instruction)
    - unknown roles / zero-part turns -> skipped defensively (never crash)

    ``live_media_text``, when given, is forwarded to
    ``_attach_current_turn_media`` as ``live_user_text`` — the bare per-call
    rendering of this turn's media (the boundary swap); the stored session keeps
    the labeled transcript.
    """
    contents: list[types.Content] = []
    i = 0
    messages = messages or []
    while i < len(messages):
        msg = messages[i]
        i += 1
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        if role == "user":
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=str(content or ""))],
                )
            )

        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                parts: list[types.Part] = []
                # Preserve any visible text the model emitted alongside calls.
                if isinstance(content, str) and content.strip():
                    parts.append(types.Part.from_text(text=content))
                for tc in tool_calls:
                    parts.append(_gemini_function_call_part_from_openai_tool_call(tc))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            else:
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=str(content or ""))],
                    )
                )

        elif role == "tool":
            parts: list[types.Part] = []
            tool_msg: dict | None = msg
            while isinstance(tool_msg, dict) and tool_msg.get("role") == "tool":
                parts.append(_gemini_function_response_part_from_openai_tool_message(tool_msg))

                if i >= len(messages):
                    tool_msg = None
                    break
                next_msg = messages[i]
                if not isinstance(next_msg, dict) or next_msg.get("role") != "tool":
                    tool_msg = None
                    break
                tool_msg = next_msg
                i += 1

            if parts:
                contents.append(types.Content(role="tool", parts=parts))

        # system -> skip (already lifted to system_instruction by the shim).
        # unknown roles -> skip defensively; don't crash the tool loop.

    # Side-channel: attach the CURRENT turn's media (below Hermes) to the live
    # user turn only -> structural pay-once. See _attach_current_turn_media.
    _attach_current_turn_media(contents, current_turn_media, live_user_text=live_media_text)

    return contents


def _normalize_tool_response(
    response: Any, model_used: str, provider: str
) -> "LLMToolResponse":
    """Normalize a Gemini function-calling response to LLMToolResponse.

    Preserves function-call parts (name + JSON-encoded args) that
    ``_normalize_gemini`` drops, and keeps any plain text the model emitted
    alongside the calls.

    Walks ``candidates[0].content.parts`` directly (NOT the
    ``response.function_calls`` convenience list) because the Gemini 3
    ``thought_signature`` lives on the PART, not on the FunctionCall — and
    ``function_calls`` drops it. We still fall back to ``function_calls`` if the
    parts aren't accessible (older response shapes / lightweight test mocks).
    """
    tool_calls: list[LLMToolCall] = []
    visible_text_parts: list[str] = []
    thought_summaries: list[str] = []
    parts = None
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) if content is not None else None

    if parts:
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                if getattr(part, "thought", False):
                    thought_summaries.append(text.strip())
                else:
                    visible_text_parts.append(text)
            fc = getattr(part, "function_call", None)
            if fc is None:
                continue
            args = getattr(fc, "args", None) or {}
            tool_calls.append(
                LLMToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=getattr(fc, "name", ""),
                    arguments=json.dumps(dict(args)),
                    thought_signature=getattr(part, "thought_signature", None),
                )
            )
    else:
        # Fallback: parts unavailable — signature can't be recovered here.
        for fc in getattr(response, "function_calls", None) or []:
            args = getattr(fc, "args", None) or {}
            tool_calls.append(
                LLMToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=getattr(fc, "name", ""),
                    arguments=json.dumps(dict(args)),
                )
            )
    text = "".join(visible_text_parts).strip()
    if not text:
        text = getattr(response, "text", "") or ""
    usage = None
    um = getattr(response, "usage_metadata", None)
    if um is not None:
        usage = {
            "input_tokens": getattr(um, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(um, "candidates_token_count", 0) or 0,
        }
        cached_tokens = getattr(um, "cached_content_token_count", 0) or 0
        if cached_tokens:
            usage["cached_content_tokens"] = cached_tokens
        thought_tokens = getattr(um, "thoughts_token_count", 0) or 0
        if thought_tokens:
            usage["thought_tokens"] = thought_tokens
    return LLMToolResponse(
        text=text,
        tool_calls=tool_calls,
        model_used=model_used,
        provider=provider,
        usage=usage,
        thought_summaries=thought_summaries,
    )


_POLICY_CACHE_TTL_SECONDS = 5.0
_policy_redis: aioredis.Redis | None = None
_policy_override_cache: dict[int, tuple[float, dict[str, str]]] = {}


async def _load_workspace_llm_overrides(workspace_id: int | None) -> dict[str, str]:
    """Load workspace model overrides.

    This is intentionally fail-open: Redis problems must not block Seller Agent replies. The
    cache keeps normal LLM calls from paying a Redis roundtrip every time.
    """
    if workspace_id is None:
        return {}
    now = time.monotonic()
    cached = _policy_override_cache.get(workspace_id)
    if cached and now - cached[0] <= _POLICY_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        global _policy_redis
        if _policy_redis is None:
            _policy_redis = aioredis.from_url(
                get_settings().redis_url,
                decode_responses=True,
            )
        raw = await asyncio.wait_for(
            _policy_redis.get(llm_policy_key(workspace_id)),
            timeout=0.2,
        )
        parsed = json.loads(raw) if raw else {}
        overrides = sanitize_llm_policy_overrides(parsed)
    except Exception:
        logger.debug(
            "LLM policy override lookup failed; using defaults",
            exc_info=True,
        )
        overrides = {}
    _policy_override_cache[workspace_id] = (now, overrides)
    return overrides


# ── LLMClient ──


class LLMClient:
    """Unified LLM client wrapping Gemini and Cerebras providers.

    Holds both provider clients internally. Routes calls based on chain
    configuration. Normalizes responses into LLMResponse. Handles
    rate-limit cooldown for Cerebras.
    """

    def __init__(self) -> None:
        settings = get_settings()

        # Gemini clients (always available)
        self._gemini_client = self._create_gemini_client(settings)

        # Cerebras client (optional — graceful degradation per D-07)
        if settings.cerebras_api_key:
            self._cerebras_client: AsyncOpenAI | None = AsyncOpenAI(
                base_url=settings.cerebras_base_url,
                api_key=settings.cerebras_api_key,
            )
            logger.info(
                "LLM providers: Gemini (configured), Cerebras (configured)"
            )
        else:
            self._cerebras_client = None
            logger.info(
                "LLM providers: Gemini (configured), "
                "Cerebras (not configured -- CEREBRAS_API_KEY missing)"
            )

        # Rate-limit cooldown for Cerebras (per D-06)
        self._cerebras_cooldown_until: float = 0.0
        self._gemini_cache_names: dict[str, tuple[float, str]] = {}

    def _create_gemini_client(
        self,
        settings: Any,
    ) -> genai.Client:
        client_kwargs, auth_status = build_genai_client_kwargs(settings)
        log_google_auth_status(logger, component="brain.llm", status=auth_status)
        return genai.Client(**client_kwargs)

    # ── Provider availability ──

    def _is_cerebras_available(self) -> bool:
        """Check if Cerebras is available (configured and not in cooldown)."""
        if self._cerebras_client is None:
            return False
        now = time.monotonic()
        was_in_cooldown = self._cerebras_cooldown_until > 0
        is_available = now > self._cerebras_cooldown_until
        if was_in_cooldown and is_available and self._cerebras_cooldown_until > 0:
            # Transitioning from cooldown to available (D-07)
            logger.warning("Cerebras cooldown expired, provider available again")
            self._cerebras_cooldown_until = 0.0  # Reset so we don't log again
        return is_available

    def _cooldown_cerebras(self, seconds: float = 60.0) -> None:
        """Put Cerebras in cooldown for the given duration."""
        self._cerebras_cooldown_until = time.monotonic() + seconds
        logger.warning(
            "Cerebras rate-limited, cooling down for %.0fs", seconds
        )

    # ── Response normalization ──

    def _normalize_gemini(
        self, response: Any, model: str
    ) -> LLMResponse:
        """Normalize Gemini GenerateContentResponse to LLMResponse."""
        usage = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = {
                "input_tokens": getattr(
                    response.usage_metadata, "prompt_token_count", 0
                ),
                "output_tokens": getattr(
                    response.usage_metadata, "candidates_token_count", 0
                ),
            }
            cached_tokens = getattr(
                response.usage_metadata, "cached_content_token_count", 0
            )
            if cached_tokens:
                usage["cached_content_tokens"] = cached_tokens
        return LLMResponse(
            text=response.text or "",
            model_used=model,
            provider="gemini",
            usage=usage,
            parsed=getattr(response, "parsed", None),
        )

    def _normalize_cerebras(
        self, response: Any, model: str
    ) -> LLMResponse:
        """Normalize Cerebras/OpenAI ChatCompletion to LLMResponse."""
        raw_text = response.choices[0].message.content or ""
        text = strip_thinking(raw_text)  # D-08: strip think tags
        usage = None
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }
        return LLMResponse(
            text=text,
            model_used=model,
            provider="cerebras",
            usage=usage,
            parsed=None,
        )

    # ── Parameter mapping ──

    def _build_cerebras_kwargs(  # noqa: C901 (pre-existing complexity; out of scope for this change)
        self,
        model: str,
        contents: str | list,
        config: types.GenerateContentConfig | None,
        timeout: float,
    ) -> dict:
        """Map Gemini GenerateContentConfig to OpenAI chat.completions kwargs."""
        settings = get_settings()

        messages: list[dict[str, str]] = []

        # Extract system instruction from config
        if config and hasattr(config, "system_instruction") and config.system_instruction:
            sys_text = config.system_instruction
            if isinstance(sys_text, str):
                messages.append({"role": "system", "content": sys_text})
            elif hasattr(sys_text, "text"):
                messages.append({"role": "system", "content": sys_text.text})

        # User content
        if isinstance(contents, str):
            messages.append({"role": "user", "content": contents})
        elif isinstance(contents, list):
            # Handle both flat text/Part lists and Gemini Content objects
            for item in contents:
                if isinstance(item, str):
                    messages.append({"role": "user", "content": item})
                elif hasattr(item, "parts"):
                    # types.Content — extract role + text from parts
                    role = getattr(item, "role", "user")
                    openai_role = "assistant" if role == "model" else role
                    text_parts = []
                    for p in item.parts:
                        if isinstance(p, str):
                            text_parts.append(p)
                        elif hasattr(p, "text"):
                            text_parts.append(p.text)
                    if text_parts:
                        messages.append({"role": openai_role, "content": "\n".join(text_parts)})
                elif hasattr(item, "text"):
                    # types.Part directly
                    messages.append({"role": "user", "content": item.text})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "timeout": timeout or settings.cerebras_timeout,
        }

        if config:
            # Map supported parameters only (Pitfall 2: strip unsupported)
            if hasattr(config, "temperature") and config.temperature is not None:
                kwargs["temperature"] = config.temperature
            if hasattr(config, "max_output_tokens") and config.max_output_tokens is not None:
                kwargs["max_tokens"] = config.max_output_tokens
            if hasattr(config, "top_p") and config.top_p is not None:
                kwargs["top_p"] = config.top_p
            if hasattr(config, "stop_sequences") and config.stop_sequences:
                kwargs["stop"] = config.stop_sequences
            if hasattr(config, "seed") and config.seed is not None:
                kwargs["seed"] = config.seed

            # Map response_mime_type to response_format
            if (
                hasattr(config, "response_mime_type")
                and config.response_mime_type == "application/json"
            ):
                kwargs["response_format"] = {"type": "json_object"}

        return kwargs

    # ── Single-model call ──

    async def generate(
        self,
        *,
        model: str,
        contents: str | list,
        config: types.GenerateContentConfig | None = None,
        provider: str,
        timeout: float = DEFAULT_TIMEOUT,
        prompt_cache: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Call a single model on a specific provider."""
        if provider == "gemini":
            effective_config = await self._gemini_effective_config(
                model=model,
                config=config,
                prompt_cache=prompt_cache,
            )
            try:
                response = await asyncio.wait_for(
                    self._gemini_client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=effective_config,
                    ),
                    timeout=timeout,
                )
            except Exception as exc:
                if not (_has_thinking_config(effective_config) and _is_unsupported_thinking_config_error(exc)):
                    raise
                logger.warning(
                    "Model %s rejected thinking_config; retrying once without it",
                    model,
                )
                response = await asyncio.wait_for(
                    self._gemini_client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=_without_thinking_config(effective_config),
                    ),
                    timeout=timeout,
                )
            return self._normalize_gemini(response, model)

        elif provider == "cerebras":
            if self._cerebras_client is None:
                raise RuntimeError("Cerebras client not configured")
            kwargs = self._build_cerebras_kwargs(model, contents, config, timeout)
            response = await self._cerebras_client.chat.completions.create(**kwargs)
            return self._normalize_cerebras(response, model)

        else:
            raise ValueError(f"Unknown provider: {provider}")

    async def _gemini_effective_config(
        self,
        *,
        model: str,
        config: types.GenerateContentConfig | None,
        prompt_cache: dict[str, Any] | None,
    ) -> types.GenerateContentConfig | None:
        cache_name = await self._gemini_cache_name(
            model=model,
            config=config,
            prompt_cache=prompt_cache,
        )
        if not cache_name:
            return config
        return _with_cached_content_config(config, cache_name)

    async def _gemini_cache_name(
        self,
        *,
        model: str,
        config: types.GenerateContentConfig | None,
        prompt_cache: dict[str, Any] | None,
    ) -> str | None:
        if not _prompt_cache_wants_gemini(prompt_cache):
            return None
        runtime_context = prompt_cache.get("runtime_context") if prompt_cache else None
        if not isinstance(runtime_context, dict):
            return None
        stable_payload = runtime_context.get("stable_payload")
        if not isinstance(stable_payload, dict) or not stable_payload:
            return None
        material_hash = str(runtime_context.get("material_hash") or "").strip()
        cache_key = str(runtime_context.get("cache_key") or "").strip()
        if not material_hash and not cache_key:
            return None
        system_instruction = _cache_system_instruction(config)
        estimated_tokens = _prompt_cache_estimated_tokens(
            prompt_cache=prompt_cache,
            stable_payload=stable_payload,
            system_instruction=system_instruction,
        )
        min_cache_tokens = max(
            _prompt_cache_min_tokens(prompt_cache),
            _gemini_cache_min_tokens_for_model(model),
        )
        if estimated_tokens < min_cache_tokens:
            logger.info(
                "Skipping Gemini cached content creation below provider minimum "
                "(estimated_tokens=%s, min_cache_tokens=%s, cache_key=%s)",
                estimated_tokens,
                min_cache_tokens,
                cache_key or material_hash[:16],
            )
            return None
        # tool_config rides inside the cache (it cannot be set per-request with
        # cached content), so a mode change must produce a different cache.
        fc_mode = "auto"
        tool_config = getattr(config, "tool_config", None)
        if tool_config is not None and getattr(tool_config, "function_calling_config", None):
            fc_mode = str(tool_config.function_calling_config.mode or "auto").lower()
        lookup_key = (
            f"{model}:{cache_key}:{material_hash}:"
            f"{_tools_digest(getattr(config, 'tools', None))}:{fc_mode}"
        )
        now = time.monotonic()
        cached = self._gemini_cache_names.get(lookup_key)
        if cached and cached[0] > now:
            return cached[1]
        ttl_seconds = _prompt_cache_ttl_seconds(prompt_cache)
        try:
            cache_config_kwargs: dict[str, Any] = {
                "contents": [
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text=json.dumps(
                                    stable_payload,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                )
                            )
                        ],
                    )
                ],
                "system_instruction": system_instruction,
                "display_name": _cache_display_name(cache_key, material_hash),
                "ttl": f"{ttl_seconds}s",
            }
            tools = getattr(config, "tools", None)
            if tools:
                cache_config_kwargs["tools"] = tools
            tool_config = getattr(config, "tool_config", None)
            if tool_config:
                cache_config_kwargs["tool_config"] = tool_config
            created = await asyncio.to_thread(
                self._gemini_client.caches.create,
                model=model,
                config=types.CreateCachedContentConfig(**cache_config_kwargs),
            )
        except Exception:
            logger.warning(
                "Gemini cached content creation failed; falling back to uncached call",
                exc_info=True,
            )
            return None
        cache_name = str(getattr(created, "name", "") or "").strip()
        if not cache_name:
            return None
        self._gemini_cache_names[lookup_key] = (
            now + max(60, ttl_seconds - 30),
            cache_name,
        )
        return cache_name

    # ── Fallback chain ──

    async def generate_with_fallback(
        self,
        *,
        chain: list[ChainItem],
        contents: str | list,
        config: types.GenerateContentConfig | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        workspace_id: int | None = None,
        operation: str | None = None,
        prompt_cache: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Call generate with automatic model fallback across providers.

        Iterates chain items. For each (provider, model):
        - Skips Cerebras entries if not available (D-07)
        - On 429 from Cerebras, enters cooldown and continues (D-06)
        - On timeout or error, logs and tries next
        - Raises last error if all models fail

        Optional workspace_id + operation: if both provided, records token
        usage via the module-level TokenTracker (if initialized).
        """
        last_error: Exception | None = None
        from app.brain.token_tracker import get_token_context

        ctx_workspace_id, ctx_operation = get_token_context()
        policy_workspace_id = workspace_id or ctx_workspace_id
        policy_operation = operation or ctx_operation
        chain = resolve_chain_for_operation(
            operation=policy_operation,
            requested_chain=chain,
            overrides=await _load_workspace_llm_overrides(policy_workspace_id),
        )

        # ── Budget enforcement (P1.9) ──
        # check_and_reserve raises BudgetExceededError if cap would be exceeded.
        # Caller decides whether to fail or degrade gracefully.
        if workspace_id is not None:
            from app.db.session import async_session
            from app.modules.agent_runtime_v2.budget import BudgetService
            tokens_estimate = len(str(contents)) // 4
            async with async_session() as _budget_session:
                budget_svc = BudgetService(_budget_session)
                await budget_svc.check_and_reserve(
                    workspace_id=workspace_id,
                    tokens_estimate=tokens_estimate,
                )
                await _budget_session.commit()

        for i, (provider, model) in enumerate(chain):
            # Skip Cerebras if not available (D-07)
            if provider == "cerebras" and not self._is_cerebras_available():
                logger.debug(
                    "Skipping %s/%s — Cerebras unavailable", provider, model
                )
                continue

            try:
                from app.modules.agent_runtime_v2.trace import emit_trace_event

                await emit_trace_event(
                    "llm",
                    "attempt",
                    provider=provider,
                    model=model,
                    operation=operation or "unknown",
                    attempt=i + 1,
                    fallback=i > 0,
                    request_summary=_build_llm_request_summary(
                        contents=contents,
                        config=config,
                        prompt_cache=prompt_cache,
                    ),
                )
                call_start = time.monotonic()
                result = await self.generate(
                    model=model,
                    contents=contents,
                    config=config,
                    provider=provider,
                    timeout=timeout,
                    prompt_cache=prompt_cache if provider == "gemini" else None,
                )
                latency_ms = round((time.monotonic() - call_start) * 1000)
                is_fallback = i > 0
                logger.info(
                    "llm_call",
                    extra={
                        "provider": provider,
                        "model": model,
                        "latency_ms": latency_ms,
                        "fallback": is_fallback,
                    },
                )
                if is_fallback:
                    logger.info(
                        "Fallback succeeded: %s/%s (after %d skips/failures)",
                        provider,
                        model,
                        i,
                    )
                await emit_trace_event(
                    "llm",
                    "success",
                    provider=provider,
                    model=model,
                    operation=operation or "unknown",
                    latency_ms=latency_ms,
                    fallback=is_fallback,
                    usage=result.usage or {},
                )

                # Record token usage — use explicit params or context vars
                from app.brain.token_tracker import get_token_context, get_token_tracker
                tracker = get_token_tracker()
                if tracker is not None:
                    ctx_ws, ctx_op = get_token_context()
                    ws = workspace_id or ctx_ws or 1  # fallback to workspace 1
                    op = operation or ctx_op or "unknown"
                    usage = result.usage or {}
                    in_tok = usage.get("input_tokens", 0)
                    out_tok = usage.get("output_tokens", 0)
                    if in_tok or out_tok:
                        await tracker.record(
                            workspace_id=ws,
                            operation=op,
                            provider=result.provider or "unknown",
                            input_tokens=in_tok,
                            output_tokens=out_tok,
                        )

                # ── Budget: record actual usage (P1.9) ──
                if workspace_id is not None:
                    _actual_usage = result.usage or {}
                    _in_tok = _actual_usage.get("input_tokens", 0)
                    _out_tok = _actual_usage.get("output_tokens", 0)
                    if _in_tok or _out_tok:
                        try:
                            from app.db.session import async_session
                            from app.modules.agent_runtime_v2.budget import BudgetService
                            async with async_session() as _budget_session:
                                _budget_svc = BudgetService(_budget_session)
                                await _budget_svc.record_usage(
                                    workspace_id=workspace_id,
                                    tokens_in=_in_tok,
                                    tokens_out=_out_tok,
                                )
                                await _budget_session.commit()
                        except Exception:
                            logger.debug(
                                "Budget record_usage failed; non-fatal",
                                exc_info=True,
                            )

                return result

            except TimeoutError:
                from app.modules.agent_runtime_v2.trace import emit_trace_event

                await emit_trace_event(
                    "llm",
                    "timeout",
                    provider=provider,
                    model=model,
                    operation=operation or "unknown",
                    timeout_seconds=timeout,
                    fallback=i > 0,
                )
                logger.warning(
                    "Model %s/%s timed out after %.0fs, trying next",
                    provider,
                    model,
                    timeout,
                )
                last_error = TimeoutError(
                    f"{provider}/{model} timed out after {timeout}s"
                )

            except Exception as e:
                from app.modules.agent_runtime_v2.trace import emit_trace_event

                await emit_trace_event(
                    "llm",
                    "error",
                    provider=provider,
                    model=model,
                    operation=operation or "unknown",
                    fallback=i > 0,
                    error_type=type(e).__name__,
                    error=str(e),
                )
                # Check for Cerebras rate limit (429)
                if provider == "cerebras" and _is_rate_limit_error(e):
                    self._cooldown_cerebras()
                    last_error = e
                    continue

                logger.warning(
                    "Model %s/%s failed: %s, trying next", provider, model, e
                )
                last_error = e

        raise last_error or RuntimeError("All models in fallback chain failed")

    # ── Single-model raw seam (tool-loop path) ──

    async def _call_one_model(
        self,
        *,
        provider: str,
        model: str,
        contents: str | list,
        config: types.GenerateContentConfig | None,
        timeout: float,
        prompt_cache: dict[str, Any] | None = None,
    ) -> tuple[Any, str, str]:
        """Call one model and return the RAW provider response.

        Unlike ``generate``/``_normalize_gemini``, this returns the raw
        response object so callers can read function-call parts that the
        text normalizer drops. Reuses the centralized Gemini client and the
        same thinking-config retry + cached-content path as ``generate``.

        Returns ``(raw_response, model_used, provider)``. Per-call accounting
        (trace events, token tracker, budget) is the caller's responsibility —
        ``generate_with_tools`` mirrors ``generate_with_fallback`` for that.

        DRY-debt: the gemini branch below mirrors the gemini branch of
        ``generate`` (the wait_for + thinking-config one-shot retry). If you
        change the retry/cache logic in ``generate``, mirror it here too.
        """
        if provider == "gemini":
            effective_config = await self._gemini_effective_config(
                model=model,
                config=config,
                prompt_cache=prompt_cache,
            )
            try:
                response = await asyncio.wait_for(
                    self._gemini_client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=effective_config,
                    ),
                    timeout=timeout,
                )
            except Exception as exc:
                if not (
                    _has_thinking_config(effective_config)
                    and _is_unsupported_thinking_config_error(exc)
                ):
                    raise
                logger.warning(
                    "Model %s rejected thinking_config; retrying once without it",
                    model,
                )
                response = await asyncio.wait_for(
                    self._gemini_client.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=_without_thinking_config(effective_config),
                    ),
                    timeout=timeout,
                )
            return response, model, provider

        # Function-calling drives a Gemini-shaped tool loop; the Cerebras
        # OpenAI shim is not wired for it. Raise so the chain falls through.
        raise RuntimeError(
            f"_call_one_model: provider {provider!r} does not support tool calls"
        )

    # ── Tool-enabled fallback chain (Hermes) ──

    async def generate_with_tools(
        self,
        *,
        chain: list[ChainItem],
        contents: str | list,
        tools: list[dict],
        timeout: float = DEFAULT_TIMEOUT,
        workspace_id: int | None = None,
        operation: str | None = None,
        system_instruction: str | None = None,
        prompt_cache: dict[str, Any] | None = None,
        force_function_calling: bool = False,
        allowed_function_names: list[str] | None = None,
        current_turn_media: list | None = None,
        live_media_text: str | None = None,
    ) -> "LLMToolResponse":
        """Gemini function-calling with automatic model fallback.

        Mirrors ``generate_with_fallback`` for everything that is NOT
        normalization: workspace chain override, budget reserve/record, trace
        events (attempt/success/timeout/error), and TokenTracker recording.
        This keeps Hermes-driven tool turns visible in the trace timeline and
        in the per-workspace token ledger (so BudgetService + TokenTracker
        stay in agreement). Returns an LLMToolResponse that preserves
        function-call parts.

        ``system_instruction`` becomes ``config.system_instruction`` (Gemini
        takes the system prompt that way, not as a content turn) — the Hermes
        OpenAI shim translates the "system" message into this.
        """
        from app.brain.token_tracker import get_token_context

        include_thoughts = operation == "hermes_reply"
        config = build_tool_config(
            tools,
            system_instruction=system_instruction,
            include_thoughts=include_thoughts,
            thinking_level=_tool_loop_thinking_level(operation, include_thoughts),
            temperature=_tool_loop_temperature(operation),
            force_function_calling=force_function_calling,
            allowed_function_names=allowed_function_names,
        )

        # ── OpenAI-format messages -> Gemini types.Content (the Gemini boundary) ──
        # The Hermes shim hands us OpenAI-shaped message dicts. google-genai
        # rejects those; translate to list[types.Content] once (same for every
        # model in the chain). A bare string is a valid single-turn prompt and
        # passes through untouched.
        if isinstance(contents, list):
            gemini_contents: str | list = _openai_messages_to_gemini_contents(
                contents, current_turn_media, live_media_text
            )
        else:
            gemini_contents = contents

        # ── Workspace model overrides (mirrors generate_with_fallback) ──
        ctx_workspace_id, ctx_operation = get_token_context()
        policy_workspace_id = workspace_id or ctx_workspace_id
        policy_operation = operation or ctx_operation
        chain = resolve_chain_for_operation(
            operation=policy_operation,
            requested_chain=chain,
            overrides=await _load_workspace_llm_overrides(policy_workspace_id),
        )

        # ── Budget enforcement (mirrors generate_with_fallback) ──
        if workspace_id is not None:
            from app.db.session import async_session
            from app.modules.agent_runtime_v2.budget import BudgetService

            tokens_estimate = len(str(contents)) // 4
            async with async_session() as _budget_session:
                budget_svc = BudgetService(_budget_session)
                await budget_svc.check_and_reserve(
                    workspace_id=workspace_id,
                    tokens_estimate=tokens_estimate,
                )
                await _budget_session.commit()

        last_exc: Exception | None = None
        for i, (provider, model) in enumerate(chain):
            if provider == "cerebras" and not self._is_cerebras_available():
                continue
            try:
                from app.modules.agent_runtime_v2.trace import emit_trace_event

                await emit_trace_event(
                    "llm",
                    "attempt",
                    provider=provider,
                    model=model,
                    operation=operation or "unknown",
                    attempt=i + 1,
                    fallback=i > 0,
                    native_media=_native_media_trace_summary(current_turn_media),
                    request_summary=_build_llm_request_summary(
                        contents=contents,
                        config=config,
                        prompt_cache=prompt_cache,
                    ),
                )
                call_start = time.monotonic()
                response, model_used, prov = await self._call_one_model(
                    provider=provider,
                    model=model,
                    contents=gemini_contents,
                    config=config,
                    timeout=timeout,
                    prompt_cache=prompt_cache if provider == "gemini" else None,
                )
                latency_ms = round((time.monotonic() - call_start) * 1000)
                is_fallback = i > 0
                logger.info(
                    "llm_call",
                    extra={
                        "provider": prov,
                        "model": model_used,
                        "latency_ms": latency_ms,
                        "fallback": is_fallback,
                    },
                )
                result = _normalize_tool_response(response, model_used, prov)
                await emit_trace_event(
                    "llm",
                    "success",
                    provider=prov,
                    model=model_used,
                    operation=operation or "unknown",
                    latency_ms=latency_ms,
                    fallback=is_fallback,
                    usage=result.usage or {},
                    output_text_preview=_trace_preview(result.text, limit=500),
                    tool_calls=[
                        {
                            "name": call.name,
                            "arguments": _trace_preview(call.arguments, limit=300),
                        }
                        for call in result.tool_calls
                    ],
                    thought_summaries=[
                        _trace_preview(summary, limit=600)
                        for summary in result.thought_summaries
                    ],
                )

                # Record token usage — use explicit params or context vars
                from app.brain.token_tracker import get_token_context, get_token_tracker
                tracker = get_token_tracker()
                if tracker is not None:
                    ctx_ws, ctx_op = get_token_context()
                    ws = workspace_id or ctx_ws or 1  # fallback to workspace 1
                    op = operation or ctx_op or "unknown"
                    usage = result.usage or {}
                    in_tok = usage.get("input_tokens", 0)
                    out_tok = usage.get("output_tokens", 0)
                    if in_tok or out_tok:
                        await tracker.record(
                            workspace_id=ws,
                            operation=op,
                            provider=result.provider or "unknown",
                            input_tokens=in_tok,
                            output_tokens=out_tok,
                        )

                # ── Budget: record actual usage (mirrors generate_with_fallback) ──
                if workspace_id is not None:
                    _actual_usage = result.usage or {}
                    _in_tok = _actual_usage.get("input_tokens", 0)
                    _out_tok = _actual_usage.get("output_tokens", 0)
                    if _in_tok or _out_tok:
                        try:
                            from app.db.session import async_session
                            from app.modules.agent_runtime_v2.budget import BudgetService
                            async with async_session() as _budget_session:
                                _budget_svc = BudgetService(_budget_session)
                                await _budget_svc.record_usage(
                                    workspace_id=workspace_id,
                                    tokens_in=_in_tok,
                                    tokens_out=_out_tok,
                                )
                                await _budget_session.commit()
                        except Exception:
                            logger.debug(
                                "Budget record_usage failed; non-fatal",
                                exc_info=True,
                            )

                return result

            except TimeoutError as exc:
                from app.modules.agent_runtime_v2.trace import emit_trace_event

                await emit_trace_event(
                    "llm",
                    "timeout",
                    provider=provider,
                    model=model,
                    operation=operation or "unknown",
                    timeout_seconds=timeout,
                    fallback=i > 0,
                )
                logger.warning(
                    "generate_with_tools: %s/%s timed out after %.0fs, trying next",
                    provider,
                    model,
                    timeout,
                )
                last_exc = TimeoutError(
                    f"{provider}/{model} timed out after {timeout}s"
                )
                last_exc.__cause__ = exc

            except Exception as exc:
                from app.modules.agent_runtime_v2.trace import emit_trace_event

                await emit_trace_event(
                    "llm",
                    "error",
                    provider=provider,
                    model=model,
                    operation=operation or "unknown",
                    fallback=i > 0,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                last_exc = exc
                logger.warning(
                    "generate_with_tools: %s/%s failed: %s, trying next",
                    provider,
                    model,
                    exc,
                )

        raise RuntimeError(
            f"generate_with_tools: chain exhausted ({last_exc})"
        )


# ── Rate limit detection ──


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception is a rate limit error (429)."""
    # openai.RateLimitError
    exc_type = type(exc).__name__
    if exc_type == "RateLimitError":
        return True
    # Check for status_code attribute
    return hasattr(exc, "status_code") and exc.status_code == 429


# ── Singleton + module-level API ──

_instance: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Get the shared LLMClient. Lazy init on first call."""
    global _instance
    if _instance is None:
        _instance = LLMClient()
    return _instance


def get_files_client() -> genai.Client:
    """Get a Gemini client that supports the Files API.

    google-genai only supports ``files.upload`` on the Gemini Developer API
    client. Runtime generation can still use Vertex; file upload needs the
    API-key client when Vertex is forced for model calls.
    """
    settings = get_settings()
    if settings.gemini_api_key:
        return genai.Client(api_key=settings.gemini_api_key, vertexai=False)
    return get_llm_client()._gemini_client


# Module-level convenience (callers keep doing `from app.brain.llm import generate_with_fallback`)
async def generate_with_fallback(
    *,
    chain: list[ChainItem],
    contents: str | list,
    config: types.GenerateContentConfig | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    workspace_id: int | None = None,
    operation: str | None = None,
    prompt_cache: dict[str, Any] | None = None,
) -> LLMResponse:
    """Module-level convenience wrapper around LLMClient.generate_with_fallback.

    Pass workspace_id + operation to record token usage via TokenTracker.
    """
    return await get_llm_client().generate_with_fallback(
        chain=chain,
        contents=contents,
        config=config,
        timeout=timeout,
        workspace_id=workspace_id,
        operation=operation,
        prompt_cache=prompt_cache,
    )


async def generate_structured_json(
    *,
    chain: list[ChainItem],
    system: str,
    prompt: str,
    response_schema: Any | None = None,
    operation: str,
    workspace_id: int | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    prompt_cache: dict[str, Any] | None = None,
) -> dict:
    """Call LLM and return parsed JSON. Handles markdown-wrapped JSON.

    Use response_schema for schema-constrained output (faster, no parse failures).
    Without it, uses response_mime_type="application/json" and parses the text.
    """
    policy = get_llm_policy(operation, fallback_chain=chain)

    response = await generate_with_fallback(
        chain=policy.chain,
        contents=prompt,
        config=build_structured_json_config(
            policy=policy,
            system_instruction=system,
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        ),
        timeout=policy.timeout,
        operation=operation,
        workspace_id=workspace_id,
        prompt_cache=prompt_cache,
    )

    text = (response.text or "").strip()
    if not text:
        return {}

    # Handle markdown-wrapped JSON (```json ... ```)
    if text.startswith("```"):
        start = text.find("\n") + 1
        end = text.rfind("```")
        if start > 0 and end > start:
            text = text[start:end].strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Try extracting JSON object from surrounding text
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start : brace_end + 1])
            except (json.JSONDecodeError, ValueError):
                pass
        return {}


async def generate_with_tools(
    *,
    chain: list[ChainItem],
    contents: str | list,
    tools: list[dict],
    timeout: float = DEFAULT_TIMEOUT,
    workspace_id: int | None = None,
    operation: str | None = None,
    system_instruction: str | None = None,
    prompt_cache: dict[str, Any] | None = None,
    force_function_calling: bool = False,
    allowed_function_names: list[str] | None = None,
    current_turn_media: list | None = None,
    live_media_text: str | None = None,
) -> LLMToolResponse:
    """Module-level convenience wrapper around LLMClient.generate_with_tools.

    Gemini function-calling through the centralized client, with model
    fallback, trace+token audit, and workspace budget. Returns an
    LLMToolResponse whose ``tool_calls`` preserve function-call parts.
    """
    return await get_llm_client().generate_with_tools(
        chain=chain,
        contents=contents,
        tools=tools,
        timeout=timeout,
        workspace_id=workspace_id,
        operation=operation,
        system_instruction=system_instruction,
        prompt_cache=prompt_cache,
        force_function_calling=force_function_calling,
        allowed_function_names=allowed_function_names,
        current_turn_media=current_turn_media,
        live_media_text=live_media_text,
    )
