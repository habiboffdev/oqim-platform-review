"""Evaluation commands for product, reply quality, and sales CRM behavior."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import typer

from cli.config import BACKEND_DIR
from cli.output import header, status_line

app = typer.Typer(no_args_is_help=True)

QUALITY_SUITE_COMMANDS = (
    "golden-demo",
    "onboarding",
    "seller-agent",
    "grounding",
    "autopilot",
    "bi",
    "learning-loop",
)


def _reply_eval_voice_fact_value() -> dict:
    return {
        "profile_text": (
            "Qisqa, tabiiy Uzbek Telegram sotuvchi ovozi. Javoblar lo'nda, "
            "halol va savdoni keyingi aniq qadamga olib boradi. Narx, mavjudlik "
            "yoki to'lovni faqat ishonchli dalil bo'lsa tasdiqlaydi."
        ),
        "exemplar_bank": {
            "greeting_new": [
                "Assalomu alaykum aka, qanday yordam beray?",
            ],
            "clarify_variant": [
                "Qaysi turidan kerak aka? Shunga qarab aniq aytaman.",
            ],
            "price_quote": [
                "Aniq narxini tekshirib aytaman, qaysi turidan kerak?",
            ],
        },
        "message_pattern": "one_shot",
        "burst_count": 1,
        "delay_range": {"min_ms": 800, "max_ms": 1800},
        "quality_score": "strong",
        "anti_patterns": [
            "ishonchli dalilsiz model, variant, narx yoki mavjudlikni tasdiqlash",
            "men sun'iy intellektman",
        ],
        "delay_profiles": {},
        "language_rules": {
            "preferred": ["uzbek_latin", "short", "seller_like"],
            "avoid": ["assistant_language", "unsupported_claims"],
        },
        "voice_card": {
            "tone": "short_helpful_honest",
            "language": "uzbek_latin",
            "closing": "one clear next-step question when needed",
        },
        "message_count_analyzed": 24,
    }


def _reply_eval_warranty_fact_value() -> dict:
    return {
        "topic": "warranty",
        "question": "kafolat bormi?",
        "answer": (
            "Sotiladigan takliflarga 7 kunlik tekshiruv kafolati bor. Muammo chiqsa "
            "mijoz yozadi, holat tekshiriladi va yechim taklif qilinadi."
        ),
        "guidance": (
            "Kafolat savollarida qisqa javob bering: 7 kunlik tekshiruv kafolati bor, "
            "muammo bo'lsa yozishini so'rang."
        ),
    }


def _reply_eval_return_policy_fact_value() -> dict:
    return {
        "topic": "return_exchange",
        "question": "qaytarish yoki almashtirish tartibi qanday?",
        "answer": (
            "Qaytarish yoki almashtirish bo'yicha mijoz avval buyurtma raqami, "
            "mahsulot holati va muammo tavsifini yuboradi. Jamoa holatni "
            "tekshiradi va mos yechimni taklif qiladi."
        ),
        "guidance": (
            "Qaytarish yoki almashtirish savollarida mijozdan buyurtma raqami "
            "va muammo tavsifini so'rang; aniq tasdiqni tekshiruvdan keyin bering."
        ),
    }


def _reply_eval_offerings_fact_value() -> dict:
    return {
        "topic": "offerings",
        "question": "qanday mahsulot va xizmatlar bor?",
        "answer": (
            "Bizda erkaklar uchun qizil paxta futbolka bor — narxi 90 000 so'm, "
            "o'lchamlar M, L va XL. Telefon ekranini almashtirish xizmati 150 000 "
            "so'mdan boshlanadi, model aniqlangach aniq narx aytiladi. Kechki ingliz "
            "tili kurslari ham bor: haftada uch marta, kechqurun."
        ),
        "guidance": (
            "Mahsulot yoki narx so'ralganda mavjud ma'lumotni bering va kerakli "
            "tafsilotni (o'lcham, model, kun) so'rab sotuvni davom ettiring. "
            "Mavjud bo'lmagan narx yoki zaxirani o'ylab topmang."
        ),
    }


def _reply_eval_red_tshirt_product_value() -> dict:
    # A real catalog_product (not a faked knowledge_fact) so the v2 reply engine
    # exercises catalog grounding — the seller_agent grounding family the live
    # path actually relies on for product/price/stock questions.
    return {
        "name": "Qizil paxta futbolka",
        "price": "90 000 so'm",
        "availability": "bor",
        "description": "Erkaklar uchun qizil paxta futbolka, o'lchamlar M, L va XL.",
    }


def _ensure_backend_path() -> None:
    backend_str = str(BACKEND_DIR)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)


def _load_quality_report(suite: str):
    _ensure_backend_path()
    try:
        from app.modules.evals.quality_eval import run_quality_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    try:
        return run_quality_eval_suite(suite=suite)
    except ValueError as exc:
        typer.echo(f"  {exc}")
        raise typer.Exit(1) from None


def _emit_quality_report(*, suite: str, json_mode: bool) -> None:
    report = _load_quality_report(suite)

    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if report.release_class in {"blue", "green"} else 1)

    header(f"OQIM — {suite} Quality Eval")
    ok = report.release_class in {"blue", "green"}
    status_line(
        "quality_eval",
        ok,
        (
            f"{report.release_class.upper()} · score={report.weighted_score:.2f} · "
            f"{report.passed_cases}/{report.total_cases} passed, "
            f"{report.partial_cases} partial, {report.failed_cases} failed"
        ),
    )
    typer.echo(f"  Decision: {report.decision}")
    for result in report.results:
        label = result.status.upper()
        typer.echo(f"  [{label}] {result.scenario_id}: {result.description}")
        for missing in result.missing[:3]:
            typer.echo(f"    - missing: {missing}")
        for fault in result.critical_faults[:3]:
            typer.echo(f"    - critical: {fault}")

    raise typer.Exit(0 if ok else 1)


def _quality_command(suite: str, json_mode: bool) -> None:
    _emit_quality_report(suite=suite, json_mode=json_mode)


def _make_quality_command(suite: str):
    def command(
        json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
    ) -> None:
        _quality_command(suite=suite, json_mode=json_mode)

    command.__name__ = suite.replace("-", "_")
    return command


for _suite_name in QUALITY_SUITE_COMMANDS:
    app.command(name=_suite_name)(_make_quality_command(_suite_name))


@app.command(name="replies")
def replies(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    seed_workspace: bool = typer.Option(
        False,
        "--seed-workspace",
        help="Create or reuse the deterministic reply-eval workspace before running",
    ),
    engine: str = typer.Option(
        "v2",
        "--engine",
        help="Reply engine: 'v2' (agent_runtime_v2) or 'seller-runtime' (current seller eval suite)",
    ),
    suite: str = typer.Option("regression", "--suite", help="Reply eval suite name"),
    trials: int = typer.Option(1, "--trials", min=1, max=5, help="Repeat each case this many times"),
    concurrency: int = typer.Option(1, "--concurrency", min=1, max=8, help="Run reply eval cases in parallel"),
    max_p95_ms: int | None = typer.Option(None, "--max-p95-ms", min=1, help="Fail if case latency p95 exceeds this value"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run seller reply quality evals with traces and budget checks."""
    asyncio.run(
        _replies_impl(
            workspace=workspace,
            seed_workspace=seed_workspace,
            engine=engine,
            suite=suite,
            trials=trials,
            concurrency=concurrency,
            max_p95_ms=max_p95_ms,
            json_mode=json_mode,
        )
    )


