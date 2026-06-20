"""`oqim metrics` — terse per-turn / window / worst-turn cost + token metrics.

Reads `hermes_runs` (row columns + details["trace_metrics"]); no recomputation,
no new tables. See the spec for the output contract."""
from __future__ import annotations

import asyncio

import typer

import cli.agentio as agentio
from cli._paths import ensure_backend_path
from cli.agentio import dur, emit, kv, money, pct, tokens

# Approximate Gemini Flash pricing, USD per 1M tokens (single source; update here
# if the model/price changes). Cached input bills at the discounted rate.
_PRICE_PER_1M = {"in": 0.10, "cached": 0.025, "out": 0.40}


def _estimate_cost(*, raw_in: int, cached: int, out: int) -> float:
    # Clamp keeps cost non-negative when partial telemetry reports cached >
    # raw_in; in that rare case fresh input bills as 0 (a slight under-report).
    fresh_in = max(0, int(raw_in) - int(cached))
    return (
        fresh_in * _PRICE_PER_1M["in"]
        + int(cached) * _PRICE_PER_1M["cached"]
        + int(out) * _PRICE_PER_1M["out"]
    ) / 1_000_000


def _row_metrics(row: dict) -> dict:
    tm = row.get("trace_metrics") or {}
    breakdown = tm.get("token_breakdown") or {}
    raw_in = int(breakdown.get("raw_input_tokens", row.get("tokens_in") or 0))
    out = int(breakdown.get("output_tokens", row.get("tokens_out") or 0))
    cached = int(tm.get("cached_content_tokens", breakdown.get("cached_content_tokens", 0)))
    cache_frac = (cached / raw_in) if raw_in else None
    return {
        "run_id": row.get("run_id"),
        "started_at": row.get("started_at"),
        "in": raw_in,
        "out": out,
        "cached": cached,
        "cache_frac": cache_frac,
        "calls": int(row.get("llm_calls") or 0),
        "fallbacks": int(tm.get("fallback_calls", 0)),
        "latency_ms": row.get("total_latency_ms"),
        "cost": _estimate_cost(raw_in=raw_in, cached=cached, out=out),
    }


def _aggregate(rows: list[dict], *, capped: bool = False) -> dict:
    metrics = [_row_metrics(r) for r in rows]
    if not metrics:
        return {"last": None, "window": {"turns": 0}, "worst": None, "capped": capped}
    last = metrics[0]
    worst = max(metrics, key=lambda m: m["cost"])
    return {
        "last": last,
        "window": {
            "turns": len(metrics),
            "cost": sum(m["cost"] for m in metrics),
            "in": sum(m["in"] for m in metrics),
            "cached": sum(m["cached"] for m in metrics),
            "fallbacks": sum(m["fallbacks"] for m in metrics),
        },
        "worst": worst,
        "capped": capped,
    }


def _render(agg: dict) -> None:
    last = agg["last"]
    if last is None:
        print("no runs in window")
        return
    print("last  " + kv(
        cost=money(last["cost"]), in_=tokens(last["in"]), cache=pct(last["cache_frac"]),
        out=tokens(last["out"]), calls=last["calls"], fb=last["fallbacks"],
        t=dur(last["latency_ms"]),
    ) + f"  (run {last['run_id']})")
    w = agg["window"]
    win_cache = (w["cached"] / w["in"]) if w.get("in") else None
    print("24h   " + kv(
        turns=w["turns"], cost=money(w.get("cost")), in_=tokens(w.get("in")),
        cache=pct(win_cache), fb=w["fallbacks"],
    ))
    worst = agg["worst"]
    print("worst " + kv(cost=money(worst["cost"]), cache=pct(worst["cache_frac"]),
                        run=worst["run_id"]))
    if agg.get("capped"):
        print("note  capped at 500 runs; narrow with --agent/--conv")


async def _fetch(*, workspace: int, agent: int | None, conversation: int | None,
                 hours: int) -> list[dict]:
    from datetime import timedelta

    from sqlalchemy import select

    from app.db.base import utc_now  # repo helper used across models (verified path)
    from app.db.session import async_session
    from app.models.hermes_run import HermesRun

    since = utc_now() - timedelta(hours=hours)
    async with async_session() as db:
        stmt = (
            select(HermesRun)
            .where(HermesRun.workspace_id == workspace)
            .where(HermesRun.started_at.is_not(None))
            .where(HermesRun.started_at >= since)
            .order_by(HermesRun.started_at.desc())
            .limit(500)
        )
        if agent is not None:
            stmt = stmt.where(HermesRun.agent_id == agent)
        if conversation is not None:
            stmt = stmt.where(HermesRun.conversation_id == conversation)
        runs = (await db.execute(stmt)).scalars().all()
    out = []
    for r in runs:
        out.append({
            "run_id": r.run_id,
            "started_at": r.started_at,
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
            "llm_calls": r.llm_calls,
            "total_latency_ms": r.total_latency_ms,
            "trace_metrics": (r.details or {}).get("trace_metrics") or {},
        })
    return out


def metrics(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    agent: int = typer.Option(None, "--agent", help="Agent ID filter"),
    conversation: int = typer.Option(None, "--conv", help="Conversation ID filter"),
    hours: int = typer.Option(24, "--hours", help="Window size in hours"),
    json_mode: bool = typer.Option(False, "--json", help="Emit compact JSON"),
) -> None:
    """Terse cost/token/cache metrics for recent agent turns."""
    ensure_backend_path()
    rows = asyncio.run(_fetch(workspace=workspace, agent=agent,
                              conversation=conversation, hours=hours))
    agg = _aggregate(rows, capped=len(rows) >= 500)
    emit(agg, json_mode=json_mode or agentio.OUTPUT_JSON, render=_render)
