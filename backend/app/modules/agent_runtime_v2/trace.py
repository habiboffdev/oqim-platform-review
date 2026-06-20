from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.db.base import utc_now

TraceEvent = dict[str, Any]
TraceSink = Callable[[TraceEvent], Awaitable[None] | None]

_TAG_BLOCK_RE = re.compile(
    r"<(?P<tag>[a-zA-Z0-9_:-]+)>\s*(?P<body>[\s\S]*?)\s*</(?P=tag)>"
)


@dataclass(slots=True)
class AgentRuntimeTraceSession:
    sink: TraceSink | None = None
    events: list[TraceEvent] = field(default_factory=list)
    sequence: int = 0


_TRACE_SESSION: ContextVar[AgentRuntimeTraceSession | None] = ContextVar(
    "agent_runtime_trace_session",
    default=None,
)


@contextmanager
def agent_runtime_trace_session(*, sink: TraceSink | None = None):
    session = AgentRuntimeTraceSession(sink=sink)
    token = _TRACE_SESSION.set(session)
    try:
        yield session
    finally:
        _TRACE_SESSION.reset(token)


def get_trace_session() -> AgentRuntimeTraceSession | None:
    return _TRACE_SESSION.get()


def get_trace_events() -> list[TraceEvent]:
    session = get_trace_session()
    return list(session.events) if session is not None else []


def summarize_trace_metrics(events: list[TraceEvent]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cached_content_tokens = 0
    total_thought_tokens = 0
    total_latency_ms = 0
    fallback_count = 0

    for event in events:
        if event.get("stage") != "llm" or event.get("event") != "success":
            continue
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cached_content_tokens = int(usage.get("cached_content_tokens", 0) or 0)
        cache_savings_tokens = min(input_tokens, cached_content_tokens)
        cache_effective_input_tokens = max(0, input_tokens - cache_savings_tokens)
        thought_tokens = int(usage.get("thought_tokens", 0) or 0)
        latency_ms = int(event.get("latency_ms", 0) or 0)
        fallback = bool(event.get("fallback"))
        fallback_count += int(fallback)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_cached_content_tokens += cached_content_tokens
        total_thought_tokens += thought_tokens
        total_latency_ms += latency_ms
        calls.append(
            {
                "sequence": event.get("sequence"),
                "operation": event.get("operation") or "unknown",
                "provider": event.get("provider") or "unknown",
                "model": event.get("model") or "unknown",
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cached_content_tokens": cached_content_tokens,
                "cache_savings_tokens": cache_savings_tokens,
                "cache_effective_input_tokens": cache_effective_input_tokens,
                "cache_effective_total_tokens": (
                    cache_effective_input_tokens + output_tokens + thought_tokens
                ),
                "thought_tokens": thought_tokens,
                "fallback": fallback,
                "output_text_preview": event.get("output_text_preview") or "",
                "tool_calls": event.get("tool_calls") or [],
                "thought_summaries": event.get("thought_summaries") or [],
            }
        )

    total_cache_savings_tokens = min(
        total_input_tokens,
        total_cached_content_tokens,
    )
    total_cache_effective_input_tokens = max(
        0,
        total_input_tokens - total_cache_savings_tokens,
    )
    total_cache_effective_total_tokens = (
        total_cache_effective_input_tokens
        + total_output_tokens
        + total_thought_tokens
    )

    return {
        "llm_calls": len(calls),
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "cached_content_tokens": total_cached_content_tokens,
        "cache_savings_tokens": total_cache_savings_tokens,
        "cache_effective_input_tokens": total_cache_effective_input_tokens,
        "cache_effective_total_tokens": total_cache_effective_total_tokens,
        "thought_tokens": total_thought_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "token_breakdown": {
            "raw_input_tokens": total_input_tokens,
            "cached_content_tokens": total_cached_content_tokens,
            "cache_savings_tokens": total_cache_savings_tokens,
            "cache_effective_input_tokens": total_cache_effective_input_tokens,
            "output_tokens": total_output_tokens,
            "thought_tokens": total_thought_tokens,
            "raw_total_tokens": total_input_tokens + total_output_tokens,
            "cache_effective_total_tokens": total_cache_effective_total_tokens,
        },
        "llm_latency_ms": total_latency_ms,
        "fallback_calls": fallback_count,
        "calls": calls,
    }


def trace_preview(value: Any, *, limit: int = 600) -> str:
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


def build_prompt_snapshot(
    *,
    prompt: str,
    system_instruction: str | None = None,
    section_tags: tuple[str, ...] = (),
    extra: dict[str, Any] | None = None,
    prompt_limit: int = 1200,
    section_limit: int = 500,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "prompt_chars": len(prompt or ""),
        "prompt_preview": trace_preview(prompt, limit=prompt_limit),
    }
    if system_instruction:
        snapshot["system_instruction_chars"] = len(system_instruction)
        snapshot["system_instruction_preview"] = trace_preview(
            system_instruction,
            limit=min(prompt_limit, 900),
        )
    sections: dict[str, str] = {}
    if prompt:
        extracted = _extract_tag_blocks(prompt)
        for tag in section_tags:
            body = extracted.get(tag)
            if body:
                sections[tag] = trace_preview(body, limit=section_limit)
    if sections:
        snapshot["sections"] = sections
    if extra:
        snapshot["extra"] = extra
    return snapshot


def split_style_example_preview(style_examples: str, *, limit: int = 2) -> list[str]:
    if not style_examples.strip():
        return []
    chunks = [
        trace_preview(part, limit=320)
        for part in style_examples.split("Example ")
        if part.strip()
    ]
    return [f"Example {chunk}" for chunk in chunks[:limit]]


def _extract_tag_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for match in _TAG_BLOCK_RE.finditer(text or ""):
        tag = match.group("tag").strip()
        body = (match.group("body") or "").strip()
        if tag and body and tag not in blocks:
            blocks[tag] = body
    return blocks


async def emit_trace_event(stage: str, event: str, /, **payload: Any) -> TraceEvent | None:
    session = get_trace_session()
    if session is None:
        return None

    session.sequence += 1
    record: TraceEvent = {
        "sequence": session.sequence,
        "at": utc_now().isoformat(),
        "stage": stage,
        "event": event,
    }
    record.update(payload)
    session.events.append(record)

    if session.sink is not None:
        result = session.sink(record)
        if inspect.isawaitable(result):
            await result

    return record