async def _replies_impl(
    *,
    workspace: int,
    seed_workspace: bool,
    engine: str,
    suite: str,
    trials: int,
    concurrency: int,
    max_p95_ms: int | None,
    json_mode: bool,
) -> None:
    _ensure_backend_path()
    if engine == "v2":
        await _reply_agent_v2_impl(
            workspace=workspace, seed_workspace=seed_workspace, json_mode=json_mode
        )
        return
    if engine != "seller-runtime":
        typer.echo("  Unknown reply eval engine. Use 'v2' or 'seller-runtime'.")
        raise typer.Exit(1)
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.evals.seller_eval import run_seller_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    if seed_workspace:
        workspace = await _ensure_reply_eval_workspace()

    async with async_session() as db:
        ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)

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

    header(f"OQIM — Reply Eval workspace {workspace}")
    status_line(
        "reply_eval",
        passed,
        f"{report.passed_cases}/{report.total_cases} passed, {report.soft_warning_count} soft warnings",
    )
    typer.echo(
        f"  concurrency={report.concurrency} latency_p95={int(report.latency_ms_p95)}ms "
        f"max_case={report.max_case_latency_ms}ms"
    )
    if max_p95_ms is not None and not p95_passed:
        typer.echo(f"  [FAIL] latency_p95_budget: expected <= {max_p95_ms}ms")
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(f"  [{label}] {result.case_id}: {result.description}")
        failed_checks = [check for check in result.checks if not check.passed]
        for check in failed_checks[:5]:
            typer.echo(f"    - [{check.severity}] {check.name}: {check.detail}")

    raise typer.Exit(0 if passed else 1)


