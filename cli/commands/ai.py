"""oqim ai — AI debugging commands: reply, search, voice, prepass, classify."""
import asyncio
import json
import logging
import sys
import time
from contextlib import contextmanager
from typing import Annotated, Optional

import typer

from cli.config import BACKEND_DIR
from cli.output import header, table

app = typer.Typer(no_args_is_help=True)
sandbox_app = typer.Typer(no_args_is_help=True)
app.add_typer(sandbox_app, name="sandbox", help="Synthetic customer sandbox for the Seller Agent runtime")


def _ensure_backend_path() -> None:
    """Add backend directory to sys.path so app.* imports work."""
    backend_str = str(BACKEND_DIR)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)


@contextmanager
def _suppress_info_logs(enabled: bool):
    if not enabled:
        yield
        return

    previous_disable = logging.root.manager.disable
    # JSON mode should stay machine-readable even when the backend emits
    # warnings or recoverable provider errors during degraded paths.
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous_disable)


# ── reply ──────────────────────────────────────────────────────────────────────


@app.command("reply")
def reply(
    message: Annotated[str, typer.Argument(help="Customer message to test Seller Agent reply generation with")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Generate a test Seller Agent reply for a message and show result, chips, and latency."""
    with _suppress_info_logs(json_mode):
        asyncio.run(_reply_impl(message=message, workspace=workspace, json_mode=json_mode))


async def _reply_impl(message: str, workspace: int, json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from app.db.session import async_session
        from app.modules.action_runtime.seller_agent_runtime.bridge import (
            generate_seller_agent_reply,
        )
        from app.models.conversation import Conversation
        from app.models.message import Message
        from app.models.workspace import Workspace
        from sqlalchemy import select
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv:")
        typer.echo("    cd backend && source venv/bin/activate && oqim ai reply '...'")
        raise typer.Exit(1)

    if not json_mode:
        header(f"Seller Agent Reply — workspace {workspace}")
        typer.echo(f"\n  message: {message!r}\n")

    start = time.monotonic()

    async with async_session() as db:
        # Verify workspace exists
        ws_row = await db.execute(
            select(Workspace).where(Workspace.id == workspace)
        )
        ws = ws_row.scalar_one_or_none()
        if not ws:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)

        # Find a real conversation to test with
        conv_row = await db.execute(
            select(Conversation)
            .where(Conversation.workspace_id == workspace)
            .limit(1)
        )
        conversation = conv_row.scalar_one_or_none()

        if not conversation:
            typer.echo("  No conversations found in this workspace.")
            typer.echo("  Complete onboarding or run 'oqim mock send' to append a canonical test message.")
            raise typer.Exit(1)

        if not json_mode:
            typer.echo(f"  Using conversation id={conversation.id} (chat_id={conversation.telegram_chat_id})")

        # Create a temporary test message so Seller Agent has a real trigger_message_id.
        # The bridge commits internally, so we clean up both artifacts afterward.
        temp_msg = Message(
            conversation_id=conversation.id,
            content=message,
            sender_type="customer",
            telegram_message_id=None,  # NULL avoids unique constraint on (conversation_id, tg_msg_id)
        )
        db.add(temp_msg)
        await db.flush()
        temp_msg_id = temp_msg.id

        try:
            ai_reply = await generate_seller_agent_reply(
                conversation_id=conversation.id,
                trigger_message_id=temp_msg_id,
                db=db,
            )
        except Exception as e:
            typer.echo(f"  generate_seller_agent_reply raised: {type(e).__name__}: {e}")
            raise typer.Exit(1)

        # Clean up: delete artifacts in dependency order so FK constraints pass.
        from sqlalchemy import delete as sql_delete
        from app.models.seller_agent_reply import SellerAgentReply
        from app.models.action_runtime import ActionRuntime
        if ai_reply is not None:
            await db.execute(
                sql_delete(SellerAgentReply).where(SellerAgentReply.id == ai_reply.id)
            )
        await db.execute(
            sql_delete(ActionRuntime).where(ActionRuntime.message_id == temp_msg_id)
        )
        await db.execute(sql_delete(Message).where(Message.id == temp_msg_id))
        await db.commit()

    elapsed_ms = int((time.monotonic() - start) * 1000)

    if ai_reply is None:
        result = {
            "workspace": workspace,
            "message": message,
            "should_reply": False,
            "reply": None,
            "latency_ms": elapsed_ms,
            "note": "Seller Agent planner decided not to reply for this message.",
        }
    else:
        result = {
            "workspace": workspace,
            "message": message,
            "should_reply": True,
            "intent": getattr(ai_reply, "intent", None),
            "reply": ai_reply.draft_content,
            "confidence": getattr(ai_reply, "confidence_score", None),
            "chips": _chip_labels(ai_reply.chips if hasattr(ai_reply, "chips") else []),
            "latency_ms": elapsed_ms,
        }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    if not result["should_reply"]:
        typer.echo(typer.style("  Seller Agent planner: skip (should_reply=false)", fg=typer.colors.YELLOW))
        typer.echo(f"  note: {result['note']}")
    else:
        typer.echo(typer.style("  Seller Agent planner: reply", fg=typer.colors.GREEN))
        if result.get("intent"):
            typer.echo(f"  intent:     {result['intent']}")
        if result.get("confidence"):
            typer.echo(f"  confidence: {result['confidence']}")
        typer.echo(f"\n  reply:\n")
        for line in str(result["reply"]).splitlines():
            typer.echo(f"    {line}")
        if result.get("chips"):
            typer.echo(f"\n  chips: {', '.join(result['chips'])}")

    typer.echo(f"\n  latency: {elapsed_ms}ms")


def _chip_labels(raw_chips: object) -> list[str]:
    labels: list[str] = []
    if not isinstance(raw_chips, list):
        return labels
    for chip in raw_chips:
        if isinstance(chip, dict):
            label = chip.get("label")
        else:
            label = getattr(chip, "label", None)
        if isinstance(label, str) and label.strip():
            labels.append(label.strip())
    return labels


# ── knowledge ──────────────────────────────────────────────────────────────────


@app.command("kb-ingest")
def kb_ingest(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    limit: int = typer.Option(25, "--limit", "-n", min=1, max=100, help="Max conversations to mine"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Mine Business Brain conversation-pair memory from existing chats."""
    asyncio.run(_kb_ingest_impl(workspace=workspace, limit=limit, json_mode=json_mode))


async def _kb_ingest_impl(*, workspace: int, limit: int, json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.conversation import Conversation
        from app.models.message import Message
        from app.models.workspace import Workspace
        from app.modules.business_brain.memory import BusinessBrainMemoryService
        from app.modules.business_brain.memory_contracts import ConversationPairMiningInput
        from app.modules.commercial_spine.repository import CommercialSpineRepository
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    start = time.monotonic()
    with _suppress_info_logs(json_mode):
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)

            conversation_ids = (
                await db.scalars(
                    select(Conversation.id)
                    .where(Conversation.workspace_id == workspace)
                    .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                    .limit(limit)
                )
            ).all()
            memory = BusinessBrainMemoryService(
                repository=CommercialSpineRepository(db),
            )
            items: list[dict[str, object]] = []
            for conversation_id in conversation_ids:
                messages = (
                    await db.scalars(
                        select(Message)
                        .where(
                            Message.conversation_id == conversation_id,
                            Message.is_deleted.is_(False),
                        )
                        .order_by(Message.created_at.asc(), Message.id.asc())
                    )
                ).all()
                turns = [
                    {
                        "message_ref": f"message:{message.id}",
                        "sender_type": message.sender_type,
                        "content": message.content or "",
                        "created_at": message.created_at.isoformat()
                        if message.created_at
                        else None,
                    }
                    for message in messages
                    if message.content
                ]
                if not turns:
                    continue
                mined = await memory.mine_conversation_pairs(
                    ConversationPairMiningInput(
                        workspace_id=workspace,
                        conversation_id=conversation_id,
                        source_refs=[f"conversation:{conversation_id}:messages"],
                        turns=turns,
                        correlation_id=f"cli:kb-ingest:{workspace}:{conversation_id}",
                    )
                )
                items.extend(
                    {
                        "fact_id": pair.fact.fact_id,
                        "fact_type": pair.fact.fact_type,
                        "source_refs": list(pair.fact.source_refs),
                    }
                    for pair in mined.pairs
                )
            await db.commit()

    elapsed_ms = int((time.monotonic() - start) * 1000)
    result = {
        "workspace": workspace,
        "conversations": len(conversation_ids),
        "count": len(items),
        "items": items,
        "latency_ms": elapsed_ms,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    header(f"Business Brain Pair Mining — workspace {workspace}")
    typer.echo("")
    typer.echo(f"  conversations: {len(conversation_ids)}")
    typer.echo(f"  pairs:         {len(items)}")
    typer.echo(f"  latency:   {elapsed_ms}ms")
    if not items:
        typer.echo("")
        typer.echo("  No conversation pairs were mined from the selected chats.")
        return

    typer.echo("")
    for item in items[:12]:
        typer.echo(f"  - {item['fact_id']}")
    if len(items) > 12:
        typer.echo(f"  ... and {len(items) - 12} more")


@app.command("kb-reembed")
def kb_reembed(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    only_missing: bool = typer.Option(
        False,
        "--only-missing",
        help="Only fill missing contextual_text / missing embeddings instead of refreshing all active KB rows",
    ),
    limit: int | None = typer.Option(None, "--limit", help="Limit number of KB rows to process"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Backfill contextual KB text and embeddings for existing knowledge rows."""
    asyncio.run(
        _kb_reembed_impl(
            workspace=workspace,
            only_missing=only_missing,
            limit=limit,
            json_mode=json_mode,
        )
    )


async def _kb_reembed_impl(
    *,
    workspace: int,
    only_missing: bool,
    limit: int | None,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.evidence.audit import backfill_knowledge_context_and_embeddings
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    start = time.monotonic()
    with _suppress_info_logs(json_mode):
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)

            result = await backfill_knowledge_context_and_embeddings(
                db=db,
                workspace_id=workspace,
                only_missing=only_missing,
                limit=limit,
            )
            await db.commit()

    payload = result.to_dict()
    payload["latency_ms"] = int((time.monotonic() - start) * 1000)

    if json_mode:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    header(f"AI KB Re-embed — workspace {workspace}")
    typer.echo("")
    typer.echo(f"  scanned:        {payload['scanned']}")
    typer.echo(f"  contextualized: {payload['contextualized']}")
    typer.echo(f"  embedded:       {payload['embedded']}")
    typer.echo(f"  skipped:        {payload['skipped']}")
    typer.echo(f"  latency:        {payload['latency_ms']}ms")


@app.command("rag-audit")
def rag_audit(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Audit workspace evidence health so we can track RAG progress over time."""
    asyncio.run(_rag_audit_impl(workspace=workspace, json_mode=json_mode))


async def _rag_audit_impl(*, workspace: int, json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.evidence.audit import audit_workspace_evidence
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    with _suppress_info_logs(json_mode):
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)
            result = await audit_workspace_evidence(db=db, workspace_id=workspace)

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    header(f"AI RAG Audit — workspace {workspace}")
    typer.echo("")
    knowledge = result["knowledge"]
    style = result["style"]
    voice = result["voice_profile"]
    readiness = result["readiness"]

    typer.echo("  Knowledge")
    typer.echo(f"    active:         {knowledge['active']}")
    typer.echo(f"    embedded:       {knowledge['embedded']} ({knowledge['embedded_pct']:.0%})")
    typer.echo(f"    contextualized: {knowledge['contextualized']} ({knowledge['contextualized_pct']:.0%})")
    typer.echo(f"    confirmed:      {knowledge['confirmed']} ({knowledge['confirmed_pct']:.0%})")
    typer.echo(f"    by_source:      {knowledge['by_source']}")
    typer.echo("")
    typer.echo("  Style")
    typer.echo(f"    pairs:          {style['total_pairs']}")
    typer.echo(f"    intent_labeled: {style['intent_labeled']} ({style['intent_labeled_pct']:.0%})")
    typer.echo(f"    sales_safe:     {style['sales_safe_pairs']} ({style['sales_safe_pct']:.0%})")
    typer.echo(
        f"    safe_labeled:   {style['sales_safe_intent_labeled']} "
        f"({style['sales_safe_intent_labeled_pct']:.0%})"
    )
    typer.echo(f"    by_contact:     {style['by_contact_type']}")
    typer.echo("")
    typer.echo("  Voice")
    typer.echo(f"    exists:         {voice['exists']}")
    typer.echo(f"    quality_score:  {voice['quality_score'] or '—'}")
    typer.echo(f"    messages_seen:  {voice['message_count_analyzed']}")
    typer.echo("")
    typer.echo("  Readiness")
    for key, value in readiness.items():
        mark = "yes" if value else "no"
        typer.echo(f"    {key}: {mark}")


@app.command("runtime-signals")
def runtime_signals(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    days: int = typer.Option(7, "--days", min=1, max=30, help="How many recent days to summarize"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show workspace runtime drift + Seller Agent reply freshness in one operator-facing summary."""
    asyncio.run(
        _runtime_signals_impl(
            workspace=workspace,
            days=days,
            json_mode=json_mode,
        )
    )


async def _runtime_signals_impl(
    *,
    workspace: int,
    days: int,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        import redis.asyncio as aioredis
        from sqlalchemy import select

        from app.core.config import get_settings
        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.services.runtime_signals import load_runtime_signals
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)

            signals = await load_runtime_signals(
                db,
                redis,
                workspace_id=workspace,
                period_days=days,
            )
    finally:
        await redis.aclose()

    payload = signals.to_dict()

    if json_mode:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    header(f"AI Runtime Signals — workspace {workspace}")
    typer.echo("")
    typer.echo(f"  period_days: {payload['period_days']}")
    typer.echo("")
    typer.echo("  Event Spine")
    typer.echo(f"    status:            {payload['event_spine']['status']}")
    typer.echo(f"    publish_failures:  {payload['event_spine']['publish_failures']}")
    typer.echo(f"    global_divergences:{payload['event_spine']['global_divergences']}")
    typer.echo(f"    workspace_divs:    {payload['event_spine']['workspace_divergences']}")
    typer.echo("")
    typer.echo("  Seller Agent Reply Freshness")
    freshness = payload["seller_agent_reply_freshness"]
    typer.echo(f"    replies_total:      {freshness['replies_total']}")
    typer.echo(f"    expired_count:      {freshness['expired_count']}")
    typer.echo(f"    suppressed_count:   {freshness['suppressed_count']}")
    typer.echo(f"    freshness_loss:     {freshness['freshness_loss_count']}")
    typer.echo(f"    freshness_loss_pct: {freshness['freshness_loss_rate']}")
    typer.echo(f"    suppressed_reasons: {freshness['suppressed_reasons']}")


@app.command("style-relabel")
def style_relabel(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    only_missing: bool = typer.Option(
        True,
        "--only-missing/--all",
        help="Only label unlabeled sales-safe style pairs by default",
    ),
    limit: int | None = typer.Option(None, "--limit", help="Limit number of style pairs to relabel"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Backfill sales-style intent labels for conversation pairs."""
    asyncio.run(
        _style_relabel_impl(
            workspace=workspace,
            only_missing=only_missing,
            limit=limit,
            json_mode=json_mode,
        )
    )


async def _style_relabel_impl(
    *,
    workspace: int,
    only_missing: bool,
    limit: int | None,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.evidence.style_labeling import backfill_style_pair_intents
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    start = time.monotonic()
    with _suppress_info_logs(json_mode):
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)
            result = await backfill_style_pair_intents(
                db=db,
                workspace_id=workspace,
                only_missing=only_missing,
                limit=limit,
            )
            await db.commit()

    payload = {
        **result.to_dict(),
        "latency_ms": int((time.monotonic() - start) * 1000),
    }

    if json_mode:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    header(f"AI Style Relabel — workspace {workspace}")
    typer.echo("")
    typer.echo(f"  scanned:  {payload['scanned']}")
    typer.echo(f"  updated:  {payload['updated']}")
    typer.echo(f"  skipped:  {payload['skipped']}")
    typer.echo(f"  failed:   {payload['failed']}")
    typer.echo(f"  latency:  {payload['latency_ms']}ms")


@app.command("seller-eval")
def seller_eval(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    suite: str = typer.Option("regression", "--suite", help="Eval suite name"),
    trials: int = typer.Option(1, "--trials", min=1, max=5, help="Repeat each case this many times"),
    concurrency: int = typer.Option(1, "--concurrency", min=1, max=8, help="Run eval cases in parallel"),
    max_p95_ms: int | None = typer.Option(None, "--max-p95-ms", min=1, help="Fail if case latency p95 exceeds this value"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run the seller-agent evaluation suite with grading, traces, and budgets."""
    asyncio.run(
        _seller_eval_impl(
            workspace=workspace,
            suite=suite,
            trials=trials,
            concurrency=concurrency,
            max_p95_ms=max_p95_ms,
            json_mode=json_mode,
        )
    )


async def _seller_eval_impl(
    *,
    workspace: int,
    suite: str,
    trials: int,
    concurrency: int,
    max_p95_ms: int | None,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.evals.seller_eval import run_seller_eval_suite
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    async with async_session() as db:
        ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)

    with _suppress_info_logs(json_mode):
        report = await run_seller_eval_suite(
            workspace_id=workspace,
            suite=suite,
            trials=trials,
            concurrency=concurrency,
        )
    p95_passed = max_p95_ms is None or report.latency_ms_p95 <= max_p95_ms
    passed = report.hard_failure_count == 0 and p95_passed

    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header(f"Seller Eval — workspace {workspace}")
    typer.echo("")
    typer.echo(f"  suite:         {report.suite}")
    typer.echo(f"  run_id:        {report.run_id}")
    typer.echo(f"  trials:        {report.trials}")
    typer.echo(f"  concurrency:   {report.concurrency}")
    typer.echo(f"  pass_rate:     {report.passed_cases}/{report.total_cases} ({round(report.pass_rate * 100, 1)}%)")
    typer.echo(f"  hard_failures: {report.hard_failure_count}")
    typer.echo(f"  soft_warnings: {report.soft_warning_count}")
    typer.echo(
        "  medians:       "
        f"llm_calls={report.llm_calls_median}  "
        f"latency={int(report.latency_ms_median)}ms  "
        f"p95={int(report.latency_ms_p95)}ms  "
        f"tokens={int(report.total_tokens_median)}"
    )
    if max_p95_ms is not None and not p95_passed:
        typer.echo(f"  latency_p95_budget: expected <= {max_p95_ms}ms")

    for result in report.results:
        typer.echo("")
        label = typer.style("PASS", fg=typer.colors.GREEN) if result.passed else typer.style("FAIL", fg=typer.colors.RED)
        typer.echo(f"  [{label}] {result.case_id} (trial {result.trial})")
        typer.echo(f"    {result.description}")
        typer.echo(
            "    "
            f"llm_calls={result.trace_metrics.get('llm_calls', 0)}  "
            f"latency={result.latency_ms}ms  "
            f"tokens={result.trace_metrics.get('total_tokens', 0)}"
        )
        typer.echo(
            "    "
            f"planner_chars={result.prompt_budgets.get('planner_prompt_chars') or 0}  "
            f"generation_chars={result.prompt_budgets.get('generation_prompt_chars') or 0}"
        )
        if result.content:
            typer.echo("    reply:")
            for line in str(result.content).splitlines():
                typer.echo(f"      {line}")

        failed_checks = [check for check in result.checks if not check.passed]
        if failed_checks:
            typer.echo("    findings:")
            for check in failed_checks[:5]:
                level = "hard" if check.severity == "hard" else "soft"
                typer.echo(f"      [{level}] {check.name}: {check.detail}")

        typer.echo(f"    sandbox_slug: {result.sandbox_slug}")

    raise typer.Exit(0 if passed else 1)


@app.command("bench")
def bench(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    last: int = typer.Option(20, "--last", "-n", min=1, max=200, help="How many recent replies to summarize"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Aggregate per-reply telemetry (latency, llm_calls, tokens) across recent Seller Agent replies."""
    asyncio.run(_bench_impl(workspace=workspace, last=last, json_mode=json_mode))


async def _bench_impl(*, workspace: int, last: int, json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.conversation import Conversation
        from app.models.seller_agent_reply import SellerAgentReply
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    async with async_session() as db:
        rows = (
            await db.scalars(
                select(SellerAgentReply)
                .join(Conversation, Conversation.id == SellerAgentReply.conversation_id)
                .where(Conversation.workspace_id == workspace)
                .order_by(SellerAgentReply.created_at.desc(), SellerAgentReply.id.desc())
                .limit(last)
            )
        ).all()

    items: list[dict[str, object]] = []
    for reply in rows:
        trace_data = reply.trace_data if isinstance(reply.trace_data, dict) else {}
        telemetry = trace_data.get("telemetry") if isinstance(trace_data, dict) else {}
        telemetry = telemetry if isinstance(telemetry, dict) else {}
        items.append(
            {
                "reply_id": reply.id,
                "status": reply.status,
                "confidence": reply.confidence_score,
                "response_time_ms": reply.response_time_ms,
                "llm_calls": telemetry.get("llm_calls", 0),
                "total_tokens": telemetry.get("total_tokens", 0),
                "input_tokens": telemetry.get("input_tokens", 0),
                "output_tokens": telemetry.get("output_tokens", 0),
                "llm_latency_ms": telemetry.get("llm_latency_ms", 0),
            }
        )

    aggregate = _bench_aggregate(items)
    payload = {
        "workspace": workspace,
        "count": len(items),
        "aggregate": aggregate,
        "replies": items,
    }

    if json_mode:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    header(f"Reply Bench — workspace {workspace}")
    typer.echo("")
    if not items:
        typer.echo("  No Seller Agent replies found for this workspace yet.")
        typer.echo("  Generate some (live test or 'oqim ai sandbox send'), then re-run.")
        return
    typer.echo(f"  replies: {len(items)}")
    typer.echo(
        "  latency_ms:  "
        f"p50={aggregate['latency_ms_p50']}  p95={aggregate['latency_ms_p95']}  max={aggregate['latency_ms_max']}"
    )
    typer.echo(
        "  llm_calls:   "
        f"p50={aggregate['llm_calls_p50']}  max={aggregate['llm_calls_max']}"
    )
    typer.echo(
        "  tokens:      "
        f"p50_total={aggregate['total_tokens_p50']}  "
        f"avg_in={aggregate['avg_input_tokens']}  avg_out={aggregate['avg_output_tokens']}"
    )
    typer.echo("")
    table(
        ["id", "status", "conf", "latency_ms", "llm", "in/out tok"],
        [
            [
                item["reply_id"],
                item["status"],
                item["confidence"],
                item["response_time_ms"],
                item["llm_calls"],
                f"{item['input_tokens']}/{item['output_tokens']}",
            ]
            for item in items
        ],
        json_mode=False,
    )


def _bench_aggregate(items: list[dict]) -> dict:
    import statistics

    def _pct(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = int(round((pct / 100.0) * (len(ordered) - 1)))
        return ordered[max(0, min(len(ordered) - 1, idx))]

    def _median(values: list[float]) -> float:
        return float(statistics.median(values)) if values else 0.0

    def _avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 1) if values else 0.0

    latencies = [float(item["response_time_ms"] or 0) for item in items]
    llm_calls = [float(item["llm_calls"] or 0) for item in items]
    tokens = [float(item["total_tokens"] or 0) for item in items]
    return {
        "latency_ms_p50": int(_median(latencies)),
        "latency_ms_p95": int(_pct(latencies, 95)),
        "latency_ms_max": int(max(latencies)) if latencies else 0,
        "llm_calls_p50": _median(llm_calls),
        "llm_calls_max": int(max(llm_calls)) if llm_calls else 0,
        "total_tokens_p50": int(_median(tokens)),
        "avg_input_tokens": _avg([float(item["input_tokens"] or 0) for item in items]),
        "avg_output_tokens": _avg([float(item["output_tokens"] or 0) for item in items]),
    }


@app.command("trace")
def trace(
    target: Annotated[str, typer.Argument(help="HermesRun run_id, or legacy ai_replies.id to inspect")],
    json_mode: bool = typer.Option(False, "--json", help="Output raw telemetry + reasoning as JSON"),
):
    """Show HermesRun telemetry, with legacy Seller Agent reply trace fallback."""
    asyncio.run(_trace_impl(target=target, json_mode=json_mode))


async def _trace_impl(*, target: str, json_mode: bool) -> None:  # noqa: C901
    _ensure_backend_path()

    try:
        from app.db.session import async_session
        from app.models.seller_agent_reply import SellerAgentReply
        from app.modules.hermes_runtime.service import HermesRunService
        from app.modules.hermes_runtime.trace_formatter import (
            build_hermes_run_trace_payload,
            format_hermes_run_trace_lines,
        )
        from sqlalchemy import select
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1) from e

    async with async_session() as db:
        run_service = HermesRunService(db)
        reply = None
        run = await run_service.get_by_run_id(target)
        if run is None and target.isdigit():
            reply = await db.scalar(
                select(SellerAgentReply).where(SellerAgentReply.id == int(target))
            )
            if reply is not None:
                run = await run_service.get_by_output_ref(f"seller_agent_reply:{reply.id}")
        if run is not None:
            events = await run_service.events_for_run(run.run_id)
            payload = build_hermes_run_trace_payload(run, events)
            if reply is None and run.output_ref and run.output_ref.startswith("seller_agent_reply:"):
                reply_id_part = run.output_ref.rsplit(":", 1)[-1]
                if reply_id_part.isdigit():
                    reply = await db.scalar(
                        select(SellerAgentReply).where(SellerAgentReply.id == int(reply_id_part))
                    )
            if reply is not None:
                payload["reply"] = {
                    "reply_id": reply.id,
                    "conversation_id": reply.conversation_id,
                    "status": reply.status,
                    "is_auto_sent": reply.is_auto_sent,
                    "draft_content": reply.draft_content,
                    "response_time_ms": reply.response_time_ms,
                }
            if json_mode:
                typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
                return

            header(f"HermesRun Trace — {payload['run_id']}")
            typer.echo("")
            for line in format_hermes_run_trace_lines(payload):
                typer.echo(f"  {line}")
            reply_payload = payload.get("reply")
            if isinstance(reply_payload, dict) and reply_payload.get("draft_content"):
                typer.echo("\n  reply:")
                for line in str(reply_payload["draft_content"]).splitlines():
                    typer.echo(f"    {line}")
            events_payload = payload.get("events") if isinstance(payload.get("events"), list) else []
            if events_payload:
                typer.echo("\n  events:")
                for event in events_payload:
                    if isinstance(event, dict):
                        typer.echo(f"    {event.get('sequence')}. {event.get('kind')}")
            return

        if not target.isdigit():
            typer.echo(f"  HermesRun {target} not found.")
            raise typer.Exit(1)
        reply = await db.scalar(
            select(SellerAgentReply).where(SellerAgentReply.id == int(target))
        )
        if reply is None:
            typer.echo(f"  Reply {target} not found.")
            raise typer.Exit(1)
        trace_data = reply.trace_data if isinstance(reply.trace_data, dict) else {}
        telemetry = trace_data.get("telemetry") if isinstance(trace_data, dict) else {}
        events = trace_data.get("events") if isinstance(trace_data, dict) else []
        payload = {
            "reply_id": reply.id,
            "conversation_id": reply.conversation_id,
            "agent_id": reply.agent_id,
            "status": reply.status,
            "confidence": reply.confidence_score,
            "response_time_ms": reply.response_time_ms,
            "draft_content": reply.draft_content,
            "telemetry": telemetry if isinstance(telemetry, dict) else {},
            "reasoning": _reasoning_from_events(events),
        }

    if json_mode:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    header(f"Reply Trace — id {payload['reply_id']}")
    typer.echo("")
    typer.echo(f"  conversation: {payload['conversation_id']}  agent: {payload['agent_id']}")
    typer.echo(f"  status:       {payload['status']}  confidence: {payload['confidence']}")
    typer.echo(f"  end-to-end:   {payload['response_time_ms']}ms")
    typer.echo("\n  reply:")
    for line in str(payload["draft_content"] or "").splitlines():
        typer.echo(f"    {line}")
    telemetry = payload["telemetry"]
    if isinstance(telemetry, dict) and telemetry:
        _print_trace_metrics(telemetry)
    reasoning = payload["reasoning"]
    if reasoning:
        typer.echo("\n  reasoning:")
        for step in reasoning:
            _print_reasoning_step(step)


def _reasoning_from_events(events: object) -> list[dict]:
    if not isinstance(events, list):
        return []
    for event in events:
        if (
            isinstance(event, dict)
            and event.get("stage") == "hermes"
            and event.get("event") == "loop"
        ):
            reasoning = event.get("reasoning")
            return reasoning if isinstance(reasoning, list) else []
    return []


def _print_reasoning_step(step: object) -> None:
    if not isinstance(step, dict):
        return
    role = step.get("role", "?")
    content = step.get("content")
    if content:
        typer.echo(f"    [{role}] {content}")
    for call in step.get("tool_calls") or []:
        if isinstance(call, dict):
            typer.echo(f"    [{role}] -> {call.get('name')}({call.get('arguments')})")


@app.command("google-auth")
def google_auth(
    validate: bool = typer.Option(False, "--validate", help="Refresh Google credentials to verify they are actually usable"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show which Google auth path OQIM will use for Gemini, embeddings, and Vertex."""
    asyncio.run(_google_auth_impl(validate=validate, json_mode=json_mode))


async def _google_auth_impl(*, validate: bool, json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from app.core.google_auth import resolve_google_auth, validate_google_auth
        from app.core.config import get_settings
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    settings = get_settings()
    resolution = resolve_google_auth(settings)
    status = resolution.status

    if validate:
        status = validate_google_auth(status, resolution.credentials)

    result = {
        "genai_mode": status.genai_mode,
        "vertex_mode": status.vertex_mode,
        "api_key_configured": status.api_key_configured,
        "credentials_path": status.credentials_path,
        "project": status.project,
        "location": status.location,
        "detail": status.detail,
        "validated": status.validated,
        "validation_error": status.validation_error,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    header("AI Google Auth")
    typer.echo("")
    typer.echo(f"  genai_mode:        {status.genai_mode}")
    typer.echo(f"  vertex_mode:       {status.vertex_mode}")
    typer.echo(f"  api_key_configured:{status.api_key_configured}")
    typer.echo(f"  project:           {status.project or '—'}")
    typer.echo(f"  location:          {status.location or '—'}")
    typer.echo(f"  credentials_path:  {status.credentials_path or '—'}")
    if status.detail:
        typer.echo(f"  detail:            {status.detail}")
    if validate:
        verdict = "ok" if status.validated else "failed"
        typer.echo(f"  validation:        {verdict}")
        if status.validation_error:
            typer.echo(f"  validation_error:  {status.validation_error}")


# ── sandbox ────────────────────────────────────────────────────────────────────


@sandbox_app.command("list")
def sandbox_list(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List synthetic sandbox conversations for a workspace."""
    asyncio.run(_sandbox_list_impl(workspace=workspace, json_mode=json_mode))


async def _sandbox_list_impl(workspace: int, json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.sandbox.service import list_sandbox_conversations
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    async with async_session() as db:
        ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)

        conversations = await list_sandbox_conversations(db, workspace_id=workspace)

    result = {
        "workspace": workspace,
        "count": len(conversations),
        "conversations": [
            {
                "conversation_id": item.conversation_id,
                "customer_id": item.customer_id,
                "customer_name": item.customer_name,
                "slug": item.slug,
                "external_chat_id": item.external_chat_id,
                "message_count": item.message_count,
                "latest_reply_status": item.latest_reply_status,
                "last_message_at": item.last_message_at.isoformat() if item.last_message_at else None,
            }
            for item in conversations
        ],
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    header(f"AI Sandbox — workspace {workspace}")
    typer.echo("")
    if not conversations:
        typer.echo("  No sandbox conversations yet.")
        typer.echo("  Create one with: oqim ai sandbox create \"Test Customer\"")
        return

    table(
        ["id", "slug", "customer", "messages", "latest_reply", "last_message_at"],
        [
            [
                item.conversation_id,
                item.slug,
                item.customer_name[:28],
                item.message_count,
                item.latest_reply_status or "—",
                item.last_message_at.isoformat()[:19] if item.last_message_at else "—",
            ]
            for item in conversations
        ],
        json_mode=False,
    )


@sandbox_app.command("create")
def sandbox_create(
    display_name: Annotated[str, typer.Argument(help="Sandbox customer display name")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    slug: str | None = typer.Option(None, "--slug", help="Stable sandbox slug (defaults to a slugified name)"),
    language: str = typer.Option("uz", "--language", help="Customer language tag"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Create or reuse a synthetic sandbox customer conversation inside a workspace."""
    asyncio.run(
        _sandbox_create_impl(
            display_name=display_name,
            workspace=workspace,
            slug=slug,
            language=language,
            json_mode=json_mode,
        )
    )


async def _sandbox_create_impl(
    *,
    display_name: str,
    workspace: int,
    slug: str | None,
    language: str,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.sandbox.service import ensure_sandbox_conversation
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    async with async_session() as db:
        ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)

        handle = await ensure_sandbox_conversation(
            db,
            workspace_id=workspace,
            display_name=display_name,
            slug=slug,
            language=language,
        )
        await db.commit()

    result = {
        "workspace": workspace,
        "conversation_id": handle.conversation.id,
        "customer_id": handle.customer.id,
        "customer_name": handle.customer.display_name,
        "slug": handle.slug,
        "channel": handle.conversation.channel,
        "external_chat_id": handle.conversation.external_chat_id,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    header(f"AI Sandbox Create — workspace {workspace}")
    typer.echo("")
    typer.echo(typer.style("  Sandbox conversation ready.", fg=typer.colors.GREEN))
    typer.echo(f"  conversation_id: {handle.conversation.id}")
    typer.echo(f"  customer_id:     {handle.customer.id}")
    typer.echo(f"  slug:            {handle.slug}")
    typer.echo(f"  external_chat_id:{handle.conversation.external_chat_id}")


@sandbox_app.command("send")
def sandbox_send(
    reference: Annotated[str, typer.Argument(help="Sandbox conversation id or slug")],
    messages: Annotated[list[str], typer.Argument(help="One or more customer messages to inject")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    delay: float = typer.Option(0.0, "--delay", help="Seconds to wait between injected messages"),
    wait: float = typer.Option(20.0, "--wait", help="Seconds to wait for a generated Seller Agent reply after the last message"),
    poll: float = typer.Option(1.0, "--poll", help="Polling interval while waiting for the reply"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Inject customer messages into a sandbox conversation and wait for the real Seller Agent reply."""
    asyncio.run(
        _sandbox_send_impl(
            reference=reference,
            messages=messages,
            workspace=workspace,
            delay=delay,
            wait=wait,
            poll=poll,
            json_mode=json_mode,
        )
    )


async def _sandbox_send_impl(
    *,
    reference: str,
    messages: list[str],
    workspace: int,
    delay: float,
    wait: float,
    poll: float,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.sandbox.service import (
            enqueue_sandbox_message,
            persist_sandbox_customer_message,
            resolve_sandbox_conversation,
            wait_for_sandbox_reply,
        )
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    if not messages:
        typer.echo("  Provide at least one message to send.")
        raise typer.Exit(1)

    start = time.monotonic()
    injected_rows: list[dict[str, object]] = []

    with _suppress_info_logs(json_mode):
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)

            handle = await resolve_sandbox_conversation(
                db,
                workspace_id=workspace,
                reference=reference,
            )

            for idx, content in enumerate(messages):
                injection = await persist_sandbox_customer_message(
                    db,
                    workspace_id=workspace,
                    conversation=handle.conversation,
                    customer=handle.customer,
                    content=content,
                )
                await db.commit()

                enqueue_result = await enqueue_sandbox_message(
                    workspace_id=workspace,
                    conversation_id=injection.conversation.id,
                    customer_id=injection.customer.id,
                    message_id=injection.message.id,
                    media_type=injection.message.media_type,
                )
                injected_rows.append(
                    {
                        "message_id": injection.message.id,
                        "content": injection.message.content,
                        "candidate_state": injection.candidate.state if injection.candidate else None,
                        "enqueue_result": enqueue_result,
                    }
                )

                if delay > 0 and idx < len(messages) - 1:
                    await asyncio.sleep(delay)

            reply_snapshot = None
            if wait > 0:
                reply_snapshot = await wait_for_sandbox_reply(
                    db,
                    conversation_id=handle.conversation.id,
                    trigger_message_id=int(injected_rows[-1]["message_id"]),
                    timeout_seconds=wait,
                    poll_interval=poll,
                )

    elapsed_ms = int((time.monotonic() - start) * 1000)
    reply_payload = (
        {
            "id": reply_snapshot.ai_reply.id,
            "status": reply_snapshot.ai_reply.status,
            "intent": reply_snapshot.ai_reply.intent,
            "confidence": reply_snapshot.ai_reply.confidence_score,
            "model_used": reply_snapshot.ai_reply.model_used,
            "content": reply_snapshot.ai_reply.draft_content,
        }
        if reply_snapshot and reply_snapshot.ai_reply is not None
        else None
    )
    result = {
        "workspace": workspace,
        "conversation_id": handle.conversation.id,
        "customer_id": handle.customer.id,
        "customer_name": handle.customer.display_name,
        "slug": handle.slug,
        "messages": injected_rows,
        "reply": reply_payload,
        "candidate_state": reply_snapshot.candidate_state if reply_snapshot else None,
        "candidate_reason": reply_snapshot.candidate_reason if reply_snapshot else None,
        "candidate_error": reply_snapshot.candidate_error if reply_snapshot else None,
        "latency_ms": elapsed_ms,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    header(f"AI Sandbox Send — workspace {workspace}")
    typer.echo(f"\n  target: {reference!r} -> conversation {handle.conversation.id} ({handle.slug})\n")
    for item in injected_rows:
        typer.echo(
            f"  injected: msg={item['message_id']}  enqueue={item['enqueue_result']}  "
            f"candidate={item['candidate_state']}  text={item['content']!r}"
        )

    if result["reply"] is None:
        typer.echo("")
        typer.echo(typer.style("  No Seller Agent reply arrived within the wait window.", fg=typer.colors.YELLOW))
        if result["candidate_state"]:
            typer.echo(f"  candidate_state:  {result['candidate_state']}")
        if result["candidate_reason"]:
            typer.echo(f"  candidate_reason: {result['candidate_reason']}")
        if result["candidate_error"]:
            typer.echo(f"  candidate_error:  {result['candidate_error']}")
    else:
        reply = result["reply"]
        typer.echo("")
        typer.echo(typer.style("  Reply received.", fg=typer.colors.GREEN))
        typer.echo(f"  reply_id:    {reply['id']}")
        typer.echo(f"  status:      {reply['status']}")
        typer.echo(f"  intent:      {reply['intent']}")
        typer.echo(f"  confidence:  {reply['confidence']}")
        typer.echo(f"  model_used:  {reply['model_used']}")
        typer.echo("\n  content:\n")
        for line in str(reply["content"]).splitlines():
            typer.echo(f"    {line}")

    typer.echo(f"\n  latency: {elapsed_ms}ms")


def _trace_summary_line(event: dict[str, object]) -> str:
    stage = str(event.get("stage", "trace"))
    name = str(event.get("event", "event"))
    if stage == "llm":
        if name == "attempt":
            request_summary = event.get("request_summary") if isinstance(event.get("request_summary"), dict) else {}
            prompt_chars = request_summary.get("prompt_chars")
            bits = [f"{event.get('provider')}/{event.get('model')}"]
            if event.get("operation"):
                bits.append(f"op={event.get('operation')}")
            if prompt_chars:
                bits.append(f"prompt={prompt_chars}ch")
            if event.get("fallback"):
                bits.append("fallback")
            return "  " + " | ".join([f"[{event.get('sequence')}] {stage}.{name}", *bits])
        op = event.get("operation")
        model = event.get("model")
        provider = event.get("provider")
        latency = event.get("latency_ms")
        error = event.get("error_type")
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        total_tokens = input_tokens + output_tokens
        bits = [f"{provider}/{model}"]
        if op:
            bits.append(f"op={op}")
        if latency is not None:
            bits.append(f"{latency}ms")
        if total_tokens:
            bits.append(f"{input_tokens}in/{output_tokens}out/{total_tokens}tok")
        if error:
            bits.append(f"error={error}")
        return "  " + " | ".join([f"[{event.get('sequence')}] {stage}.{name}", *bits])
    if stage in {"planner", "generation", "review", "rewrite", "chooser"} and name == "prompt":
        snapshot = event.get("prompt_snapshot") if isinstance(event.get("prompt_snapshot"), dict) else {}
        extra = snapshot.get("extra") if isinstance(snapshot.get("extra"), dict) else {}
        bits = []
        if extra.get("operation"):
            bits.append(f"op={extra.get('operation')}")
        if snapshot.get("prompt_chars"):
            bits.append(f"prompt={snapshot.get('prompt_chars')}ch")
        return "  " + " | ".join([f"[{event.get('sequence')}] {stage}.{name}", *bits])
    if stage == "planner" and event.get("plan"):
        plan = event["plan"]
        return (
            f"  [{event.get('sequence')}] planner.{name} | "
            f"reply={plan.get('should_reply')} | "
            f"intent={plan.get('detected_intent')} | "
            f"strategy={plan.get('response_strategy')} | "
            f"shape={plan.get('answer_shape')} | "
            f"catalog={plan.get('retrieve_catalog')} | "
            f"knowledge={plan.get('retrieve_knowledge')} | "
            f"history={plan.get('retrieve_history')}"
        )
    if stage == "retrieval" and name == "result":
        return (
            f"  [{event.get('sequence')}] retrieval.result | "
            f"catalog_items={event.get('catalog_items_found', 0)} | "
            f"knowledge_chunks={event.get('knowledge_chunks_found', 0)} | "
            f"history_orders={event.get('history_orders_found', 0)} | "
            f"history_conversations={event.get('history_conversations_found', 0)} | "
            f"style_examples={event.get('style_examples_count', 0)}"
        )
    if stage in {"generation", "rewrite"} and event.get("reply_text"):
        text = str(event["reply_text"]).replace("\n", " / ")
        return f"  [{event.get('sequence')}] {stage}.{name} | {text}"
    if stage == "review" and event.get("review"):
        review = event["review"]
        return (
            f"  [{event.get('sequence')}] review.{name} | "
            f"score={review.get('score')} | cap={review.get('confidence_cap')} | "
            f"issues={','.join(review.get('issue_codes') or ['none'])}"
        )
    if stage == "confidence":
        return (
            f"  [{event.get('sequence')}] confidence.{name} | "
            f"level={event.get('level')} | score={event.get('score')} | "
            f"reason={event.get('alignment_reason') or 'none'}"
        )
    if stage == "chooser" and event.get("decision"):
        decision = event["decision"]
        return (
            f"  [{event.get('sequence')}] chooser.{name} | "
            f"selection={decision.get('selection')} | reasoning={decision.get('reasoning')}"
        )
    if stage == "draft" and name == "persisted":
        return (
            f"  [{event.get('sequence')}] reply.persisted | "
            f"ai_reply_id={event.get('ai_reply_id')} | "
            f"status={event.get('status')} | confidence={event.get('confidence_score')} | "
            f"quality={event.get('quality_score')} | blocked={event.get('quality_blocked')} | "
            f"chooser={event.get('chooser_selection')}"
        )
    return f"  [{event.get('sequence')}] {stage}.{name}"


def _trace_detail_lines(event: dict[str, object], *, verbose: bool) -> list[str]:
    if not verbose:
        return []

    stage = str(event.get("stage", "trace"))
    name = str(event.get("event", "event"))
    lines: list[str] = []

    def _add_block(label: str, value: object, *, limit: int = 400) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if len(text) > limit:
            text = text[: limit - 3].rstrip() + "..."
        for idx, part in enumerate(text.splitlines() or [text]):
            prefix = f"      {label}: " if idx == 0 else " " * (len(label) + 8)
            lines.append(prefix + part)

    if stage == "planner" and name == "result":
        plan = event.get("plan")
        if isinstance(plan, dict):
            _add_block("reply", plan.get("should_reply"))
            _add_block("intent", plan.get("detected_intent"))
            _add_block("goal", plan.get("customer_goal"))
            _add_block("missing", plan.get("missing_information"))
            _add_block("reasoning", plan.get("reasoning"))
        _add_block("raw", event.get("raw_text"))
    elif stage == "review" and name in {"result", "fallback"}:
        review = event.get("review")
        if isinstance(review, dict):
            _add_block("reasoning", review.get("reasoning"))
        _add_block("raw", event.get("raw_text"))
    elif stage in {"planner", "generation", "review", "rewrite", "chooser"} and name == "prompt":
        snapshot = event.get("prompt_snapshot")
        if isinstance(snapshot, dict):
            extra = snapshot.get("extra")
            if isinstance(extra, dict):
                _add_block("operation", extra.get("operation"))
                for key, value in extra.items():
                    if key == "operation":
                        continue
                    _add_block(key, value)
            _add_block("system", snapshot.get("system_instruction_preview"), limit=500)
            sections = snapshot.get("sections")
            if isinstance(sections, dict):
                for label, value in sections.items():
                    _add_block(label, value, limit=360)
            _add_block("prompt", snapshot.get("prompt_preview"), limit=700)
        if stage == "generation" and isinstance(event.get("evidence_snapshot"), dict):
            evidence = event["evidence_snapshot"]
            _add_block("rag_query", evidence.get("query"))
            catalog_preview = evidence.get("catalog_preview")
            if isinstance(catalog_preview, list) and catalog_preview:
                _add_block("catalog", catalog_preview)
            knowledge_preview = evidence.get("knowledge_preview")
            if isinstance(knowledge_preview, list) and knowledge_preview:
                _add_block("knowledge", knowledge_preview)
            history_preview = evidence.get("history_preview")
            if isinstance(history_preview, dict):
                _add_block("history", history_preview)
            style_preview = evidence.get("style_examples_preview")
            if isinstance(style_preview, list) and style_preview:
                _add_block("style", style_preview)
    elif stage == "retrieval" and name == "result":
        evidence = event.get("evidence_snapshot")
        if isinstance(evidence, dict):
            _add_block("query", evidence.get("query"))
            _add_block("catalog_query", evidence.get("catalog_query"))
            _add_block("knowledge_query", evidence.get("knowledge_query"))
            _add_block("history_query", evidence.get("history_query"))
            catalog_preview = evidence.get("catalog_preview")
            if isinstance(catalog_preview, list) and catalog_preview:
                _add_block("catalog", catalog_preview)
            knowledge_preview = evidence.get("knowledge_preview")
            if isinstance(knowledge_preview, list) and knowledge_preview:
                _add_block("knowledge", knowledge_preview)
            history_preview = evidence.get("history_preview")
            if isinstance(history_preview, dict):
                _add_block("history", history_preview)
            style_preview = evidence.get("style_examples_preview")
            if isinstance(style_preview, list) and style_preview:
                _add_block("style", style_preview)
    elif stage == "confidence" and name == "selected":
        _add_block("reason", event.get("alignment_reason"))
    elif stage == "chooser" and name in {"result", "fallback"}:
        decision = event.get("decision")
        if isinstance(decision, dict):
            _add_block("reasoning", decision.get("reasoning"))
        _add_block("raw", event.get("raw_text"))
    elif stage == "llm" and name == "attempt":
        request_summary = event.get("request_summary")
        if isinstance(request_summary, dict):
            config = request_summary.get("config")
            if isinstance(config, dict):
                _add_block("config", config)
            _add_block("system", request_summary.get("system_instruction_preview"), limit=500)
            _add_block("prompt", request_summary.get("prompt_preview"), limit=700)
    elif stage in {"generation", "rewrite"} and name == "result":
        raw_text = str(event.get("raw_text") or "").strip()
        reply_text = str(event.get("reply_text") or "").strip()
        if raw_text and raw_text != reply_text:
            _add_block("raw", raw_text)
            _add_block("cleaned", reply_text)

    return lines


def _summarize_trace_metrics(events: list[dict[str, object]]) -> dict[str, object]:
    llm_calls: list[dict[str, object]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency_ms = 0

    for event in events:
        if event.get("stage") != "llm" or event.get("event") != "success":
            continue
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        latency_ms = int(event.get("latency_ms", 0) or 0)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_latency_ms += latency_ms
        llm_calls.append(
            {
                "sequence": event.get("sequence"),
                "operation": event.get("operation") or "unknown",
                "provider": event.get("provider") or "unknown",
                "model": event.get("model") or "unknown",
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "fallback": bool(event.get("fallback")),
            }
        )

    return {
        "llm_calls": len(llm_calls),
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "llm_latency_ms": total_latency_ms,
        "calls": llm_calls,
    }


def _print_trace_metrics(metrics: dict[str, object]) -> None:
    typer.echo("")
    typer.echo("  Trace summary:")
    typer.echo(
        "    "
        f"llm_calls={metrics['llm_calls']}  "
        f"tokens={metrics['input_tokens']}in/{metrics['output_tokens']}out/{metrics['total_tokens']}total  "
        f"llm_latency={metrics['llm_latency_ms']}ms"
    )
    calls = metrics.get("calls")
    if not isinstance(calls, list) or not calls:
        return
    typer.echo("")
    table(
        ["seq", "operation", "model", "latency_ms", "tokens"],
        [
            [
                call.get("sequence"),
                call.get("operation"),
                str(call.get("model"))[:28],
                call.get("latency_ms"),
                f"{call.get('input_tokens')}in/{call.get('output_tokens')}out",
            ]
            for call in calls
        ],
        json_mode=False,
    )


def _sandbox_default_display_name(reference: str) -> str:
    cleaned = reference.replace("-", " ").replace("_", " ").strip()
    return cleaned.title() if cleaned else "Sandbox Customer"


async def _resolve_or_create_sandbox_handle(
    *,
    db,
    workspace: int,
    reference: str,
    display_name: str | None,
    language: str,
):
    from app.modules.sandbox.service import (
        ensure_sandbox_conversation,
        resolve_sandbox_conversation,
    )

    try:
        return await resolve_sandbox_conversation(
            db,
            workspace_id=workspace,
            reference=reference,
        ), False
    except ValueError:
        if reference.isdigit():
            raise
        handle = await ensure_sandbox_conversation(
            db,
            workspace_id=workspace,
            display_name=(display_name or _sandbox_default_display_name(reference)),
            slug=reference,
            language=language,
        )
        return handle, True


@sandbox_app.command("seller")
def sandbox_seller(
    reference: Annotated[str, typer.Argument(help="Sandbox conversation id or slug")],
    message: Annotated[str, typer.Argument(help="Seller message to inject")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    ai_reply_id: int | None = typer.Option(None, "--ai-reply-id", help="Optional linked AI reply id"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Inject a seller message into a sandbox conversation and apply suppression/follow-up logic."""
    asyncio.run(
        _sandbox_seller_impl(
            reference=reference,
            message=message,
            workspace=workspace,
            ai_reply_id=ai_reply_id,
            json_mode=json_mode,
        )
    )


async def _sandbox_seller_impl(
    *,
    reference: str,
    message: str,
    workspace: int,
    ai_reply_id: int | None,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.sandbox.service import (
            persist_sandbox_seller_message,
            record_sandbox_seller_reply,
            resolve_sandbox_conversation,
        )
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    async with async_session() as db:
        ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)

        handle = await resolve_sandbox_conversation(
            db,
            workspace_id=workspace,
            reference=reference,
        )
        injection = await persist_sandbox_seller_message(
            db,
            workspace_id=workspace,
            conversation=handle.conversation,
            customer=handle.customer,
            content=message,
            ai_reply_id=ai_reply_id,
        )
        await db.commit()
        await record_sandbox_seller_reply(
            workspace_id=workspace,
            conversation_id=handle.conversation.id,
            message_id=injection.message.id,
        )

    result = {
        "workspace": workspace,
        "conversation_id": handle.conversation.id,
        "customer_id": handle.customer.id,
        "customer_name": handle.customer.display_name,
        "slug": handle.slug,
        "message_id": injection.message.id,
        "content": injection.message.content,
        "candidate_state": injection.candidate.state if injection.candidate else None,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    header(f"AI Sandbox Seller — workspace {workspace}")
    typer.echo("")
    typer.echo(typer.style("  Seller message recorded.", fg=typer.colors.GREEN))
    typer.echo(f"  conversation_id: {handle.conversation.id}")
    typer.echo(f"  message_id:      {injection.message.id}")
    typer.echo(f"  candidate_state: {result['candidate_state'] or '—'}")
    typer.echo(f"  content:         {injection.message.content}")


@sandbox_app.command("trace")
def sandbox_trace(
    reference: Annotated[str, typer.Argument(help="Sandbox conversation id or slug")],
    messages: Annotated[list[str], typer.Argument(help="One or more customer messages to inject before tracing")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    delay: float = typer.Option(0.0, "--delay", help="Seconds to wait between injected messages"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show expanded planner/review/raw step output"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run Seller Agent synchronously for a sandbox conversation."""
    asyncio.run(
        _sandbox_trace_impl(
            reference=reference,
            messages=messages,
            workspace=workspace,
            delay=delay,
            verbose=verbose,
            json_mode=json_mode,
        )
    )


async def _sandbox_trace_impl(
    *,
    reference: str,
    messages: list[str],
    workspace: int,
    delay: float,
    verbose: bool,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.action_runtime.seller_agent_runtime.bridge import (
            generate_seller_agent_reply,
        )
        from app.modules.sandbox.service import persist_sandbox_customer_message
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    if not messages:
        typer.echo("  Provide at least one message to trace.")
        raise typer.Exit(1)

    start = time.monotonic()
    trace_events: list[dict[str, object]] = []

    with _suppress_info_logs(json_mode):
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)

            handle, created = await _resolve_or_create_sandbox_handle(
                db=db,
                workspace=workspace,
                reference=reference,
                display_name=None,
                language="uz",
            )
            await db.commit()

            injection = None
            for idx, content in enumerate(messages):
                injection = await persist_sandbox_customer_message(
                    db,
                    workspace_id=workspace,
                    conversation=handle.conversation,
                    customer=handle.customer,
                    content=content,
                )
                await db.commit()
                if delay > 0 and idx < len(messages) - 1:
                    await asyncio.sleep(delay)

            if not json_mode:
                header(f"AI Sandbox Trace — workspace {workspace}")
                action = "created" if created else "using"
                typer.echo(
                    f"\n  target: {reference!r} -> conversation {handle.conversation.id} ({handle.slug}) [{action}]\n"
                )

            ai_reply = await generate_seller_agent_reply(
                conversation_id=handle.conversation.id,
                trigger_message_id=injection.message.id,
                db=db,
            )
            if ai_reply is not None:
                debug = getattr(ai_reply, "_debug_trace", None)
                if isinstance(debug, dict):
                    trace_events.append(debug)
                    if not json_mode and verbose:
                        typer.echo(json.dumps(debug, indent=2, ensure_ascii=False, default=str))

    elapsed_ms = int((time.monotonic() - start) * 1000)
    metrics = _summarize_trace_metrics(trace_events)
    reply_payload = (
        {
            "id": ai_reply.id,
            "status": ai_reply.status,
            "intent": ai_reply.intent,
            "confidence": ai_reply.confidence_score,
            "model_used": ai_reply.model_used,
            "content": ai_reply.draft_content,
            "debug_trace": getattr(ai_reply, "_debug_trace", None),
        }
        if ai_reply is not None
        else None
    )
    result = {
        "workspace": workspace,
        "conversation_id": handle.conversation.id,
        "customer_id": handle.customer.id,
        "customer_name": handle.customer.display_name,
        "slug": handle.slug,
        "created": created,
        "trigger_message_id": injection.message.id,
        "reply": reply_payload,
        "trace_events": trace_events,
        "metrics": metrics,
        "latency_ms": elapsed_ms,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    typer.echo("")
    if ai_reply is None:
        typer.echo(typer.style("  Reply skipped by Seller Agent.", fg=typer.colors.YELLOW))
    else:
        typer.echo(typer.style("  Reply created.", fg=typer.colors.GREEN))
        typer.echo(f"  reply_id:    {ai_reply.id}")
        typer.echo(f"  status:      {ai_reply.status}")
        typer.echo(f"  intent:      {ai_reply.intent}")
        typer.echo(f"  confidence:  {ai_reply.confidence_score}")
        typer.echo(f"  model_used:  {ai_reply.model_used}")
        typer.echo("\n  content:\n")
        for line in str(ai_reply.draft_content).splitlines():
            typer.echo(f"    {line}")

    _print_trace_metrics(metrics)
    typer.echo(f"\n  latency: {elapsed_ms}ms")


@sandbox_app.command("smoke")
def sandbox_smoke(
    reference: Annotated[str, typer.Argument(help="Sandbox slug or conversation id; creates the slug automatically if missing")],
    messages: Annotated[list[str], typer.Argument(help="One or more customer messages to test")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    name: str | None = typer.Option(None, "--name", help="Display name if a new sandbox customer is created"),
    language: str = typer.Option("uz", "--language", help="Customer language tag"),
    delay: float = typer.Option(0.0, "--delay", help="Seconds between injected customer messages"),
    wait: float = typer.Option(20.0, "--wait", help="Seconds to wait for a queue-produced Seller Agent reply"),
    poll: float = typer.Option(1.0, "--poll", help="Polling interval while waiting"),
    trace: bool = typer.Option(False, "--trace", help="Run the synchronous traced path instead of queue wait"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show expanded planner/review/raw trace details"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Create-or-reuse a sandbox buyer and run a full Seller Agent smoke test."""
    asyncio.run(
        _sandbox_smoke_impl(
            reference=reference,
            messages=messages,
            workspace=workspace,
            name=name,
            language=language,
            delay=delay,
            wait=wait,
            poll=poll,
            trace=trace,
            verbose=verbose,
            json_mode=json_mode,
        )
    )


async def _sandbox_smoke_impl(
    *,
    reference: str,
    messages: list[str],
    workspace: int,
    name: str | None,
    language: str,
    delay: float,
    wait: float,
    poll: float,
    trace: bool,
    verbose: bool,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    async with async_session() as db:
        ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)

        handle, created = await _resolve_or_create_sandbox_handle(
            db=db,
            workspace=workspace,
            reference=reference,
            display_name=name,
            language=language,
        )
        await db.commit()

    if not json_mode:
        header(f"AI Sandbox Smoke — workspace {workspace}")
        typer.echo("")
        action = "Created" if created else "Using"
        typer.echo(f"  {action} sandbox customer: {handle.customer.display_name} ({handle.slug})")
        typer.echo(f"  conversation_id: {handle.conversation.id}")
        typer.echo(f"  mode: {'trace' if trace else 'queue'}")
        typer.echo("")

    if trace:
        await _sandbox_trace_impl(
            reference=handle.slug,
            messages=messages,
            workspace=workspace,
            delay=delay,
            verbose=verbose,
            json_mode=json_mode,
        )
        return

    await _sandbox_send_impl(
        reference=handle.slug,
        messages=messages,
        workspace=workspace,
        delay=delay,
        wait=wait,
        poll=poll,
        json_mode=json_mode,
    )

    if not json_mode:
        typer.echo("")
        typer.echo("  Deep trace for the same sandbox buyer:")
        typer.echo(f"    oqim ai sandbox trace {handle.slug} {' '.join(repr(m) for m in messages)} -w {workspace} -v")


@sandbox_app.command("suite")
def sandbox_suite(
    reference_prefix: Annotated[str, typer.Argument(help="Base slug prefix for the generated sandbox buyers")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run a small canned sandbox suite against the current Seller Agent runtime."""
    asyncio.run(
        _sandbox_suite_impl(
            reference_prefix=reference_prefix,
            workspace=workspace,
            json_mode=json_mode,
        )
    )


async def _sandbox_suite_impl(
    *,
    reference_prefix: str,
    workspace: int,
    json_mode: bool,
) -> None:
    reference_prefix = normalize_suite_slug(reference_prefix)
    cases = [
        {
            "name": "fragmented_turn_queue",
            "description": "Course buyer sends a fragmented vague ask; queue path should collapse it into one reply.",
            "slug": f"{reference_prefix}-fragmented",
            "messages": [
                "Assalomu alaykum",
                "qalesiz",
                "kechki ingliz tili kursi bormi, ishda o'qiyman",
            ],
            "mode": "queue",
            "delay": 1.0,
            "wait": 35.0,
        },
        {
            "name": "vague_listing_trace",
            "description": "Trace planner/generation for a vague real-estate ask that should clarify, not stall.",
            "slug": f"{reference_prefix}-vague",
            "messages": [
                "oilaga mos 2 xonali uy kerak, qaysi hududlarda bor?",
            ],
            "mode": "trace",
            "delay": 0.0,
            "verbose": True,
        },
        {
            "name": "regulated_offer_trace",
            "description": "Trace a medicine-store inquiry and inspect grounding, safety, tokens, and review.",
            "slug": f"{reference_prefix}-price",
            "messages": [
                "bolalar uchun D vitamini tomchisi bormi, narxi qancha?",
            ],
            "mode": "trace",
            "delay": 0.0,
            "verbose": True,
        },
    ]

    if json_mode:
        typer.echo(
            json.dumps(
                {
                    "workspace": workspace,
                    "reference_prefix": reference_prefix,
                    "cases": cases,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    header(f"AI Sandbox Suite — workspace {workspace}")
    typer.echo("")
    typer.echo(f"  Base slug prefix: {reference_prefix}")
    typer.echo("  This runs a few high-signal Seller Agent checks using separate sandbox buyers.")

    for index, case in enumerate(cases, start=1):
        typer.echo("")
        typer.echo(f"  {index}. {case['name']}")
        typer.echo(f"     {case['description']}")
        typer.echo(f"     slug: {case['slug']}")
        typer.echo("")
        if case["mode"] == "queue":
            await _sandbox_smoke_impl(
                reference=case["slug"],
                messages=list(case["messages"]),
                workspace=workspace,
                name=None,
                language="uz",
                delay=float(case["delay"]),
                wait=float(case["wait"]),
                poll=1.0,
                trace=False,
                verbose=False,
                json_mode=False,
            )
        else:
            await _sandbox_smoke_impl(
                reference=case["slug"],
                messages=list(case["messages"]),
                workspace=workspace,
                name=None,
                language="uz",
                delay=float(case["delay"]),
                wait=20.0,
                poll=1.0,
                trace=True,
                verbose=bool(case.get("verbose", False)),
                json_mode=False,
            )

    typer.echo("")
    typer.echo("  Manual next step for suppression/follow-up:")
    typer.echo(f"    oqim ai sandbox seller {reference_prefix}-fragmented \"Bor, qaysi model kerak?\" -w {workspace}")
    typer.echo(f"    oqim ai sandbox trace {reference_prefix}-fragmented \"narxi qancha bo'ladi?\" -w {workspace} -v")


def normalize_suite_slug(value: str) -> str:
    cleaned = (value or "").strip().lower().replace(" ", "-").replace("_", "-")
    return cleaned.strip("-") or "sandbox-suite"


# ── search ─────────────────────────────────────────────────────────────────────


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query to run against Business Brain")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    fact_type: Optional[list[str]] = typer.Option(
        None,
        "--fact-type",
        help="Restrict retrieval to one fact type. Repeat for multiple.",
    ),
    modality: Optional[list[str]] = typer.Option(
        None,
        "--modality",
        help="Add query modality: text, image, audio, video, pdf, or file.",
    ),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50, help="Max results"),
    semantic: bool = typer.Option(True, "--semantic/--no-semantic", help="Use query embeddings when available"),
    rewrite: bool = typer.Option(False, "--rewrite", help="Use LLM query rewrite"),
    agentic: bool = typer.Option(False, "--agentic", help="Use LLM agentic retrieval planning"),
    rerank: bool = typer.Option(False, "--rerank", help="Use reranker after recall"),
    include_source_units: bool = typer.Option(False, "--source-units", help="Include source unit snippets in JSON output"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run shared Retrieval Core search over Business Brain evidence."""
    asyncio.run(
        _search_impl(
            query=query,
            workspace=workspace,
            fact_types=list(fact_type or []),
            modalities=list(modality or []),
            limit=limit,
            semantic=semantic,
            rewrite=rewrite,
            agentic=agentic,
            rerank=rerank,
            include_source_units=include_source_units,
            json_mode=json_mode,
        )
    )


async def _search_impl(
    query: str,
    workspace: int,
    fact_types: list[str],
    modalities: list[str],
    limit: int,
    semantic: bool,
    rewrite: bool,
    agentic: bool,
    rerank: bool,
    include_source_units: bool,
    json_mode: bool,
) -> None:
    _ensure_backend_path()

    try:
        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.commercial_spine.repository import CommercialSpineRepository
        from app.modules.retrieval_core.contracts import RetrievalContextRequest
        from app.modules.retrieval_core.service import RetrievalCoreService
        from sqlalchemy import select
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    if not json_mode:
        header(f"AI Retrieval Core Search — workspace {workspace}")
        typer.echo(f"\n  query: {query!r}\n")

    start = time.monotonic()

    with _suppress_info_logs(json_mode):
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)
            retrieval = await RetrievalCoreService(
                repository=CommercialSpineRepository(db),
            ).retrieve_contextual(
                RetrievalContextRequest(
                    workspace_id=workspace,
                    requested_fact_types=fact_types,
                    query_text=query,
                    query_modalities=modalities,  # type: ignore[arg-type]
                    enable_semantic=semantic,
                    enable_query_rewrite=rewrite,
                    enable_agentic_search=agentic,
                    enable_rerank=rerank,
                    include_source_units=include_source_units,
                    limit=limit,
                )
            )

    elapsed_ms = int((time.monotonic() - start) * 1000)

    items = [
        _retrieval_item(candidate.model_dump(mode="json"), include_source_units=include_source_units)
        for candidate in retrieval.candidates
    ]

    result = {
        "workspace": workspace,
        "query": query,
        "count": len(items),
        "latency_ms": elapsed_ms,
        "degraded_reasons": list(retrieval.degraded_reasons),
        "trace": retrieval.trace.model_dump(mode="json"),
        "items": items,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    if not items:
        typer.echo("  No Business Brain evidence matched.")
        typer.echo("  Run onboarding/source learning, or broaden --fact-type / --modality.")
    else:
        if retrieval.degraded_reasons:
            typer.echo(typer.style(f"  degraded: {', '.join(retrieval.degraded_reasons)}", fg=typer.colors.YELLOW))
        headers = ["fact_id", "type", "title", "score", "channels"]
        table_rows = [
            [
                str(i["fact_id"])[:42],
                str(i["fact_type"])[:24],
                str(i["title"])[:42],
                i["score"],
                ", ".join(dict(i["retrieval_scores"]).keys()) or "—",
            ]
            for i in items
        ]
        table(headers, table_rows, json_mode=False)

    typer.echo(f"\n  channels: {', '.join(retrieval.trace.retrieval_channels) or 'none'}")
    if retrieval.trace.query_rewrites:
        typer.echo(f"  rewrites: {', '.join(retrieval.trace.query_rewrites)}")
    if retrieval.trace.agentic_queries:
        typer.echo(f"  agentic:  {', '.join(retrieval.trace.agentic_queries)}")
    typer.echo(f"  {len(items)} results  |  latency: {elapsed_ms}ms")


def _retrieval_item(candidate: dict[str, object], *, include_source_units: bool) -> dict[str, object]:
    scores = {
        str(key): round(float(value), 4)
        for key, value in dict(candidate.get("retrieval_scores") or {}).items()
    }
    return {
        "fact_id": str(candidate.get("fact_id") or ""),
        "fact_type": str(candidate.get("fact_type") or ""),
        "entity_ref": str(candidate.get("entity_ref") or ""),
        "title": _candidate_title(dict(candidate.get("value") or {})),
        "status": str(candidate.get("status") or ""),
        "confidence": candidate.get("confidence"),
        "score": round(sum(scores.values()), 4),
        "retrieval_scores": scores,
        "source_refs": list(candidate.get("source_refs") or []),
        "contextual_text": candidate.get("contextual_text"),
        "source_units": list(candidate.get("source_units") or []) if include_source_units else [],
    }


def _candidate_title(value: dict[str, object]) -> str:
    for key in ("title", "name", "question", "label", "trait", "rule"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    product = value.get("product")
    if isinstance(product, dict):
        raw = product.get("title") or product.get("name")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "—"


# ── voice ──────────────────────────────────────────────────────────────────────


@app.command()
def voice(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    regenerate: bool = typer.Option(False, "--regenerate", help="Regenerate voice profile from Telegram history"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show (or regenerate) the current voice profile for a workspace."""
    asyncio.run(_voice_impl(workspace=workspace, regenerate=regenerate, json_mode=json_mode))


async def _voice_impl(workspace: int, regenerate: bool, json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.business_brain.voice_learning import BusinessVoiceLearningService
        from app.modules.commercial_spine.repository import CommercialSpineRepository
        from sqlalchemy import select
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    if not json_mode:
        header(f"Business Brain Voice — workspace {workspace}")

    started = time.monotonic()
    snapshot = None
    async with async_session() as db:
        ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        repository = CommercialSpineRepository(db)
        if regenerate:
            if not json_mode:
                typer.echo("\n  Regenerating from seller history through Business Brain voice learning...")
            try:
                snapshot = await BusinessVoiceLearningService(
                    repository=repository,
                ).learn_from_history(
                    workspace_id=workspace,
                    correlation_id=f"cli:voice:{workspace}",
                    idempotency_key=f"cli:voice:{workspace}",
                )
                await db.commit()
            except Exception as e:
                typer.echo(f"  voice learning failed: {type(e).__name__}: {e}")
                raise typer.Exit(1)
            projection = snapshot.projection
        else:
            projection = await repository.get_projection(
                workspace_id=workspace,
                projection_ref="voice_profile:seller_voice",
            )
        facts = await repository.list_facts(
            workspace_id=workspace,
            entity_ref="seller_voice",
            fact_type="voice_fact",
            limit=250,
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    traits = []
    excluded_fact_ids = []
    source_refs = []
    degraded_reasons = []
    if projection is not None:
        traits = list(projection.state.get("traits") or [])
        excluded_fact_ids = list(projection.state.get("excluded_fact_ids") or [])
        source_refs = list(projection.source_refs)
        degraded_reasons = list(projection.degraded_reasons)
    if snapshot is not None:
        degraded_reasons = sorted({*degraded_reasons, *snapshot.degraded_reasons})

    result = {
        "workspace": workspace,
        "quality_score": snapshot.quality_score if snapshot is not None else ("ready" if traits else "missing"),
        "message_count_analyzed": snapshot.message_count_analyzed if snapshot is not None else None,
        "accepted_observations": snapshot.accepted_observations if snapshot is not None else len(traits),
        "voice_card": snapshot.voice_card if snapshot is not None else _voice_card_from_traits(traits),
        "message_pattern": snapshot.message_pattern if snapshot is not None else _message_pattern_from_traits(traits),
        "traits": traits,
        "fact_count": len(facts),
        "excluded_fact_ids": excluded_fact_ids,
        "source_refs": source_refs,
        "degraded_reasons": degraded_reasons,
        "latency_ms": elapsed_ms,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    typer.echo("")
    rows = [
        ["quality_score", result["quality_score"]],
        ["messages_analyzed", result["message_count_analyzed"] or "—"],
        ["accepted_observations", result["accepted_observations"]],
        ["message_pattern", result["message_pattern"]],
        ["voice_facts", result["fact_count"]],
        ["excluded_fact_ids", len(excluded_fact_ids)],
        ["source_refs", len(source_refs)],
        ["latency_ms", elapsed_ms],
    ]

    vc = dict(result["voice_card"] or {})
    simple_vc_keys = ["primary_language", "script", "tone", "style", "formality", "warmth", "brevity"]
    for key in simple_vc_keys:
        if key in vc:
            rows.append([f"vc.{key}", vc[key]])

    table(["field", "value"], rows, json_mode=False)

    if degraded_reasons:
        typer.echo(typer.style(f"\n  degraded: {', '.join(degraded_reasons)}", fg=typer.colors.YELLOW))
    if not traits:
        typer.echo("\n  No active Business Brain voice traits yet.")
        typer.echo("  Run onboarding voice learning or `oqim ai voice --regenerate` after seller history exists.")


def _voice_card_from_traits(traits: list[object]) -> dict[str, object]:
    if not traits or not isinstance(traits[-1], dict):
        return {}
    latest = traits[-1]
    return {
        "primary_language": latest.get("primary_language")
        or latest.get("language")
        or latest.get("language_mix"),
        "script": latest.get("script") or latest.get("writing_script"),
    }


def _message_pattern_from_traits(traits: list[object]) -> str:
    if not traits or not isinstance(traits[-1], dict):
        return "unknown"
    latest = traits[-1]
    return str(
        latest.get("message_pattern")
        or latest.get("selling_rhythm")
        or latest.get("tone")
        or "learned"
    )


# ── style ─────────────────────────────────────────────────────────────────────


@app.command()
def style(
    message: Annotated[str, typer.Argument(help="Customer message to find style examples for")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Retrieve Business Brain voice and conversation examples for a message."""
    asyncio.run(_style_impl(message, workspace, json_mode))


async def _style_impl(message: str, workspace: int, json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.commercial_spine.repository import CommercialSpineRepository
        from app.modules.retrieval_core.contracts import RetrievalContextRequest
        from app.modules.retrieval_core.service import RetrievalCoreService
        from sqlalchemy import select
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    if not json_mode:
        header(f"Business Brain Style Retrieval — workspace {workspace}")
        typer.echo(f"\n  message: {message!r}\n")

    start = time.monotonic()

    with _suppress_info_logs(json_mode):
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)
            retrieval = await RetrievalCoreService(
                repository=CommercialSpineRepository(db),
            ).retrieve_contextual(
                RetrievalContextRequest(
                    workspace_id=workspace,
                    requested_fact_types=["voice_fact", "conversation_pair_fact"],
                    requested_slots=["voice_fact", "conversation_pair_fact"],
                    query_text=message,
                    enable_semantic=True,
                    enable_query_rewrite=True,
                    enable_rerank=True,
                    limit=5,
                )
            )

    elapsed_ms = int((time.monotonic() - start) * 1000)
    items = [
        _retrieval_item(candidate.model_dump(mode="json"), include_source_units=False)
        for candidate in retrieval.candidates
    ]

    if json_mode:
        result = {
            "workspace": workspace,
            "message": message,
            "items": items,
            "degraded_reasons": list(retrieval.degraded_reasons),
            "trace": retrieval.trace.model_dump(mode="json"),
            "latency_ms": elapsed_ms,
        }
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    if not items:
        typer.echo(typer.style("  No Business Brain style evidence found.", fg=typer.colors.YELLOW))
    else:
        for item in items:
            typer.echo(f"  {item['fact_id']} [{item['fact_type']}] score={item['score']}")
            text = str(item.get("contextual_text") or "")
            if text:
                typer.echo(f"    {text[:220]}")

    typer.echo(f"\n  latency: {elapsed_ms}ms")


# ── prepass ────────────────────────────────────────────────────────────────────


@app.command()
def prepass(
    message: Annotated[str, typer.Argument(help="Message to run the pre-pass classifier on")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID (unused, for API consistency)"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run the Flash-Lite pre-pass classifier and show intent, should_reply, and language."""
    asyncio.run(_prepass_impl(message=message, json_mode=json_mode))


async def _prepass_impl(message: str, json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from app.brain.pre_pass import run_pre_pass
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    if not json_mode:
        header("AI Pre-Pass")
        typer.echo(f"\n  message: {message!r}\n")

    start = time.monotonic()

    try:
        result_obj = await run_pre_pass(
            message_text=message,
            conversation_context="",
            contact_type="unknown",
        )
    except Exception as e:
        typer.echo(f"  run_pre_pass failed: {type(e).__name__}: {e}")
        raise typer.Exit(1)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    result = {
        "message": message,
        "should_reply": result_obj.should_reply,
        "intent": result_obj.intent,
        "urgency": result_obj.urgency,
        "language": result_obj.language,
        "is_voice_message": result_obj.is_voice_message,
        "latency_ms": elapsed_ms,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    color = typer.colors.GREEN if result_obj.should_reply else typer.colors.YELLOW
    reply_label = "REPLY" if result_obj.should_reply else "SKIP"
    typer.echo(typer.style(f"  {reply_label}", fg=color, bold=True))
    typer.echo(f"  intent:   {result_obj.intent}")
    typer.echo(f"  urgency:  {result_obj.urgency}")
    typer.echo(f"  language: {result_obj.language}")
    typer.echo(f"  latency:  {elapsed_ms}ms")


# ── classify ───────────────────────────────────────────────────────────────────


@app.command()
def classify(
    name: Annotated[str, typer.Argument(help="Contact display name to classify")],
    messages: Optional[list[str]] = typer.Option(
        None,
        "--messages",
        help="Sample messages from the contact (repeat flag for multiple)",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Classify a contact by name (and optional messages) — read-only, no DB write."""
    asyncio.run(_classify_impl(name=name, messages=messages or [], json_mode=json_mode))


async def _classify_impl(name: str, messages: list[str], json_mode: bool) -> None:
    _ensure_backend_path()

    try:
        from app.services.contact_classifier import classify_contacts_batch_v2
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        typer.echo("  Make sure you're running from the backend venv.")
        raise typer.Exit(1)

    if not json_mode:
        header("AI Contact Classify")
        typer.echo(f"\n  name: {name!r}")
        if messages:
            typer.echo(f"  messages: {len(messages)} provided\n")
        else:
            typer.echo("  messages: (none — name-only classification)\n")

    contact = {
        "display_name": name,
        "is_group": False,
        "is_bot": False,
        "messages": [{"text": m, "is_outgoing": False} for m in messages],
    }

    start = time.monotonic()

    try:
        classifications = await classify_contacts_batch_v2([contact])
    except Exception as e:
        typer.echo(f"  classify_contacts_batch_v2 failed: {type(e).__name__}: {e}")
        raise typer.Exit(1)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    if not classifications:
        typer.echo("  No classification returned.")
        raise typer.Exit(1)

    cls = classifications[0]

    result = {
        "name": name,
        "contact_type": cls.contact_type,
        "confidence": cls.confidence,
        "reasoning": cls.reasoning,
        "latency_ms": elapsed_ms,
    }

    if json_mode:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    type_colors = {
        "customer": typer.colors.GREEN,
        "supplier": typer.colors.CYAN,
        "personal": typer.colors.MAGENTA,
        "work": typer.colors.BLUE,
        "group": typer.colors.YELLOW,
    }
    color = type_colors.get(cls.contact_type, typer.colors.WHITE)
    typer.echo(typer.style(f"  {cls.contact_type.upper()}", fg=color, bold=True) +
               f"  (confidence: {cls.confidence:.0%})")
    typer.echo(f"  reasoning: {cls.reasoning}")
    typer.echo(f"  latency:   {elapsed_ms}ms")


# ── pairs ──────────────────────────────────────────────────────────────────────


@app.command()
def pairs(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    stats: bool = typer.Option(False, "--stats", help="Show intent breakdown instead of recent pairs"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show indexed conversation pairs."""
    asyncio.run(_pairs_impl(workspace, stats, json_output))


async def _pairs_impl(workspace: int, stats: bool, json_output: bool) -> None:
    _ensure_backend_path()
    try:
        from app.db.session import async_session
        from app.models.conversation_pair import ConversationPair
        from app.models.customer import Customer
        from sqlalchemy import select, func
        from datetime import datetime, timezone
    except ImportError as e:
        typer.echo(f"  Error: {e}")
        raise typer.Exit(1)

    def _relative_time(dt: datetime) -> str:
        if not dt:
            return "?"
        now = datetime.now(timezone.utc)
        delta = now - dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else now - dt
        if delta.days > 0:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        if hours > 0:
            return f"{hours}h ago"
        minutes = delta.seconds // 60
        return f"{minutes}m ago" if minutes > 0 else "just now"

    async with async_session() as db:
        total = await db.scalar(
            select(func.count(ConversationPair.id))
            .where(ConversationPair.workspace_id == workspace)
        )

        if json_output and not stats:
            result = await db.execute(
                select(ConversationPair, Customer.display_name)
                .join(Customer, ConversationPair.customer_id == Customer.id)
                .where(ConversationPair.workspace_id == workspace)
                .order_by(ConversationPair.pair_timestamp.desc())
                .limit(20)
            )
            pairs_list = [
                {
                    "id": p.id,
                    "customer_name": name,
                    "customer_turn": p.customer_turn,
                    "seller_turn": p.seller_turn,
                    "intent": p.intent,
                    "has_media": p.has_media,
                    "pair_timestamp": p.pair_timestamp.isoformat() if p.pair_timestamp else None,
                }
                for p, name in result
            ]
            typer.echo(json.dumps({"total": total, "pairs": pairs_list}, indent=2, ensure_ascii=False, default=str))
            return

        from cli.output import header
        header(f"Conversation Pairs — workspace {workspace}")
        typer.echo(f"  Total: {total or 0} pairs\n")

        if stats:
            intent_result = await db.execute(
                select(ConversationPair.intent, func.count(ConversationPair.id))
                .where(ConversationPair.workspace_id == workspace)
                .group_by(ConversationPair.intent)
                .order_by(func.count(ConversationPair.id).desc())
            )
            typer.echo("  By intent:")
            for intent, count in intent_result:
                typer.echo(f"    {intent or 'none':20} {count}")

            customer_result = await db.execute(
                select(Customer.display_name, func.count(ConversationPair.id))
                .join(Customer, ConversationPair.customer_id == Customer.id)
                .where(ConversationPair.workspace_id == workspace)
                .group_by(Customer.display_name)
                .order_by(func.count(ConversationPair.id).desc())
                .limit(10)
            )
            typer.echo("\n  By contact:")
            for name, count in customer_result:
                typer.echo(f"    {name or 'Unknown':20} {count}")
            return

        result = await db.execute(
            select(ConversationPair, Customer.display_name)
            .join(Customer, ConversationPair.customer_id == Customer.id)
            .where(ConversationPair.workspace_id == workspace)
            .order_by(ConversationPair.pair_timestamp.desc())
            .limit(10)
        )
        for p, customer_name in result:
            intent_tag = f" | {p.intent}" if p.intent else ""
            time_str = _relative_time(p.pair_timestamp)
            typer.echo(f"  #{p.id} | {customer_name or '?'} | {time_str}{intent_tag}")
            typer.echo(f"    C: {p.customer_turn[:80]}")
            typer.echo(f"    S: {p.seller_turn[:80]}")
            typer.echo()


# ── corpus-query ──────────────────────────────────────────────────────────────


@app.command()
def query(
    text: Annotated[str, typer.Argument(help="Search query against Business Brain conversation pairs")],
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    limit: int = typer.Option(5, "--limit", "-n", help="Number of results"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Search conversation-pair facts through Retrieval Core."""
    asyncio.run(_query_impl(text, workspace, limit, json_mode))


async def _query_impl(query_text: str, workspace: int, limit: int, json_mode: bool) -> None:
    _ensure_backend_path()
    try:
        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.commercial_spine.repository import CommercialSpineRepository
        from app.modules.retrieval_core.contracts import RetrievalContextRequest
        from app.modules.retrieval_core.service import RetrievalCoreService
        from sqlalchemy import select
    except ImportError as e:
        typer.echo(f"  Error: {e}")
        raise typer.Exit(1)

    if not json_mode:
        header(f"Business Brain Pair Query — workspace {workspace}")
        typer.echo(f"\n  query: {query_text!r}\n")

    start = time.monotonic()

    with _suppress_info_logs(json_mode):
        async with async_session() as db:
            ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
            if ws is None:
                typer.echo(f"  Workspace {workspace} not found.")
                raise typer.Exit(1)
            retrieval = await RetrievalCoreService(
                repository=CommercialSpineRepository(db),
            ).retrieve_contextual(
                RetrievalContextRequest(
                    workspace_id=workspace,
                    requested_fact_types=["conversation_pair_fact"],
                    query_text=query_text,
                    enable_semantic=True,
                    enable_query_rewrite=True,
                    enable_rerank=True,
                    limit=limit,
                )
            )

    elapsed_ms = int((time.monotonic() - start) * 1000)
    items = [
        _retrieval_item(candidate.model_dump(mode="json"), include_source_units=False)
        for candidate in retrieval.candidates
    ]

    if json_mode:
        typer.echo(
            json.dumps(
                {
                    "query": query_text,
                    "results": items,
                    "degraded_reasons": list(retrieval.degraded_reasons),
                    "trace": retrieval.trace.model_dump(mode="json"),
                    "latency_ms": elapsed_ms,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return

    if not items:
        typer.echo("  No pairs found.")
    else:
        for item in items:
            typer.echo(f"  {item['fact_id']} | score={item['score']}")
            text = str(item.get("contextual_text") or "")
            if text:
                typer.echo(f"    {text[:240]}")
            typer.echo()

    typer.echo(f"  {len(items)} results  |  latency: {elapsed_ms}ms")


@app.command("compact")
def compact(
    agent: int = typer.Option(..., "--agent", help="Agent ID"),
    conversation: int = typer.Option(..., "--conversation", help="Conversation ID"),
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    apply: bool = typer.Option(False, "--apply", help="Commit (default: dry-run)"),
    focus: Optional[str] = typer.Option(None, "--focus", help="Optional focus topic"),
):
    """Compact an agent_session's stored Hermes context on demand (non-destructive)."""
    asyncio.run(
        _compact_impl(
            agent=agent, conversation=conversation, workspace=workspace,
            apply=apply, focus=focus,
        )
    )


async def _compact_impl(*, agent: int, conversation: int, workspace: int, apply: bool, focus):
    _ensure_backend_path()

    try:
        from sqlalchemy import text

        from app.db.session import async_session
        from app.modules.agent_runtime_v2.session_compaction import (
            SessionCompactionService,
        )
    except ImportError as e:
        typer.echo(f"  Cannot import backend modules: {e}")
        raise typer.Exit(1)

    async with async_session() as db:
        db_name = await db.scalar(text("select current_database()"))
        header(f"Compact — workspace {workspace}  agent {agent}  conversation {conversation}")
        typer.echo(f"  DB={db_name}  mode={'APPLY' if apply else 'DRY-RUN (pass --apply to commit)'}\n")
        try:
            result = await SessionCompactionService(db).compact(
                workspace_id=workspace, agent_id=agent, conversation_id=conversation,
                apply=apply, focus=focus,
            )
        except LookupError as exc:
            typer.echo(f"  ✗ {exc}")
            raise typer.Exit(1)

        typer.echo(f"  {result.headline}")
        typer.echo(f"  {result.token_line}")
        if result.note:
            typer.echo(f"  {result.note}")
        if result.applied:
            typer.echo(f"  ✓ applied: {result.old_session_id} -> {result.new_session_id}")
        elif result.noop:
            typer.echo("  • nothing to compact yet (not enough middle turns)")
        else:
            typer.echo("  • dry-run: nothing written")
