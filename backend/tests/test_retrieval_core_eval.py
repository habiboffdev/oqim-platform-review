from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.evals.retrieval_core_eval import run_retrieval_core_eval_suite


async def test_retrieval_core_eval_covers_agentic_rewrite_rerank_and_media_alias(
    db_session: AsyncSession,
    workspace: Workspace,
    workspace_b: Workspace,
) -> None:
    report = await run_retrieval_core_eval_suite(
        repository=CommercialSpineRepository(db_session),
        workspace_ids=(workspace.id, workspace_b.id),
        repetitions=1,
    )

    assert report.suite == "retrieval-core"
    assert report.pass_rate == 1.0
    assert report.workspace_count == 2
    assert report.cross_workspace_leak_count == 0
    assert report.median_case_duration_ms >= 0
    assert report.p95_case_duration_ms >= report.median_case_duration_ms
    assert report.max_case_duration_ms >= report.p95_case_duration_ms
    assert {result.case_id for result in report.results} == {
        "agentic_media_alias",
        "knowledge_policy_recall",
        "query_rewrite_catalog_alias",
    }
    agentic_media = next(
        result for result in report.results if result.case_id == "agentic_media_alias"
    )
    assert "agentic_search" in agentic_media.retrieval_channels
    assert "rerank" in agentic_media.retrieval_channels
    assert agentic_media.duration_ms >= 0
    assert any(
        "business_source_media" in fact_id
        for fact_id in agentic_media.retrieved_fact_ids
    )


async def test_retrieval_core_eval_live_reranker_fails_when_provider_falls_back(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def fallback_rerank(query: str, candidates: list[dict], **kwargs):
        return candidates[: kwargs.get("top_n", 5)]

    with patch("app.modules.retrieval_core.service.reranker.rerank", fallback_rerank):
        report = await run_retrieval_core_eval_suite(
            repository=CommercialSpineRepository(db_session),
            workspace_ids=(workspace.id,),
            repetitions=1,
            use_live_reranker=True,
        )

    assert report.pass_rate == 0.0
    assert all("rerank_unavailable" in result.degraded_reasons for result in report.results)
    assert all(
        any(check.name == "no_degraded_retrieval" and not check.passed for check in result.checks)
        for result in report.results
    )