async def _reply_agent_v2_impl(
    *,
    workspace: int,
    seed_workspace: bool,
    json_mode: bool,
) -> None:
    """Run the P5a agent_runtime_v2 reply-engine eval (the new generic engine).

    Seeds/reuses the deterministic reply-eval workspace + agent, then grades the
    new engine's runtime invariants across verticals. This is the viability
    signal for the gated S6 cutover; the deep quality-vs-legacy comparison is the
    job of shadow mode on real traffic.
    """
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.agent import Agent
        from app.models.workspace import Workspace
        from app.modules.evals.reply_agent_eval import run_reply_agent_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    if seed_workspace:
        workspace = await _ensure_reply_eval_workspace()

    async with async_session() as db:
        ws = await db.scalar(select(Workspace).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        agent_id = await db.scalar(
            select(Agent.id)
            .where(Agent.workspace_id == workspace, Agent.is_active.is_(True))
            .limit(1)
        )
        if agent_id is None:
            typer.echo(f"  No active agent in workspace {workspace}.")
            raise typer.Exit(1)
        report = await run_reply_agent_eval_suite(
            session=db, workspace_id=workspace, agent_id=int(agent_id)
        )

    passed = report.passed_cases == report.total_cases

    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header(f"OQIM — Reply Agent v2 Eval (workspace {report.workspace_id}, agent {report.agent_id})")
    status_line(
        "reply_agent_v2_eval",
        passed,
        f"{report.passed_cases}/{report.total_cases} passed (pass_rate={report.pass_rate})",
    )
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(
            f"  [{label}] {result.case_id} ({result.vertical}): "
            f"action={result.action} conf={result.confidence:.2f}"
        )
        typer.echo(f"      reply: {result.reply_text[:140]}")
        for check in result.checks:
            if not check.passed:
                typer.echo(f"      - [FAIL] {check.name}: {check.detail}")
    raise typer.Exit(0 if passed else 1)


async def _ensure_reply_eval_workspace() -> int:
    _ensure_backend_path()
    from sqlalchemy import select

    from app.db.session import async_session
    from app.models.agent import Agent
    from app.models.workspace import Workspace

    eval_phone = "+199900000001"
    async with async_session() as db:
        workspace = await db.scalar(select(Workspace).where(Workspace.phone_number == eval_phone))
        if workspace is None:
            workspace = Workspace(
                phone_number=eval_phone,
                name="OQIM Reply Eval Seller",
                password_hash="reply-eval-only",
                type="retail",
                monthly_revenue_band="eval",
                telegram_connected=False,
                onboarding_completed=True,
                trust_mode="draft",
            )
            db.add(workspace)
            await db.flush()
        else:
            workspace.type = "retail"

        agent_id = await db.scalar(
            select(Agent.id)
            .where(Agent.workspace_id == workspace.id, Agent.is_active.is_(True))
            .limit(1)
        )
        if agent_id is None:
            db.add(
                Agent(
                    workspace_id=workspace.id,
                    name="Reply Eval Seller Agent",
                    is_active=True,
                    is_default=True,
                    agent_type="customer",
                    contact_scope="business",
                    trust_mode="draft",
                    persona={"role": "Uzbek Telegram seller", "tone": "short, helpful, honest"},
                    tools_config={"enabled_tools": ["catalog_core", "business_brain_memory"]},
                    knowledge_config={"use_catalog": True, "use_knowledge": True},
                )
            )

        await _seed_reply_eval_business_brain(db, workspace_id=int(workspace.id))
        await db.commit()
        return int(workspace.id)


async def _seed_reply_eval_business_brain(db, *, workspace_id: int) -> None:
    from app.modules.business_brain.memory import BusinessBrainMemoryService
    from app.modules.business_brain.memory_contracts import (
        MemoryFactWriteInput,
        SourceUnitRebuildRequest,
        VoiceProjectionRequest,
    )
    from app.modules.commercial_spine.repository import CommercialSpineRepository

    repository = CommercialSpineRepository(db)
    memory = BusinessBrainMemoryService(repository=repository)
    warranty_value = _reply_eval_warranty_fact_value()
    return_policy_value = _reply_eval_return_policy_fact_value()
    offerings_value = _reply_eval_offerings_fact_value()
    voice_value = _reply_eval_voice_fact_value()
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace_id,
            fact_id="knowledge:reply_eval:warranty",
            fact_type="knowledge_fact",
            entity_ref="business:faq:warranty",
            value=warranty_value,
            source_refs=["eval:reply:warranty_policy"],
            correlation_id="reply-eval:business-brain:knowledge",
            idempotency_key="reply-eval:business-brain:knowledge:warranty",
        )
    )
    await repository.update_fact_state(
        workspace_id=workspace_id,
        fact_id="knowledge:reply_eval:warranty",
        value=warranty_value,
        status="active",
        confidence=0.95,
        risk_tier="low",
    )
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace_id,
            fact_id="knowledge:reply_eval:return_policy",
            fact_type="knowledge_fact",
            entity_ref="business:faq:return_exchange",
            value=return_policy_value,
            source_refs=["eval:reply:return_exchange_policy"],
            correlation_id="reply-eval:business-brain:knowledge",
            idempotency_key="reply-eval:business-brain:knowledge:return-policy",
        )
    )
    await repository.update_fact_state(
        workspace_id=workspace_id,
        fact_id="knowledge:reply_eval:return_policy",
        value=return_policy_value,
        status="active",
        confidence=0.95,
        risk_tier="low",
    )
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace_id,
            fact_id="knowledge:reply_eval:offerings",
            fact_type="knowledge_fact",
            entity_ref="business:faq:offerings",
            value=offerings_value,
            source_refs=["eval:reply:offerings"],
            correlation_id="reply-eval:business-brain:knowledge",
            idempotency_key="reply-eval:business-brain:knowledge:offerings",
        )
    )
    await repository.update_fact_state(
        workspace_id=workspace_id,
        fact_id="knowledge:reply_eval:offerings",
        value=offerings_value,
        status="active",
        confidence=0.95,
        risk_tier="low",
    )
    red_tshirt_value = _reply_eval_red_tshirt_product_value()
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace_id,
            fact_id="catalog_product:reply_eval:red_tshirt",
            fact_type="catalog_product",
            entity_ref="catalog:product:reply_eval:red_tshirt",
            value=red_tshirt_value,
            source_refs=["eval:reply:red_tshirt"],
            correlation_id="reply-eval:business-brain:catalog",
            idempotency_key="reply-eval:business-brain:catalog:red-tshirt",
        )
    )
    await repository.update_fact_state(
        workspace_id=workspace_id,
        fact_id="catalog_product:reply_eval:red_tshirt",
        value=red_tshirt_value,
        status="active",
        confidence=0.95,
        risk_tier="low",
    )
    await memory.write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace_id,
            fact_id="voice:reply_eval:strong",
            fact_type="voice_fact",
            entity_ref="seller_voice",
            value=voice_value,
            source_refs=["eval:reply:seller_voice"],
            correlation_id="reply-eval:business-brain:voice",
            idempotency_key="reply-eval:business-brain:voice:strong",
        )
    )
    await repository.update_fact_state(
        workspace_id=workspace_id,
        fact_id="voice:reply_eval:strong",
        value=voice_value,
        status="active",
        confidence=0.95,
        risk_tier="low",
    )
    await memory.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace_id,
            fact_types=["knowledge_fact", "voice_fact", "catalog_product"],
            candidate_fact_ids=[
                "knowledge:reply_eval:warranty",
                "knowledge:reply_eval:return_policy",
                "knowledge:reply_eval:offerings",
                "catalog_product:reply_eval:red_tshirt",
                "voice:reply_eval:strong",
            ],
        )
    )
    await memory.rebuild_voice_projection(
        VoiceProjectionRequest(workspace_id=workspace_id)
    )


