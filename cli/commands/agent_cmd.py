"""`oqim agent` — read-only conversation/trace inspection.

`agent tail` replaces hand-rolled `messages` + `hermes_runs.details` spelunking
with a terse bubbles+trace block. (`agent sim` is a deferred fast-follow; see the
spec — the dispatcher has no side-effect-free reply seam yet.)"""
from __future__ import annotations

import asyncio

import typer

import cli.agentio as agentio
from cli._paths import ensure_backend_path
from cli.agentio import bubble, dur, emit, kv, pct, tokens

app = typer.Typer(name="agent", help="Inspect agent conversations and turns (read-only)")


def _trace_line(run: dict) -> str:
    tm = run.get("trace_metrics") or {}
    breakdown = tm.get("token_breakdown") or {}
    raw_in = int(breakdown.get("raw_input_tokens", run.get("tokens_in") or 0))
    cached = int(tm.get("cached_content_tokens", breakdown.get("cached_content_tokens", 0)))
    cache_frac = (cached / raw_in) if raw_in else None
    ground = len(run.get("source_refs") or [])
    return "   " + kv(
        turn=run.get("run_id"),
        in_=tokens(raw_in),
        cache=pct(cache_frac),
        calls=int(run.get("llm_calls") or 0),
        tools=run.get("output_action") or "none",
        ground=ground,
        fb=int(tm.get("fallback_calls", 0)),
        t=dur(run.get("total_latency_ms")),
    )


def _render_tail(bubbles: list[dict], latest_run: dict | None, *, full: bool = False) -> None:
    max_chars = 100_000 if full else 120
    for b in bubbles:
        print(bubble(b.get("sender_type") or "system", b.get("content") or "",
                     max_chars=max_chars))
    if latest_run is not None:
        print(_trace_line(latest_run))


async def _fetch_tail(*, workspace: int, conversation: int, n: int) -> tuple[list[dict], dict | None]:
    from sqlalchemy import select

    from app.db.session import async_session
    from app.models.hermes_run import HermesRun
    from app.models.message import Message

    async with async_session() as db:
        msg_rows = (await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation)
            .order_by(Message.created_at.desc())
            .limit(n)
        )).scalars().all()
        bubbles = [
            {"sender_type": m.sender_type, "content": m.content}
            for m in reversed(msg_rows)
        ]
        run = (await db.execute(
            select(HermesRun)
            .where(HermesRun.workspace_id == workspace)
            .where(HermesRun.conversation_id == conversation)
            .where(HermesRun.started_at.is_not(None))
            .order_by(HermesRun.started_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    latest = None
    if run is not None:
        # Attributes were eagerly loaded by scalars().all() (session uses
        # expire_on_commit=False), so reading them after the session closes is safe.
        latest = {
            "run_id": run.run_id, "tokens_in": run.tokens_in, "llm_calls": run.llm_calls,
            "total_latency_ms": run.total_latency_ms, "output_action": run.output_action,
            "source_refs": run.source_refs,
            "trace_metrics": (run.details or {}).get("trace_metrics") or {},
        }
    return bubbles, latest


@app.command("tail")
def tail(
    conversation: int = typer.Option(..., "--conv", help="Conversation ID"),
    n: int = typer.Option(3, "-n", "--n", help="Number of recent messages"),
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    full: bool = typer.Option(False, "--full", help="Do not truncate bubbles"),
    json_mode: bool = typer.Option(False, "--json", help="Emit compact JSON"),
) -> None:
    """Show the last N bubbles of a conversation + the latest turn's trace."""
    ensure_backend_path()
    bubbles, latest = asyncio.run(_fetch_tail(workspace=workspace, conversation=conversation, n=n))
    result = {"bubbles": bubbles, "latest_run": latest}
    emit(result, json_mode=json_mode or agentio.OUTPUT_JSON,
         render=lambda r: _render_tail(r["bubbles"], r["latest_run"], full=full))
