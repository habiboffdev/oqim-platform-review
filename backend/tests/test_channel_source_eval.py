from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.evals.channel_source_eval import (
    run_channel_source_durable_eval_suite,
    run_channel_source_eval_suite,
)


def test_channel_source_eval_proves_telegram_source_ingestion_contracts() -> None:
    report = run_channel_source_eval_suite(channel="telegram")

    assert report.suite == "channel-source"
    assert report.channel == "telegram"
    assert report.pass_rate == 1.0
    assert report.passed_runs == report.total_runs
    assert report.grouped_media_count >= 1
    assert report.multimodal_media_ref_count >= 2
    assert report.extraction_job_count >= 1
    assert report.degraded_freshness_count == 1
    assert {result.case_id for result in report.results} == {
        "telegram_grouped_media_catalog_source",
        "telegram_flood_wait_degrades_freshness_only",
    }


def test_channel_source_eval_rejects_unknown_channel() -> None:
    report = run_channel_source_eval_suite(channel="instagram")

    assert report.channel == "instagram"
    assert report.pass_rate == 0.0
    assert report.total_runs == 1
    assert report.results[0].case_id == "unsupported_channel"
    assert not report.results[0].passed


async def test_channel_source_durable_eval_proves_queue_worker_claim_and_run_trace(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    report = await run_channel_source_durable_eval_suite(
        session=db_session,
        workspace_id=workspace.id,
        channel="telegram",
    )

    assert report.suite == "channel-source"
    assert report.channel == "telegram"
    assert report.pass_rate == 1.0
    assert report.passed_runs == report.total_runs
    assert report.queued_learning_count == 2
    assert report.claimed_source_count == 2
    assert report.processed_learning_count == 1
    assert report.catalog_candidate_count == 1
    assert report.hermes_run_trace_count == 1
    assert report.tenant_leak_count == 0
    assert {
        "telegram_source_learning_queue_claim",
        "telegram_source_to_catalog_execution",
    }.issubset({
        result.case_id for result in report.results
    })