@app.command(name="sales")
def sales(
    suite: str = typer.Option("core", "--suite", help="Sales eval suite name"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run deterministic CRM stage, follow-up, and next-action sales evals."""
    _ensure_backend_path()
    try:
        from app.modules.evals.sales_eval import run_sales_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    report = run_sales_eval_suite(suite=suite)

    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if report.pass_rate == 1.0 else 1)

    header("OQIM — Sales Eval")
    status_line(
        "sales_eval",
        report.pass_rate == 1.0,
        f"{report.passed_cases}/{report.total_cases} passed",
    )
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(f"  [{label}] {result.case_id}: {result.description}")
        for check in result.checks:
            if not check.passed:
                typer.echo(f"    - {check.name}: {check.detail}")

    raise typer.Exit(0 if report.pass_rate == 1.0 else 1)


@app.command(name="sales-replay")
def sales_replay(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run seller sales replay through Agent Session and Action Runtime shadow delivery."""
    asyncio.run(_sales_replay_impl(workspace=workspace, json_mode=json_mode))


@app.command(name="adversarial-replay")
def adversarial_replay(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run adversarial sales replay through the same shadow autopilot path."""
    asyncio.run(_adversarial_replay_impl(workspace=workspace, json_mode=json_mode))


@app.command(name="shadow-autopilot")
def shadow_autopilot(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    conversation: int = typer.Option(
        ...,
        "--conversation",
        help="Conversation ID used as the local proof anchor.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run one conversation-shaped autopilot proof with delivery blocked to shadow sink."""
    asyncio.run(
        _shadow_autopilot_impl(
            workspace=workspace,
            conversation=conversation,
            json_mode=json_mode,
        )
    )


async def _sales_replay_impl(*, workspace: int, json_mode: bool) -> None:
    _ensure_backend_path()
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.evals.sales_replay_eval import run_sales_replay_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    async with async_session() as db:
        ws = await db.scalar(select(Workspace.id).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        report = await run_sales_replay_eval_suite(session=db, workspace_id=workspace)
        await db.rollback()

    _emit_replay_report(report=report, json_mode=json_mode)


async def _adversarial_replay_impl(*, workspace: int, json_mode: bool) -> None:
    _ensure_backend_path()
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.evals.adversarial_replay_eval import (
            run_adversarial_replay_eval_suite,
        )
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    async with async_session() as db:
        ws = await db.scalar(select(Workspace.id).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        report = await run_adversarial_replay_eval_suite(
            session=db,
            workspace_id=workspace,
        )
        await db.rollback()

    _emit_replay_report(report=report, json_mode=json_mode)


async def _shadow_autopilot_impl(
    *,
    workspace: int,
    conversation: int,
    json_mode: bool,
) -> None:
    _ensure_backend_path()
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.conversation import Conversation
        from app.models.workspace import Workspace
        from app.modules.evals.sales_replay_eval import run_shadow_autopilot_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    async with async_session() as db:
        ws = await db.scalar(select(Workspace.id).where(Workspace.id == workspace))
        conversation_id = await db.scalar(
            select(Conversation.id).where(
                Conversation.id == conversation,
                Conversation.workspace_id == workspace,
            )
        )
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        if conversation_id is None:
            typer.echo(f"  Conversation {conversation} not found in workspace {workspace}.")
            raise typer.Exit(1)
        report = await run_shadow_autopilot_eval_suite(
            session=db,
            workspace_id=workspace,
            conversation_id=conversation,
        )
        await db.rollback()

    _emit_replay_report(report=report, json_mode=json_mode)


def _emit_replay_report(*, report, json_mode: bool) -> None:
    truth_delta = int(getattr(report, "business_truth_fact_delta", 0) or 0)
    passed = (
        report.pass_rate == 1.0
        and report.customer_visible_delivery_count == 0
        and truth_delta == 0
    )
    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header(f"OQIM — {report.suite} Eval")
    status_line(
        f"{report.suite.replace('-', '_')}_eval",
        passed,
        (
            f"{report.passed_cases}/{report.total_cases} passed · "
            f"shadow={report.shadow_delivery_count} · "
            f"customer_visible={report.customer_visible_delivery_count} · "
            f"truth_delta={truth_delta} · "
            f"tokens={report.total_input_tokens}/{report.total_output_tokens} · "
            f"p95={report.p95_latency_ms}ms"
        ),
    )
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(
            f"  [{label}] {result.case_id}: "
            f"outcome={result.outcome_kind} shadow={result.shadow_delivery}"
        )
        typer.echo(f"      reply: {result.reply_text[:140]}")
        for check in result.checks:
            if not check.passed:
                typer.echo(f"      - [{check.severity}] {check.name}: {check.detail}")

    raise typer.Exit(0 if passed else 1)


@app.command(name="buyer-intent")
def buyer_intent(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    repetitions: int = typer.Option(1, "--repetitions", "-n", min=1, max=20),
    concurrency: int = typer.Option(
        1,
        "--concurrency",
        "-c",
        min=1,
        max=8,
        help="Parallel eval cases. Uses one DB session per case when >1.",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Use the real LLM Gateway instead of the deterministic provider.",
    ),
    max_p95_ms: int | None = typer.Option(
        None,
        "--max-p95-ms",
        min=1,
        help="Fail if case latency p95 exceeds this value.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run isolated Universal Extraction buyer-intent quality cases."""
    asyncio.run(
        _buyer_intent_impl(
            workspace=workspace,
            repetitions=repetitions,
            concurrency=concurrency,
            live=live,
            max_p95_ms=max_p95_ms,
            json_mode=json_mode,
        )
    )


async def _buyer_intent_impl(
    *,
    workspace: int,
    repetitions: int,
    concurrency: int,
    live: bool,
    max_p95_ms: int | None,
    json_mode: bool,
) -> None:
    _ensure_backend_path()
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.commercial_spine.repository import CommercialSpineRepository
        from app.modules.evals.buyer_intent_eval import run_buyer_intent_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    async with async_session() as db:
        row = await db.execute(select(Workspace.id).where(Workspace.id == workspace))
        if row.scalar_one_or_none() is None:
            typer.echo(f"  Workspace not found: {workspace}")
            raise typer.Exit(1)

        report = await run_buyer_intent_eval_suite(
            repository=CommercialSpineRepository(db),
            workspace_id=workspace,
            live=live,
            repetitions=repetitions,
            concurrency=concurrency,
            session_factory=async_session,
        )
        await db.rollback()

    p95_passed = max_p95_ms is None or report.p95_case_duration_ms <= max_p95_ms
    passed = report.pass_rate == 1.0 and p95_passed
    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header("OQIM — Buyer Intent Eval")
    status_line(
        "buyer_intent_eval",
        passed,
        (
            f"{report.passed_runs}/{report.total_runs} passed · "
            f"live={report.live} · "
            f"concurrency={report.concurrency} · "
            f"rejected={report.rejected_candidate_count} · "
            f"case_p95={report.p95_case_duration_ms}ms · "
            f"{report.duration_ms}ms"
        ),
    )
    if max_p95_ms is not None and not p95_passed:
        typer.echo(f"  [FAIL] latency_p95_budget: expected <= {max_p95_ms}ms")
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(
            "  "
            f"[{label}] {result.case_id}: "
            f"intent={result.detected_intent} "
            f"strategy={result.response_strategy} "
            f"shape={result.answer_shape}"
        )
        for check in result.checks:
            if not check.passed:
                typer.echo(f"    - {check.name}: {check.detail}")

    raise typer.Exit(0 if passed else 1)


@app.command(name="retrieval-core")
def retrieval_core(
    workspace: list[int] | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace ID. Pass multiple times to prove workspace isolation.",
    ),
    repetitions: int = typer.Option(1, "--repetitions", "-n", min=1, max=20),
    max_p95_ms: int | None = typer.Option(
        None,
        "--max-p95-ms",
        min=1,
        help="Fail if case latency p95 exceeds this value.",
    ),
    live_rerank_provider: bool = typer.Option(
        False,
        "--live-rerank-provider",
        help="Use the configured external reranker instead of the deterministic eval reranker.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run deterministic Retrieval Core agentic RAG quality cases."""
    asyncio.run(
        _retrieval_core_impl(
            workspace_ids=tuple(workspace or [1]),
            repetitions=repetitions,
            max_p95_ms=max_p95_ms,
            live_rerank_provider=live_rerank_provider,
            json_mode=json_mode,
        )
    )


async def _retrieval_core_impl(
    *,
    workspace_ids: tuple[int, ...],
    repetitions: int,
    max_p95_ms: int | None,
    live_rerank_provider: bool,
    json_mode: bool,
) -> None:
    _ensure_backend_path()
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.commercial_spine.repository import CommercialSpineRepository
        from app.modules.evals.retrieval_core_eval import run_retrieval_core_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    workspace_ids = tuple(dict.fromkeys(int(item) for item in workspace_ids if item))
    if not workspace_ids:
        typer.echo("  At least one workspace is required.")
        raise typer.Exit(1)

    async with async_session() as db:
        rows = await db.execute(select(Workspace.id).where(Workspace.id.in_(workspace_ids)))
        found_ids = set(rows.scalars().all())
        missing = [workspace_id for workspace_id in workspace_ids if workspace_id not in found_ids]
        if missing:
            typer.echo(f"  Workspace not found: {', '.join(str(item) for item in missing)}")
            raise typer.Exit(1)

        report = await run_retrieval_core_eval_suite(
            repository=CommercialSpineRepository(db),
            workspace_ids=workspace_ids,
            repetitions=repetitions,
            use_live_reranker=live_rerank_provider,
        )
        await db.rollback()

    p95_passed = max_p95_ms is None or report.p95_case_duration_ms <= max_p95_ms
    passed = report.pass_rate == 1.0 and p95_passed
    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header("OQIM — Retrieval Core Eval")
    status_line(
        "retrieval_core_eval",
        passed,
        (
            f"{report.passed_runs}/{report.total_runs} passed · "
            f"workspaces={report.workspace_count} · "
            f"leaks={report.cross_workspace_leak_count} · "
            f"rerank={'live' if live_rerank_provider else 'deterministic'} · "
            f"case_p95={report.p95_case_duration_ms}ms · "
            f"{report.duration_ms}ms"
        ),
    )
    if max_p95_ms is not None and not p95_passed:
        typer.echo(f"  [FAIL] latency_p95_budget: expected <= {max_p95_ms}ms")
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(f"  [{label}] ws={result.workspace_id} {result.case_id}")
        for check in result.checks:
            if not check.passed:
                typer.echo(f"    - {check.name}: {check.detail}")

    raise typer.Exit(0 if passed else 1)


@app.command(name="channel-source")
def channel_source(
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help="Run the deterministic Telegram channel-source eval.",
    ),
    durable: bool = typer.Option(
        False,
        "--durable",
        help="Also prove durable source-learning queue, worker claim, and HermesRun trace.",
    ),
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID for durable proof."),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run deterministic Channel Runtime source-ingestion proof cases."""
    if durable:
        asyncio.run(
            _channel_source_durable_impl(
                telegram=telegram,
                workspace=workspace,
                json_mode=json_mode,
            )
        )
        return
    _channel_source_impl(telegram=telegram, json_mode=json_mode)


def _channel_source_impl(*, telegram: bool, json_mode: bool) -> None:
    _ensure_backend_path()
    if not telegram:
        typer.echo("  Select a channel source eval, for example: --telegram")
        raise typer.Exit(1)
    try:
        from app.modules.evals.channel_source_eval import run_channel_source_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    report = run_channel_source_eval_suite(channel="telegram")
    passed = report.pass_rate == 1.0
    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header("OQIM — Channel Source Eval")
    status_line(
        "channel_source_eval",
        passed,
        (
            f"{report.passed_runs}/{report.total_runs} passed · "
            f"channel={report.channel} · "
            f"grouped_media={report.grouped_media_count} · "
            f"media_refs={report.multimodal_media_ref_count} · "
            f"extraction_jobs={report.extraction_job_count} · "
            f"degraded={report.degraded_freshness_count} · "
            f"case_p95={report.p95_case_duration_ms}ms · "
            f"{report.duration_ms}ms"
        ),
    )
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(f"  [{label}] {result.case_id}")
        for check in result.checks:
            if not check.passed:
                typer.echo(f"    - {check.name}: {check.detail}")

    raise typer.Exit(0 if passed else 1)


async def _channel_source_durable_impl(
    *,
    telegram: bool,
    workspace: int,
    json_mode: bool,
) -> None:
    _ensure_backend_path()
    if not telegram:
        typer.echo("  Select a channel source eval, for example: --telegram")
        raise typer.Exit(1)
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.evals.channel_source_eval import run_channel_source_durable_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    async with async_session() as db:
        ws = await db.scalar(select(Workspace.id).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        report = await run_channel_source_durable_eval_suite(
            session=db,
            workspace_id=workspace,
            channel="telegram",
        )
        await db.rollback()

    passed = report.pass_rate == 1.0 and report.tenant_leak_count == 0
    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header("OQIM — Channel Source Durable Eval")
    status_line(
        "channel_source_durable_eval",
        passed,
        (
            f"{report.passed_runs}/{report.total_runs} passed · "
            f"workspace={workspace} · "
            f"channel={report.channel} · "
            f"grouped_media={report.grouped_media_count} · "
            f"media_refs={report.multimodal_media_ref_count} · "
            f"extraction_jobs={report.extraction_job_count} · "
            f"queued={report.queued_learning_count} · "
            f"claimed={report.claimed_source_count} · "
            f"hermes_runs={report.hermes_run_trace_count} · "
            f"tenant_leaks={report.tenant_leak_count} · "
            f"case_p95={report.p95_case_duration_ms}ms · "
            f"{report.duration_ms}ms"
        ),
    )
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(f"  [{label}] {result.case_id}")
        for check in result.checks:
            if not check.passed:
                typer.echo(f"    - {check.name}: {check.detail}")

    raise typer.Exit(0 if passed else 1)


@app.command(name="runtime-profiles")
def runtime_profiles(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    agent: int = typer.Option(..., "--agent", help="Agent ID to compile/profile."),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run deterministic RuntimeProfileCompiler background/profile proof cases."""
    asyncio.run(
        _runtime_profiles_impl(
            workspace=workspace,
            agent=agent,
            json_mode=json_mode,
        )
    )


async def _runtime_profiles_impl(
    *,
    workspace: int,
    agent: int,
    json_mode: bool,
) -> None:
    _ensure_backend_path()
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.agent import Agent
        from app.models.workspace import Workspace
        from app.modules.evals.runtime_profile_eval import (
            run_runtime_profile_background_eval_suite,
        )
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    async with async_session() as db:
        ws = await db.scalar(select(Workspace.id).where(Workspace.id == workspace))
        agent_id = await db.scalar(
            select(Agent.id).where(Agent.id == agent, Agent.workspace_id == workspace)
        )
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        if agent_id is None:
            typer.echo(f"  Agent {agent} not found in workspace {workspace}.")
            raise typer.Exit(1)
        report = await run_runtime_profile_background_eval_suite(
            session=db,
            workspace_id=workspace,
            agent_id=agent,
        )
        await db.rollback()

    passed = report.pass_rate == 1.0 and report.deduped_replay_count == report.profile_count
    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header("OQIM — Runtime Profiles Eval")
    status_line(
        "runtime_profiles_eval",
        passed,
        (
            f"{report.passed_runs}/{report.total_runs} passed · "
            f"workspace={report.workspace_id} · "
            f"agent={report.agent_id} · "
            f"profiles={report.profile_count} · "
            f"completed={report.completed_run_count} · "
            f"deduped={report.deduped_replay_count} · "
            f"tool_schemas={report.tool_schema_count} · "
            f"case_p95={report.p95_case_duration_ms}ms · "
            f"{report.duration_ms}ms"
        ),
    )
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(
            f"  [{label}] {result.profile_kind} lane={result.lane} "
            f"mode={result.run_mode} tools={result.tool_schema_count}"
        )
        for check in result.checks:
            if not check.passed:
                typer.echo(f"    - {check.name}: {check.detail}")

    raise typer.Exit(0 if passed else 1)


@app.command(name="channel-delivery")
def channel_delivery(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    agent: int = typer.Option(..., "--agent", help="Agent ID to grant/use for delivery proof."),
    conversation: int = typer.Option(
        ...,
        "--conversation",
        help="Conversation ID to use for local delivery proof.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run deterministic Channel Runtime delivery proof cases."""
    asyncio.run(
        _channel_delivery_impl(
            workspace=workspace,
            agent=agent,
            conversation=conversation,
            json_mode=json_mode,
        )
    )


async def _channel_delivery_impl(
    *,
    workspace: int,
    agent: int,
    conversation: int,
    json_mode: bool,
) -> None:
    _ensure_backend_path()
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.agent import Agent
        from app.models.conversation import Conversation
        from app.models.workspace import Workspace
        from app.modules.evals.channel_delivery_eval import run_channel_delivery_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    async with async_session() as db:
        ws = await db.scalar(select(Workspace.id).where(Workspace.id == workspace))
        agent_id = await db.scalar(
            select(Agent.id).where(Agent.id == agent, Agent.workspace_id == workspace)
        )
        conversation_id = await db.scalar(
            select(Conversation.id).where(
                Conversation.id == conversation,
                Conversation.workspace_id == workspace,
            )
        )
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        if agent_id is None:
            typer.echo(f"  Agent {agent} not found in workspace {workspace}.")
            raise typer.Exit(1)
        if conversation_id is None:
            typer.echo(f"  Conversation {conversation} not found in workspace {workspace}.")
            raise typer.Exit(1)
        report = await run_channel_delivery_eval_suite(
            session=db,
            workspace_id=workspace,
            agent_id=agent,
            conversation_id=conversation,
        )

    passed = report.pass_rate == 1.0 and report.duplicate_delivery_count == 0
    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header("OQIM — Channel Delivery Eval")
    status_line(
        "channel_delivery_eval",
        passed,
        (
            f"{report.passed_runs}/{report.total_runs} passed · "
            f"workspace={report.workspace_id} · "
            f"agent={report.agent_id} · "
            f"conversation={report.conversation_id} · "
            f"intents={report.intent_count} · "
            f"sent={report.sent_count} · "
            f"unknown={report.unknown_count} · "
            f"replayed={report.replayed_count} · "
            f"duplicates={report.duplicate_delivery_count} · "
            f"case_p95={report.p95_case_duration_ms}ms · "
            f"{report.duration_ms}ms"
        ),
    )
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(f"  [{label}] {result.case_id}")
        for check in result.checks:
            if not check.passed:
                typer.echo(f"    - {check.name}: {check.detail}")

    raise typer.Exit(0 if passed else 1)


@app.command(name="catalog-core")
def catalog_core(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    multimodal: bool = typer.Option(
        False,
        "--multimodal",
        help="Include media OCR/visual search and retrieval index rebuild proof.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Run deterministic Commerce Catalog Core projection and search proof cases."""
    asyncio.run(
        _catalog_core_impl(
            workspace=workspace,
            multimodal=multimodal,
            json_mode=json_mode,
        )
    )


async def _catalog_core_impl(
    *,
    workspace: int,
    multimodal: bool,
    json_mode: bool,
) -> None:
    _ensure_backend_path()
    try:
        from sqlalchemy import select

        from app.db.session import async_session
        from app.models.workspace import Workspace
        from app.modules.evals.catalog_core_eval import run_catalog_core_eval_suite
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    async with async_session() as db:
        ws = await db.scalar(select(Workspace.id).where(Workspace.id == workspace))
        if ws is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        report = await run_catalog_core_eval_suite(
            session=db,
            workspace_id=workspace,
            include_multimodal=multimodal,
        )
        await db.rollback()

    passed = report.pass_rate == 1.0
    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header("OQIM — Catalog Core Eval")
    status_line(
        "catalog_core_eval",
        passed,
        (
            f"{report.passed_runs}/{report.total_runs} passed · "
            f"workspace={report.workspace_id} · "
            f"multimodal={report.multimodal} · "
            f"products={report.projected_product_count} · "
            f"offers={report.projected_offer_count} · "
            f"media={report.projected_media_count} · "
            f"conflicts={report.conflict_count} · "
            f"missing={report.missing_field_count} · "
            f"indexed={report.indexed_source_unit_count} · "
            f"case_p95={report.p95_case_duration_ms}ms · "
            f"{report.duration_ms}ms"
        ),
    )
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(f"  [{label}] {result.case_id}")
        for check in result.checks:
            if not check.passed:
                typer.echo(f"    - {check.name}: {check.detail}")

    raise typer.Exit(0 if passed else 1)


@app.command(name="company-brain")
def company_brain(
    workspace: int = typer.Option(1, "--workspace", "-w", help="Workspace ID"),
    json_mode: bool = typer.Option(False, "--json", help="Output as JSON"),
    live: bool = typer.Option(False, "--live", help="Use the real LLM Gateway instead of the deterministic provider"),
    brutal: bool = typer.Option(False, "--brutal", help="Use live webpages, local PDFs in folder/, and harder Uzbek fixtures"),
    macbro: bool = typer.Option(False, "--macbro", help="Run the real Macbro.uz Shopify catalog fixture"),
    semantic: bool = typer.Option(False, "--semantic", help="Generate Gemini embeddings and use semantic retrieval"),
    contextual_source_units: bool = typer.Option(False, "--contextual-source-units", help="Use LLM-authored source-unit context before embedding"),
    max_media_assets: int = typer.Option(2, "--max-media-assets", help="Maximum media artifacts to learn per source during this eval"),
    max_p95_ms: int | None = typer.Option(
        None,
        "--max-p95-ms",
        min=1,
        help="Fail if source latency p95 exceeds this value.",
    ),
):
    """Run Company Brain eval across onboarding source kinds and retrieval."""
    asyncio.run(
        _company_brain_impl(
            workspace=workspace,
            json_mode=json_mode,
            live=live,
            brutal=brutal,
            macbro=macbro,
            semantic=semantic,
            contextual_source_units=contextual_source_units,
            max_media_assets=max_media_assets,
            max_p95_ms=max_p95_ms,
        )
    )


async def _company_brain_impl(
    *,
    workspace: int,
    json_mode: bool,
    live: bool,
    brutal: bool,
    macbro: bool,
    semantic: bool,
    contextual_source_units: bool,
    max_media_assets: int,
    max_p95_ms: int | None,
) -> None:
    _ensure_backend_path()
    try:
        from sqlalchemy import text

        from app.db.session import async_session
        from app.modules.business_brain.source_media_artifacts import SourceMediaArtifactStore
        from app.modules.commercial_spine.llm_gateway import LLMGateway
        from app.modules.commercial_spine.repository import CommercialSpineRepository
        from app.modules.evals.company_brain_eval import (
            brutal_company_brain_fixtures,
            macbro_company_brain_fixtures,
            run_company_brain_eval_suite,
        )
    except ImportError as exc:
        typer.echo(f"  Cannot import backend modules: {exc}")
        raise typer.Exit(1) from None

    async with async_session() as db:
        workspace_exists = await db.scalar(
            text("SELECT id FROM workspaces WHERE id = :workspace_id"),
            {"workspace_id": workspace},
        )
        if workspace_exists is None:
            typer.echo(f"  Workspace {workspace} not found.")
            raise typer.Exit(1)
        missing_tables = await _missing_company_brain_eval_tables(db)
        if missing_tables:
            typer.echo(
                "  Database is missing Company Brain eval tables: "
                + ", ".join(missing_tables)
            )
            typer.echo("  Run database migrations/reset before `oqim eval company-brain`.")
            raise typer.Exit(1)
        repository = CommercialSpineRepository(db)
        fixtures = None
        fetch_live_sources = brutal or macbro
        if macbro:
            fixtures = macbro_company_brain_fixtures()
        elif brutal:
            pdf_paths = tuple(sorted((Path.cwd() / "folder").glob("*.pdf")))
            fixtures = brutal_company_brain_fixtures(pdf_paths=pdf_paths)
        report = await run_company_brain_eval_suite(
            workspace_id=workspace,
            repository=repository,
            media_artifact_store=SourceMediaArtifactStore(
                base_path=Path(tempfile.gettempdir()) / "oqim-company-brain-eval-media"
            ),
            provider_factory=(
                (lambda _fixtures: LLMGateway(repository=repository)) if live else None
            ),
            fixtures=fixtures,
            fetch_live_sources=fetch_live_sources,
            embed_source_units=semantic,
            contextualize_source_units=contextual_source_units,
            enable_semantic_retrieval=semantic,
            max_media_assets_per_source=max_media_assets,
        )
        await db.rollback()

    p95_passed = max_p95_ms is None or report.p95_source_duration_ms <= max_p95_ms
    passed = report.pass_rate == 1.0 and p95_passed
    if json_mode:
        typer.echo(json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(0 if passed else 1)

    header(f"OQIM — Company Brain Eval workspace {workspace}")
    status_line(
        "company_brain_eval",
        passed,
        (
            f"{report.passed_sources}/{report.total_sources} sources passed · "
            f"products={report.product_count} kb={report.knowledge_count} "
            f"pairs={report.conversation_pair_count} media={report.media_count} · "
            f"deferred_media={report.deferred_media_count} · "
            f"retrieval={report.retrieval_pass_rate:.2f} · "
            f"embeddings={report.embedding_ready_count} ready/{report.embedding_degraded_count} degraded · "
            f"source_p95={report.p95_source_duration_ms}ms · "
            f"{report.duration_ms}ms"
        ),
    )
    if max_p95_ms is not None and not p95_passed:
        typer.echo(f"  [FAIL] source_latency_p95_budget: expected <= {max_p95_ms}ms")
    for result in report.results:
        label = "PASS" if result.passed else "FAIL"
        typer.echo(f"  [{label}] {result.source_kind}:{result.source_id}")
        for check in result.checks:
            if not check.passed:
                typer.echo(f"    - {check.name}: {check.detail}")
        if not result.passed:
            if result.learned_product_titles:
                typer.echo(
                    "    learned: " + ", ".join(result.learned_product_titles[:5])
                )
            if result.retrieved_product_titles:
                typer.echo(
                    "    retrieved: " + ", ".join(result.retrieved_product_titles[:5])
                )

    raise typer.Exit(0 if passed else 1)


async def _missing_company_brain_eval_tables(db) -> list[str]:
    from sqlalchemy import text

    required = [
        "business_brain_facts",
        "business_brain_updates",
        "business_brain_projections",
        "business_brain_index_records",
        "llm_gateway_traces",
    ]
    missing: list[str] = []
    for table_name in required:
        exists = await db.scalar(
            text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": table_name},
        )
        if not exists:
            missing.append(table_name)
    return missing
