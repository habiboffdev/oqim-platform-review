from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.evals.catalog_core_eval import run_catalog_core_eval_suite
from app.modules.retrieval_core.indexing import RetrievalIndexEmbeddingResult


async def test_catalog_core_eval_proves_projection_conflict_and_missing_fields(
    db_session: AsyncSession,
) -> None:
    report = await run_catalog_core_eval_suite(
        session=db_session,
        workspace_id=99501,
        include_multimodal=False,
    )

    assert report.suite == "catalog-core"
    assert report.pass_rate == 1.0
    assert report.passed_runs == report.total_runs
    assert report.projected_product_count >= 2
    assert report.projected_offer_count >= 2
    assert report.conflict_count >= 1
    assert report.resolved_conflict_count >= 1
    assert report.missing_field_count >= 1
    assert report.indexed_source_unit_count == 0
    assert {result.case_id for result in report.results} == {
        "typed_authority_projection",
        "conflict_and_missing_field_visibility",
        "conflict_resolution_lifecycle",
    }


async def test_catalog_core_eval_multimodal_proves_media_search_and_index_readiness(
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    async def fake_embed_texts(self, texts, *, enabled, context_prefix):
        assert enabled is True
        assert context_prefix == "business_brain_index"
        return [
            RetrievalIndexEmbeddingResult(
                embedding=[0.17, *([0.0] * 3071)],
                embedding_model="gemini-embedding-2",
                embedding_state="ready",
                degraded_reason=None,
            )
            for _text in texts
        ]

    monkeypatch.setattr(
        "app.modules.retrieval_core.indexing.RetrievalIndexEmbeddingService.embed_texts",
        fake_embed_texts,
    )

    report = await run_catalog_core_eval_suite(
        session=db_session,
        workspace_id=99502,
        include_multimodal=True,
    )

    assert report.pass_rate == 1.0
    assert report.multimodal is True
    assert report.projected_media_count >= 1
    assert report.media_search_hit_count >= 1
    assert report.indexed_source_unit_count >= 1
    assert report.stale_index_record_count >= 1
    assert "multimodal_media_authority" in {
        result.case_id for result in report.results
    }
    assert "retrieval_index_stale_lifecycle" in {
        result.case_id for result in report.results
    }
