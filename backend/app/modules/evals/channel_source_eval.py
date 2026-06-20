from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channel_runtime.source import (
    ChannelRuntimeCore,
    ChannelSourceSubscription,
)
from app.modules.channel_runtime.source_queue import ChannelSourceLearningQueueService
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.hermes_runtime.service import HermesRunService
from app.modules.onboarding_learning.source_runtime import (
    OnboardingSourceLearningRuntimeService,
)
from app.services.channel_sync_models import ChannelMessageRecord
from app.services.source_learning_worker import claim_due_source_learning_jobs


@dataclass(frozen=True, slots=True)
class ChannelSourceEvalCase:
    case_id: str
    description: str


class ChannelSourceEvalCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class ChannelSourceEvalResult(BaseModel):
    case_id: str
    description: str
    passed: bool
    freshness_state: str
    grouped_media_count: int = Field(ge=0)
    multimodal_media_ref_count: int = Field(ge=0)
    extraction_job_count: int = Field(ge=0)
    queued_learning_count: int = Field(ge=0, default=0)
    claimed_source_count: int = Field(ge=0, default=0)
    processed_learning_count: int = Field(ge=0, default=0)
    catalog_candidate_count: int = Field(ge=0, default=0)
    hermes_run_trace_count: int = Field(ge=0, default=0)
    tenant_leak_count: int = Field(ge=0, default=0)
    degraded_reasons: list[str] = Field(default_factory=list)
    account_state_impact: str
    duration_ms: int = Field(ge=0)
    checks: list[ChannelSourceEvalCheck] = Field(default_factory=list)


class ChannelSourceEvalSuiteReport(BaseModel):
    suite: str = "channel-source"
    channel: str
    total_runs: int = Field(ge=0)
    passed_runs: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    grouped_media_count: int = Field(ge=0)
    multimodal_media_ref_count: int = Field(ge=0)
    extraction_job_count: int = Field(ge=0)
    queued_learning_count: int = Field(ge=0, default=0)
    claimed_source_count: int = Field(ge=0, default=0)
    processed_learning_count: int = Field(ge=0, default=0)
    catalog_candidate_count: int = Field(ge=0, default=0)
    hermes_run_trace_count: int = Field(ge=0, default=0)
    tenant_leak_count: int = Field(ge=0, default=0)
    degraded_freshness_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    p95_case_duration_ms: int = Field(ge=0)
    results: list[ChannelSourceEvalResult] = Field(default_factory=list)


def run_channel_source_eval_suite(*, channel: str) -> ChannelSourceEvalSuiteReport:
    started = time.monotonic()
    normalized_channel = channel.strip().lower()
    if normalized_channel != "telegram":
        result = ChannelSourceEvalResult(
            case_id="unsupported_channel",
            description="Only the Telegram channel-source proof is implemented.",
            passed=False,
            freshness_state="unknown",
            grouped_media_count=0,
            multimodal_media_ref_count=0,
            extraction_job_count=0,
            degraded_reasons=["unsupported_channel"],
            account_state_impact="none",
            duration_ms=0,
            checks=[
                ChannelSourceEvalCheck(
                    name="telegram_channel_selected",
                    passed=False,
                    detail=f"channel={channel}",
                )
            ],
        )
        return _report(
            channel=channel,
            results=[result],
            started=started,
        )

    results = [
        _run_grouped_media_catalog_source_case(),
        _run_flood_wait_degraded_freshness_case(),
    ]
    return _report(channel=normalized_channel, results=results, started=started)


async def run_channel_source_durable_eval_suite(
    *,
    session: AsyncSession,
    workspace_id: int,
    channel: str,
) -> ChannelSourceEvalSuiteReport:
    started = time.monotonic()
    normalized_channel = channel.strip().lower()
    if normalized_channel != "telegram":
        return run_channel_source_eval_suite(channel=channel)

    results = [
        _run_grouped_media_catalog_source_case(),
        _run_flood_wait_degraded_freshness_case(),
        await _run_source_learning_queue_claim_case(
            session=session,
            workspace_id=workspace_id,
        ),
        await _run_source_to_catalog_execution_case(
            session=session,
            workspace_id=workspace_id,
        ),
    ]
    return _report(channel=normalized_channel, results=results, started=started)


