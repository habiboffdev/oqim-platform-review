"""Terse-first output for the oqim CLI (token-efficient agent control surface).

Default output is a dense, decoration-free block (no ANSI, no box-drawing) so an
agent reading captured stdout pays minimal tokens. `--json` is the only escape
hatch and emits compact (un-indented) JSON. See
docs/superpowers/specs/2026-06-13-oqim-cli-agent-control-surface-design.md.
"""
from __future__ import annotations

import json
from typing import Any, Callable

# Set by the app callback (--json) or a command's own --json option.
OUTPUT_JSON: bool = False


def tokens(n: int | None) -> str:
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        k = round(n / 1000)
        if k >= 1000:
            return f"{n / 1_000_000:.1f}M"
        return f"{k}k"
    return str(n)


def money(usd: float | None) -> str:
    if not usd:  # None or 0/0.0
        return "$0"
    if usd >= 1:
        return f"${usd:.2f}"
    return f"${usd:.5f}"


def pct(fraction: float | None) -> str:
    if fraction is None:
        return "n/a"
    return f"{round(fraction * 100)}%"


def dur(ms: float | None) -> str:
    if ms is None:
        return "n/a"
    ms = float(ms)
    if ms >= 10_000:
        return f"{round(ms / 1000)}s"
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{round(ms)}ms"


def kv(**fields: Any) -> str:
    """`cost=$0.0002 in=1180 cache=86%` — drops None, keeps insertion order.

    A trailing underscore in a key is stripped (so `in_=` renders as `in=`)."""
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key.rstrip('_')}={value}")
    return " ".join(parts)


def line(label: str, **fields: Any) -> str:
    body = kv(**fields)
    return f"{label}  {body}" if body else label


def bubble(role: str, text: str, *, max_chars: int = 120) -> str:
    prefix = "c>" if role == "customer" else "a>"
    flat = " ".join((text or "").split())
    if len(flat) > max_chars:
        flat = flat[:max_chars] + "…"
    return f"{prefix} {flat}"


def emit(result: dict, *, json_mode: bool, render: Callable[[dict], None]) -> None:
    """Render `result` terse-by-default; compact JSON when json_mode (or OUTPUT_JSON)."""
    if json_mode or OUTPUT_JSON:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        render(result)
