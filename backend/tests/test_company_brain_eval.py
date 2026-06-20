from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.model_policy import MODEL_GEMINI_EMBEDDING_2
from app.models.workspace import Workspace
from app.modules.business_brain.source_media_artifacts import SourceMediaArtifactStore
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.evals.company_brain_eval import run_company_brain_eval_suite


async def test_company_brain_eval_covers_mixed_sources(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
    tmp_path,
) -> None:
    async def fake_embed_texts_batch(_self, texts: list[str]) -> list[list[float]]:
        return [[0.01] * 3072 for _ in texts]

    async def fake_embed_text(_self, _text: str, intent: str = "document") -> list[float]:
        return [0.01 if intent == "document" else 0.02] * 3072

    async def fake_embed_query(_self, _query: str) -> list[float]:
        return [0.02] * 3072

    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService.embed_text",
        fake_embed_text,
    )
    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService.embed_texts_batch",
        fake_embed_texts_batch,
    )
    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService.embed_query",
        fake_embed_query,
    )

    report = await run_company_brain_eval_suite(
        workspace_id=workspace.id,
        repository=CommercialSpineRepository(db_session),
        media_artifact_store=SourceMediaArtifactStore(
            base_path=tmp_path / "company-brain-media"
        ),
        embed_source_units=True,
        enable_semantic_retrieval=True,
    )

    assert report.total_sources == 13
    assert report.pass_rate == 1.0, report.model_dump_json(indent=2)
    assert {result.source_kind for result in report.results} == {
        "text",
        "website",
        "pdf",
        "screenshot",
        "telegram_channel",
        "voice_note",
        "spreadsheet",
        "past_conversation",
    }
    assert report.product_count >= 9
    assert report.knowledge_count >= 16
    assert report.conversation_pair_count >= 1
    assert report.media_count >= 2
    assert report.embedding_ready_count > 0
    assert report.embedding_degraded_count == 0
    assert MODEL_GEMINI_EMBEDDING_2 in report.embedding_model_ids
    assert report.semantic_retrieval_enabled is True
    assert report.retrieval_pass_rate == 1.0
    assert report.median_source_duration_ms >= 0
    assert report.p95_source_duration_ms >= report.median_source_duration_ms
    assert report.max_source_duration_ms >= report.p95_source_duration_ms
    assert all(result.passed for result in report.results), report.model_dump_json(indent=2)
    support_site = next(
        result for result in report.results if result.source_id == "support_center_site"
    )
    assert support_site.product_count == 0
    assert support_site.knowledge_count >= 1
    assert support_site.retrieval_hit
    program_pdf = next(
        result for result in report.results if result.source_id == "startup_program_pdf"
    )
    assert program_pdf.product_count == 0
    assert program_pdf.knowledge_count >= 1
    assert program_pdf.retrieval_hit
    messy_pdf_ids = {
        "clinic_policy_pdf",
        "real_estate_company_pdf",
        "course_terms_messy_pdf",
    }
    messy_pdf_results = [
        result for result in report.results if result.source_id in messy_pdf_ids
    ]
    assert len(messy_pdf_results) == 3
    assert all(result.product_count == 0 for result in messy_pdf_results)
    assert all(result.knowledge_count >= 1 for result in messy_pdf_results)
    assert all(result.retrieval_hit for result in messy_pdf_results)
    past_conversation = next(
        result for result in report.results if result.source_kind == "past_conversation"
    )
    assert past_conversation.conversation_pair_count >= 1
    assert {check.name for check in past_conversation.checks} >= {
        "learned_conversation_pair",
        "retrieval_hit",
        "embedding_ready",
    }
    assert not report.hallucinated_source_ref_failures