def _run_grouped_media_catalog_source_case() -> ChannelSourceEvalResult:
    case = ChannelSourceEvalCase(
        case_id="telegram_grouped_media_catalog_source",
        description=(
            "Telegram channel posts preserve source evidence, grouped media, "
            "and catalog extraction work for downstream multimodal retrieval."
        ),
    )
    started = time.monotonic()
    subscription = ChannelSourceSubscription(
        subscription_id="source-sub:telegram:@oqim_shop",
        workspace_id=1001,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref="@oqim_shop",
        source_scope="catalog",
        sync_policy={"mode": "background", "max_posts": 50},
        freshness_state="fresh",
        last_cursor="41",
        status="active",
    )
    messages = [
        ChannelMessageRecord(
            external_message_id="42",
            sender_external_id="@oqim_shop",
            text="Yangi charm sumka, qizil rang, 120000 UZS",
            sent_at=datetime(2026, 6, 5, 10, 0, tzinfo=UTC),
            is_outgoing=False,
            media_type="photo",
            media_metadata={"mime_type": "image/jpeg"},
            grouped_id=9001,
        ),
        ChannelMessageRecord(
            external_message_id="43",
            sender_external_id="@oqim_shop",
            text="Ichki cho'ntaklari va orqa tomoni",
            sent_at=datetime(2026, 6, 5, 10, 1, tzinfo=UTC),
            is_outgoing=False,
            media_type="photo",
            media_metadata={"mime_type": "image/jpeg"},
            grouped_id=9001,
        ),
    ]
    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=messages,
    )
    media_refs = [media_ref for item in plan.items for media_ref in item.media_refs]
    extraction_job = plan.extraction_jobs[0] if plan.extraction_jobs else None
    checks = [
        ChannelSourceEvalCheck(
            name="background_ingestion",
            passed=plan.background is True,
            detail=f"background={plan.background}",
        ),
        ChannelSourceEvalCheck(
            name="source_evidence_refs",
            passed=[item.source_evidence_ref for item in plan.items]
            == [
                "channel_source:telegram_channel:@oqim_shop:42",
                "channel_source:telegram_channel:@oqim_shop:43",
            ],
            detail=f"refs={[item.source_evidence_ref for item in plan.items]}",
        ),
        ChannelSourceEvalCheck(
            name="grouped_media_preserved",
            passed=len(plan.grouped_media) == 1
            and plan.grouped_media[0].media_refs == media_refs,
            detail=f"groups={[group.model_dump() for group in plan.grouped_media]}",
        ),
        ChannelSourceEvalCheck(
            name="multimodal_media_refs_preserved",
            passed=len(media_refs) == 2
            and all(ref.startswith("channel_media:telegram_channel:") for ref in media_refs),
            detail=f"media_refs={media_refs}",
        ),
        ChannelSourceEvalCheck(
            name="catalog_extraction_job",
            passed=extraction_job is not None
            and extraction_job.job_kind == "source_to_catalog"
            and "channel_media_group:telegram_channel:@oqim_shop:9001"
            in extraction_job.source_refs,
            detail=f"jobs={[job.model_dump() for job in plan.extraction_jobs]}",
        ),
        ChannelSourceEvalCheck(
            name="cursor_advances",
            passed=plan.last_cursor == "43",
            detail=f"last_cursor={plan.last_cursor}",
        ),
    ]
    return ChannelSourceEvalResult(
        case_id=case.case_id,
        description=case.description,
        passed=all(check.passed for check in checks),
        freshness_state=plan.freshness_state,
        grouped_media_count=len(plan.grouped_media),
        multimodal_media_ref_count=len(media_refs),
        extraction_job_count=len(plan.extraction_jobs),
        degraded_reasons=list(plan.degraded_reasons),
        account_state_impact=plan.account_state_impact,
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


def _run_flood_wait_degraded_freshness_case() -> ChannelSourceEvalResult:
    case = ChannelSourceEvalCase(
        case_id="telegram_flood_wait_degrades_freshness_only",
        description=(
            "Telegram background flood-wait pauses degrade source freshness "
            "without impacting live account state."
        ),
    )
    started = time.monotonic()
    subscription = ChannelSourceSubscription(
        subscription_id="source-sub:telegram:@slow_shop",
        workspace_id=1002,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref="@slow_shop",
        source_scope="catalog",
        sync_policy={"mode": "background", "max_posts": 50},
        freshness_state="fresh",
        last_cursor="99",
        status="active",
    )
    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=[],
        degraded_reason="flood_wait",
        retry_after_seconds=120,
    )
    checks = [
        ChannelSourceEvalCheck(
            name="freshness_degraded",
            passed=plan.freshness_state == "degraded",
            detail=f"freshness_state={plan.freshness_state}",
        ),
        ChannelSourceEvalCheck(
            name="flood_wait_recorded",
            passed=plan.degraded_reasons == ["flood_wait"]
            and plan.retry_after_seconds == 120,
            detail=(
                f"degraded={plan.degraded_reasons} "
                f"retry_after={plan.retry_after_seconds}"
            ),
        ),
        ChannelSourceEvalCheck(
            name="live_account_unaffected",
            passed=plan.account_state_impact == "none",
            detail=f"account_state_impact={plan.account_state_impact}",
        ),
        ChannelSourceEvalCheck(
            name="cursor_preserved",
            passed=plan.last_cursor == "99",
            detail=f"last_cursor={plan.last_cursor}",
        ),
        ChannelSourceEvalCheck(
            name="no_empty_extraction_job",
            passed=not plan.extraction_jobs,
            detail=f"jobs={plan.extraction_jobs}",
        ),
    ]
    return ChannelSourceEvalResult(
        case_id=case.case_id,
        description=case.description,
        passed=all(check.passed for check in checks),
        freshness_state=plan.freshness_state,
        grouped_media_count=len(plan.grouped_media),
        multimodal_media_ref_count=sum(len(item.media_refs) for item in plan.items),
        extraction_job_count=len(plan.extraction_jobs),
        degraded_reasons=list(plan.degraded_reasons),
        account_state_impact=plan.account_state_impact,
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


async def _run_source_learning_queue_claim_case(
    *,
    session: AsyncSession,
    workspace_id: int,
) -> ChannelSourceEvalResult:
    case = ChannelSourceEvalCase(
        case_id="telegram_source_learning_queue_claim",
        description=(
            "Telegram channel-source plans queue canonical Source Learning work "
            "that is claimable by the existing worker path and traced by HermesRun."
        ),
    )
    started = time.monotonic()
    subscription = ChannelSourceSubscription(
        subscription_id=f"source-sub:telegram:@oqim_eval_queue:{workspace_id}",
        workspace_id=workspace_id,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref=f"@oqim_eval_queue_{workspace_id}",
        source_scope="catalog",
        sync_policy={"mode": "background", "max_posts": 20},
        freshness_state="fresh",
        last_cursor="700",
        status="active",
    )
    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=[
            ChannelMessageRecord(
                external_message_id="701",
                sender_external_id=subscription.external_channel_ref,
                text="Eval charm hamyon 99000 UZS, qora rang",
                sent_at=datetime(2026, 6, 5, 12, 30, tzinfo=UTC),
                is_outgoing=False,
                media_type="photo",
                media_metadata={"mime_type": "image/jpeg"},
                grouped_id=None,
            )
        ],
    )
    repository = CommercialSpineRepository(session)
    queued = await ChannelSourceLearningQueueService(repository).queue_ingestion_plan(
        plan=plan,
        correlation_id=f"channel-source-eval:{workspace_id}:queue",
    )
    claims = await claim_due_source_learning_jobs(
        session,
        lease_owner="channel-source-eval",
        limit=16,
        now=datetime(2026, 6, 5, 12, 31, tzinfo=UTC),
    )
    fact = await repository.get_fact(
        workspace_id=workspace_id,
        fact_id=queued.source_fact_id,
    )
    projection = await repository.get_projection(
        workspace_id=workspace_id,
        projection_ref=queued.projection_ref,
    )
    run = await HermesRunService(session).get_by_output_ref(queued.projection_ref)
    claimed_source_refs = {
        source_ref
        for claim in claims
        if claim.workspace_id == workspace_id
        for source_ref in claim.source_refs
    }
    foreign_claim_count = sum(
        1
        for claim in claims
        for source_ref in claim.source_refs
        if source_ref == queued.source_ref and claim.workspace_id != workspace_id
    )
    checks = [
        ChannelSourceEvalCheck(
            name="canonical_source_fact_written",
            passed=fact is not None
            and fact.fact_type == "business_source_fact"
            and fact.entity_ref == f"workspace:source:{queued.source_ref}",
            detail=f"fact_id={queued.source_fact_id} present={fact is not None}",
        ),
        ChannelSourceEvalCheck(
            name="source_learning_projection_queued",
            passed=projection is not None
            and projection.state.get("status") in {"queued", "learning"}
            and projection.state.get("trigger_runtime") == "channel_source",
            detail=(
                f"projection={queued.projection_ref} "
                f"state={(projection.state if projection else {})}"
            ),
        ),
        ChannelSourceEvalCheck(
            name="worker_claims_source_ref",
            passed=queued.source_ref in claimed_source_refs,
            detail=f"claimed={sorted(claimed_source_refs)} expected={queued.source_ref}",
        ),
        ChannelSourceEvalCheck(
            name="hermes_run_trace_recorded",
            passed=run is not None
            and run.agent_kind == "channel_source"
            and run.lane == "background"
            and run.run_mode == "learning"
            and run.details.get("runtime_profile_kind") == "channel_source",
            detail=f"run_id={(run.run_id if run else None)}",
        ),
        ChannelSourceEvalCheck(
            name="workspace_claim_scoped",
            passed=foreign_claim_count == 0,
            detail=f"foreign_claim_count={foreign_claim_count}",
        ),
    ]
    return ChannelSourceEvalResult(
        case_id=case.case_id,
        description=case.description,
        passed=all(check.passed for check in checks),
        freshness_state=plan.freshness_state,
        grouped_media_count=len(plan.grouped_media),
        multimodal_media_ref_count=sum(len(item.media_refs) for item in plan.items),
        extraction_job_count=len(plan.extraction_jobs),
        queued_learning_count=1 if queued.queued else 0,
        claimed_source_count=1 if queued.source_ref in claimed_source_refs else 0,
        hermes_run_trace_count=1 if run is not None else 0,
        tenant_leak_count=foreign_claim_count,
        degraded_reasons=list(plan.degraded_reasons),
        account_state_impact=plan.account_state_impact,
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


async def _run_source_to_catalog_execution_case(
    *,
    session: AsyncSession,
    workspace_id: int,
) -> ChannelSourceEvalResult:
    case = ChannelSourceEvalCase(
        case_id="telegram_source_to_catalog_execution",
        description=(
            "Telegram channel-source work executes through Source Learning "
            "and produces reviewable catalog candidates."
        ),
    )
    started = time.monotonic()
    subscription = ChannelSourceSubscription(
        subscription_id=f"source-sub:telegram:@oqim_eval_catalog:{workspace_id}",
        workspace_id=workspace_id,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref=f"@oqim_eval_catalog_{workspace_id}",
        source_scope="catalog",
        sync_policy={
            "mode": "background",
            "max_posts": 20,
            "structured_source": "shopify_products_json",
        },
        freshness_state="fresh",
        last_cursor="800",
        status="active",
    )
    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=[
            ChannelMessageRecord(
                external_message_id="801",
                sender_external_id=subscription.external_channel_ref,
                text=(
                    "Product: Channel Atlas Wallet\n"
                    "Handle: channel-atlas-wallet\n"
                    "Vendor: OQIM\n"
                    "Type: wallet\n"
                    "Compact wallet from channel source.\n"
                    "Variant: Default; sku=CAW-1; price=144000; "
                    "availability=available; options=color:emerald"
                ),
                sent_at=datetime(2026, 6, 5, 12, 40, tzinfo=UTC),
                is_outgoing=False,
                media_type="photo",
                media_metadata={"mime_type": "image/jpeg"},
                grouped_id=None,
            )
        ],
    )
    repository = CommercialSpineRepository(session)
    queued = await ChannelSourceLearningQueueService(repository).queue_ingestion_plan(
        plan=plan,
        correlation_id=f"channel-source-eval:{workspace_id}:catalog-execution",
    )
    claims = await claim_due_source_learning_jobs(
        session,
        lease_owner="channel-source-eval-execution",
        limit=16,
        now=datetime(2026, 6, 5, 12, 32, tzinfo=UTC),
    )
    claimed_source_refs = {
        source_ref
        for claim in claims
        if claim.workspace_id == workspace_id
        for source_ref in claim.source_refs
    }
    processing = await OnboardingSourceLearningRuntimeService(
        repository=repository,
        embed_source_units=False,
        contextualize_source_units=False,
    ).process_workspace_sources(
        workspace_id=workspace_id,
        correlation_id=f"channel-source-eval:{workspace_id}:catalog-process",
        limit=1,
        max_attempts=3,
        source_refs={queued.source_ref},
    )
    projection = await repository.get_projection(
        workspace_id=workspace_id,
        projection_ref=queued.projection_ref,
    )
    products = await repository.list_facts(
        workspace_id=workspace_id,
        fact_type="catalog_product",
        statuses=("proposed",),
        limit=20,
    )
    offers = await repository.list_facts(
        workspace_id=workspace_id,
        fact_type="catalog_offer",
        statuses=("proposed",),
        limit=20,
    )
    source_projection_state = dict(projection.state or {}) if projection is not None else {}
    channel_products = [
        product
        for product in products
        if queued.source_ref in list(getattr(product, "source_refs", []) or [])
    ]
    channel_offers = [
        offer
        for offer in offers
        if queued.source_ref in list(getattr(offer, "source_refs", []) or [])
    ]
    foreign_claim_count = sum(
        1
        for claim in claims
        for source_ref in claim.source_refs
        if source_ref == queued.source_ref and claim.workspace_id != workspace_id
    )
    checks = [
        ChannelSourceEvalCheck(
            name="structured_source_metadata_preserved",
            passed=plan.sync_policy.get("structured_source") == "shopify_products_json",
            detail=f"sync_policy={plan.sync_policy}",
        ),
        ChannelSourceEvalCheck(
            name="worker_claims_catalog_source",
            passed=queued.source_ref in claimed_source_refs,
            detail=f"claimed={sorted(claimed_source_refs)} expected={queued.source_ref}",
        ),
        ChannelSourceEvalCheck(
            name="source_learning_processed",
            passed=processing.review_ready_count == 1
            and source_projection_state.get("status") == "review_ready",
            detail=(
                f"processed={processing.processed_count} "
                f"review_ready={processing.review_ready_count} "
                f"state={source_projection_state}"
            ),
        ),
        ChannelSourceEvalCheck(
            name="catalog_candidate_written",
            passed=len(channel_products) == 1
            and len(channel_offers) == 1
            and channel_products[0].value.get("title") == "Channel Atlas Wallet",
            detail=(
                f"products={[product.fact_id for product in channel_products]} "
                f"offers={[offer.fact_id for offer in channel_offers]}"
            ),
        ),
        ChannelSourceEvalCheck(
            name="workspace_claim_scoped",
            passed=foreign_claim_count == 0,
            detail=f"foreign_claim_count={foreign_claim_count}",
        ),
    ]
    return ChannelSourceEvalResult(
        case_id=case.case_id,
        description=case.description,
        passed=all(check.passed for check in checks),
        freshness_state=plan.freshness_state,
        grouped_media_count=len(plan.grouped_media),
        multimodal_media_ref_count=sum(len(item.media_refs) for item in plan.items),
        extraction_job_count=len(plan.extraction_jobs),
        queued_learning_count=1 if queued.queued else 0,
        claimed_source_count=1 if queued.source_ref in claimed_source_refs else 0,
        processed_learning_count=processing.review_ready_count,
        catalog_candidate_count=len(channel_products),
        tenant_leak_count=foreign_claim_count,
        degraded_reasons=list(plan.degraded_reasons),
        account_state_impact=plan.account_state_impact,
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


def _report(
    *,
    channel: str,
    results: list[ChannelSourceEvalResult],
    started: float,
) -> ChannelSourceEvalSuiteReport:
    passed = sum(1 for result in results if result.passed)
    durations = [result.duration_ms for result in results]
    return ChannelSourceEvalSuiteReport(
        channel=channel,
        total_runs=len(results),
        passed_runs=passed,
        pass_rate=(passed / len(results)) if results else 0.0,
        grouped_media_count=sum(result.grouped_media_count for result in results),
        multimodal_media_ref_count=sum(
            result.multimodal_media_ref_count for result in results
        ),
        extraction_job_count=sum(result.extraction_job_count for result in results),
        queued_learning_count=sum(result.queued_learning_count for result in results),
        claimed_source_count=sum(result.claimed_source_count for result in results),
        processed_learning_count=sum(
            result.processed_learning_count for result in results
        ),
        catalog_candidate_count=sum(result.catalog_candidate_count for result in results),
        hermes_run_trace_count=sum(result.hermes_run_trace_count for result in results),
        tenant_leak_count=sum(result.tenant_leak_count for result in results),
        degraded_freshness_count=sum(
            1 for result in results if result.freshness_state == "degraded"
        ),
        duration_ms=int((time.monotonic() - started) * 1000),
        p95_case_duration_ms=_percentile_ms(durations, 0.95),
        results=results,
    )


def _percentile_ms(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * percentile)),
    )
    return ordered[index]
