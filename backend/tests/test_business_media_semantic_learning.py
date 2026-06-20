from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial_spine import LLMGatewayTraceRecord
from app.models.workspace import Workspace
from app.modules.business_brain.media_learning import (
    BusinessMediaArtifactBatchLearningRequest,
    BusinessMediaArtifactBatchLearningService,
    BusinessMediaArtifactLearningRequest,
    BusinessMediaArtifactLearningService,
    BusinessMediaDeferredBatchLearningRequest,
    BusinessMediaInput,
    BusinessMediaSemanticLearningRequest,
    BusinessMediaSemanticLearningService,
)
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.business_brain.source_media_artifacts import SourceMediaArtifactStore
from app.modules.onboarding_learning.source_ingestion import (
    OnboardingSourceIngestionRequest,
    OnboardingSourceIngestionService,
)


def _image_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (40, 40), (20, 130, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _image_only_pdf_bytes() -> bytes:
    buffer = BytesIO()
    Image.open(BytesIO(_image_bytes())).save(buffer, format="PDF")
    return buffer.getvalue()


def _multi_image_pdf_bytes() -> bytes:
    images = [
        Image.new("RGB", (40, 40), color)
        for color in ((20, 130, 90), (180, 120, 40), (60, 80, 180))
    ]
    buffer = BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


async def test_media_semantic_learning_uses_inline_image_and_source_gate(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        assert request.workflow_name == "business_media_semantic_learning"
        assert request.content_parts[0]["kind"] == "text"
        assert "universal source learning workflow" in request.content_parts[0]["text"]
        assert "non-empty" in request.content_parts[0]["text"]
        assert "product.title" in request.content_parts[0]["text"]
        assert "source_media_ref" in request.content_parts[0]["text"]
        assert "Do not invent prices" in request.content_parts[0]["text"]
        assert "source_fact must include source_ref" in request.content_parts[0]["text"]
        assert "page_media_only" in request.content_parts[0]["text"]
        assert request.content_parts[1]["kind"] == "inline_data"
        assert request.content_parts[1]["data_base64"]
        media_ref = request.input_payload["analyzed_media_refs"][0]
        payload: dict[str, Any] = {
            "schema_version": "business_source_learning_output.v1",
            "catalog_candidates": [
                {
                    "product_ref": "catalog_product:scanned-green-item",
                    "product": {
                        "title": "Scanned green item",
                        "identity_ref": "catalog_product:scanned-green-item",
                    },
                    "variants": [
                        {
                            "variant_ref": "catalog_variant:scanned-green-item:main",
                            "product_ref": "catalog_product:scanned-green-item",
                            "attributes": {"color": "green"},
                        }
                    ],
                    "offers": [],
                    "media": [
                        {
                            "media_ref": "catalog_media:scanned-green-item:page-image",
                            "product_ref": "catalog_product:scanned-green-item",
                            "source_media_ref": media_ref,
                            "media_type": "image",
                            "approved": False,
                        }
                    ],
                    "source_fact": {
                        "source_ref": "onboarding:pdf:scan",
                        "source_type": "pdf",
                        "content_refs": [media_ref],
                    },
                    "confidence": 0.67,
                    "risk_tier": "medium",
                    "evidence_refs": [media_ref],
                }
            ],
            "memory_candidates": [],
        }
        return LLMProviderResponse(text=json.dumps(payload), model_used="test-model")

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:scan",
            source_kind="pdf",
            source_payload={"file_name": "scan.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-media-ingest",
            idempotency_key="media-ingest",
        )
    )
    media_ref = source.media_assets[0].media_ref
    service = BusinessMediaSemanticLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await service.learn_from_media(
        BusinessMediaSemanticLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:scan",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            media_inputs=[
                BusinessMediaInput(
                    media_ref=media_ref,
                    mime_type="image/jpeg",
                    data_base64=base64.b64encode(_image_bytes()).decode("ascii"),
                    page_number=1,
                )
            ],
            correlation_id="corr-media-learn",
            idempotency_key="media-learn",
        )
    )
    product = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:scanned-green-item",
    )
    trace = (
        await db_session.execute(
            select(LLMGatewayTraceRecord).where(
                LLMGatewayTraceRecord.workspace_id == workspace.id,
                LLMGatewayTraceRecord.correlation_id == "corr-media-learn",
            )
        )
    ).scalar_one()

    assert result.gateway_status == "ok"
    assert result.analyzed_media_refs == [media_ref]
    assert result.source_learning.catalog_candidate_count == 1
    assert result.degraded_reasons == []
    assert product is not None
    assert product.source_refs == [media_ref, "onboarding:pdf:scan"]
    assert "data_base64" not in json.dumps(trace.raw_request)


async def test_media_artifact_learning_loads_persisted_pdf_image_bytes(
    db_session: AsyncSession,
    workspace: Workspace,
    tmp_path,
) -> None:
    captured_inline_bytes: list[bytes] = []

    async def provider(request) -> LLMProviderResponse:
        assert request.workflow_name == "business_media_semantic_learning"
        media_ref = request.input_payload["analyzed_media_refs"][0]
        inline_payload = request.content_parts[1]
        assert inline_payload["kind"] == "inline_data"
        captured_inline_bytes.append(base64.b64decode(inline_payload["data_base64"]))
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "product_ref": "catalog_product:artifact-green-item",
                            "product": {
                                "title": "Artifact green item",
                                "identity_ref": "catalog_product:artifact-green-item",
                            },
                            "variants": [],
                            "offers": [],
                            "media": [
                                {
                                    "media_ref": "catalog_media:artifact-green-item:main",
                                    "product_ref": "catalog_product:artifact-green-item",
                                    "source_media_ref": media_ref,
                                    "media_type": "image",
                                    "approved": False,
                                }
                            ],
                            "source_fact": {
                                "source_ref": "onboarding:pdf:artifact",
                                "source_type": "pdf",
                                "content_refs": [media_ref],
                            },
                            "confidence": 0.71,
                            "risk_tier": "medium",
                            "evidence_refs": [media_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    artifact_store = SourceMediaArtifactStore(base_path=tmp_path / "source-media")
    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(
        repository=repository,
        media_artifact_store=artifact_store,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:artifact",
            source_kind="pdf",
            source_payload={"file_name": "artifact.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-artifact-ingest",
            idempotency_key="artifact-ingest",
        )
    )
    media_ref = source.media_assets[0].media_ref
    media_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=f"business_source_media:{media_ref}",
    )

    assert media_fact is not None
    artifact_ref = media_fact.value["artifact_ref"]
    stored = await artifact_store.read(artifact_ref=artifact_ref, workspace_id=workspace.id)
    assert stored is not None
    assert stored.content_bytes

    service = BusinessMediaArtifactLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
        media_artifact_store=artifact_store,
    )

    result = await service.learn_from_artifacts(
        BusinessMediaArtifactLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:artifact",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            media_refs=[media_ref],
            correlation_id="corr-artifact-learn",
            idempotency_key="artifact-learn",
        )
    )
    product = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:artifact-green-item",
    )
    trace = (
        await db_session.execute(
            select(LLMGatewayTraceRecord).where(
                LLMGatewayTraceRecord.workspace_id == workspace.id,
                LLMGatewayTraceRecord.correlation_id == "corr-artifact-learn",
            )
        )
    ).scalar_one()

    assert result.semantic_learning.gateway_status == "ok"
    assert result.loaded_media_refs == [media_ref]
    assert captured_inline_bytes == [stored.content_bytes]
    assert product is not None
    assert product.source_refs == [media_ref, "onboarding:pdf:artifact"]
    assert "data_base64" not in json.dumps(trace.raw_request)


async def test_media_artifact_batch_learning_chunks_pdf_images_and_rejects_empty_values(
    db_session: AsyncSession,
    workspace: Workspace,
    tmp_path,
) -> None:
    calls: list[list[str]] = []

    async def provider(request) -> LLMProviderResponse:
        media_ref = request.input_payload["analyzed_media_refs"][0]
        calls.append(list(request.input_payload["analyzed_media_refs"]))
        if media_ref.endswith(":pdf:2:000"):
            payload: dict[str, Any] = {
                "schema_version": "business_source_learning_output.v1",
                "catalog_candidates": [],
                "memory_candidates": [
                    {
                        "schema_version": "business_source_memory_candidate.v1",
                        "fact_id": "knowledge:empty-pdf-page",
                        "fact_type": "knowledge_fact",
                        "entity_ref": "business:pdf",
                        "value": {},
                        "confidence": 0.8,
                        "risk_tier": "low",
                        "evidence_refs": [media_ref],
                    }
                ],
            }
        else:
            payload = {
                "schema_version": "business_source_learning_output.v1",
                "catalog_candidates": [],
                "memory_candidates": [
                    {
                        "schema_version": "business_source_memory_candidate.v1",
                        "fact_id": f"knowledge:pdf-page:{len(calls)}",
                        "fact_type": "knowledge_fact",
                        "entity_ref": "business:pdf",
                        "value": {
                            "topic": f"page {len(calls)}",
                            "answer": "Visible brochure detail.",
                        },
                        "confidence": 0.78,
                        "risk_tier": "low",
                        "evidence_refs": [media_ref],
                    }
                ],
            }
        return LLMProviderResponse(text=json.dumps(payload), model_used="test-model")

    artifact_store = SourceMediaArtifactStore(base_path=tmp_path / "source-media")
    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(
        repository=repository,
        media_artifact_store=artifact_store,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:batch",
            source_kind="pdf",
            source_payload={"file_name": "batch.pdf"},
            content_bytes=_multi_image_pdf_bytes(),
            correlation_id="corr-batch-ingest",
            idempotency_key="batch-ingest",
        )
    )
    service = BusinessMediaArtifactBatchLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
        media_artifact_store=artifact_store,
    )

    result = await service.learn_from_artifact_batches(
        BusinessMediaArtifactBatchLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:batch",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            chunk_size=1,
            correlation_id="corr-batch-learn",
            idempotency_key="batch-learn",
        )
    )
    accepted_1 = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:pdf-page:1",
    )
    empty = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:empty-pdf-page",
    )
    accepted_3 = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:pdf-page:3",
    )

    assert calls == [[asset.media_ref] for asset in source.media_assets]
    assert result.chunk_count == 3
    assert result.completed_chunk_count == 3
    assert result.memory_candidate_count == 2
    assert result.catalog_candidate_count == 0
    assert result.loaded_media_refs == [asset.media_ref for asset in source.media_assets]
    assert result.missing_artifact_refs == []
    assert result.rejected_candidates == [
        {
            "candidate_ref": "knowledge:empty-pdf-page",
            "candidate_type": "knowledge_fact",
            "reason": "empty_candidate_value",
            "unsupported_refs": [],
        }
    ]
    assert result.degraded_reasons == ["empty_candidate_value"]
    assert accepted_1 is not None
    assert empty is None
    assert accepted_3 is not None


async def test_media_artifact_batch_learning_continues_after_degraded_chunk(
    db_session: AsyncSession,
    workspace: Workspace,
    tmp_path,
) -> None:
    calls = 0

    async def provider(request) -> LLMProviderResponse:
        nonlocal calls
        calls += 1
        media_ref = request.input_payload["analyzed_media_refs"][0]
        if calls == 1:
            raise TimeoutError("simulated media chunk timeout")
        payload: dict[str, Any] = {
            "schema_version": "business_source_learning_output.v1",
            "catalog_candidates": [],
            "memory_candidates": [
                {
                    "schema_version": "business_source_memory_candidate.v1",
                    "fact_id": "knowledge:recovered-after-timeout",
                    "fact_type": "knowledge_fact",
                    "entity_ref": "business:pdf",
                    "value": {
                        "topic": "recovered page",
                        "answer": "Later page still learned.",
                    },
                    "confidence": 0.76,
                    "risk_tier": "low",
                    "evidence_refs": [media_ref],
                }
            ],
        }
        return LLMProviderResponse(text=json.dumps(payload), model_used="test-model")

    artifact_store = SourceMediaArtifactStore(base_path=tmp_path / "source-media")
    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(
        repository=repository,
        media_artifact_store=artifact_store,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:recover",
            source_kind="pdf",
            source_payload={"file_name": "recover.pdf"},
            content_bytes=_multi_image_pdf_bytes(),
            correlation_id="corr-recover-ingest",
            idempotency_key="recover-ingest",
        )
    )
    service = BusinessMediaArtifactBatchLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
        media_artifact_store=artifact_store,
    )

    result = await service.learn_from_artifact_batches(
        BusinessMediaArtifactBatchLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:recover",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            media_refs=[asset.media_ref for asset in source.media_assets[:2]],
            chunk_size=1,
            correlation_id="corr-recover-learn",
            idempotency_key="recover-learn",
        )
    )
    recovered = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:recovered-after-timeout",
    )

    assert calls == 2
    assert result.chunk_count == 2
    assert result.completed_chunk_count == 2
    assert result.chunks[0].gateway_status == "timeout"
    assert result.chunks[1].gateway_status == "ok"
    assert result.memory_candidate_count == 1
    assert result.degraded_reasons == ["provider_timeout"]
    assert recovered is not None


async def test_media_artifact_batch_learning_defers_assets_beyond_preview_cap(
    db_session: AsyncSession,
    workspace: Workspace,
    tmp_path,
) -> None:
    calls: list[str] = []

    async def provider(request) -> LLMProviderResponse:
        media_ref = request.input_payload["analyzed_media_refs"][0]
        calls.append(media_ref)
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "schema_version": "business_source_memory_candidate.v1",
                            "fact_id": f"knowledge:preview:{len(calls)}",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:pdf",
                            "value": {
                                "topic": "preview page",
                                "answer": "Preview page learned.",
                            },
                            "confidence": 0.76,
                            "risk_tier": "low",
                            "evidence_refs": [media_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    artifact_store = SourceMediaArtifactStore(base_path=tmp_path / "source-media")
    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(
        repository=repository,
        media_artifact_store=artifact_store,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:preview-cap",
            source_kind="pdf",
            source_payload={"file_name": "preview-cap.pdf"},
            content_bytes=_multi_image_pdf_bytes(),
            correlation_id="corr-preview-cap-ingest",
            idempotency_key="preview-cap-ingest",
        )
    )
    service = BusinessMediaArtifactBatchLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
        media_artifact_store=artifact_store,
    )

    result = await service.learn_from_artifact_batches(
        BusinessMediaArtifactBatchLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:preview-cap",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            media_refs=[asset.media_ref for asset in source.media_assets],
            max_media_assets=1,
            chunk_size=1,
            correlation_id="corr-preview-cap-learn",
            idempotency_key="preview-cap-learn",
        )
    )

    assert calls == [source.media_assets[0].media_ref]
    assert result.chunk_count == 1
    assert result.completed_chunk_count == 1
    assert result.memory_candidate_count == 1
    assert result.loaded_media_refs == [source.media_assets[0].media_ref]
    assert result.deferred_media_refs == [
        asset.media_ref for asset in source.media_assets[1:]
    ]
    assert result.degraded_reasons == ["media_assets_deferred"]


async def test_media_artifact_batch_learning_resumes_deferred_assets(
    db_session: AsyncSession,
    workspace: Workspace,
    tmp_path,
) -> None:
    calls: list[str] = []

    async def provider(request) -> LLMProviderResponse:
        media_ref = request.input_payload["analyzed_media_refs"][0]
        calls.append(media_ref)
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "schema_version": "business_source_memory_candidate.v1",
                            "fact_id": f"knowledge:resume:{len(calls)}",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:pdf",
                            "value": {
                                "topic": "resumed page",
                                "answer": "Deferred page learned.",
                            },
                            "confidence": 0.76,
                            "risk_tier": "low",
                            "evidence_refs": [media_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    artifact_store = SourceMediaArtifactStore(base_path=tmp_path / "source-media")
    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(
        repository=repository,
        media_artifact_store=artifact_store,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:resume",
            source_kind="pdf",
            source_payload={"file_name": "resume.pdf"},
            content_bytes=_multi_image_pdf_bytes(),
            correlation_id="corr-resume-ingest",
            idempotency_key="resume-ingest",
        )
    )
    service = BusinessMediaArtifactBatchLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
        media_artifact_store=artifact_store,
    )

    preview = await service.learn_from_artifact_batches(
        BusinessMediaArtifactBatchLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:resume",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            media_refs=[asset.media_ref for asset in source.media_assets],
            max_media_assets=1,
            chunk_size=1,
            correlation_id="corr-resume-preview",
            idempotency_key="resume-preview",
        )
    )
    projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref=f"business_media_deferred:{source.source_fact_id}",
    )

    assert preview.loaded_media_refs == [source.media_assets[0].media_ref]
    assert projection is not None
    assert projection.degraded is True
    assert projection.state["pending_media_refs"] == [
        asset.media_ref for asset in source.media_assets[1:]
    ]
    assert projection.state["processed_media_refs"] == [source.media_assets[0].media_ref]

    resumed = await service.learn_from_deferred_artifact_batches(
        BusinessMediaDeferredBatchLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:resume",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            max_media_assets=2,
            chunk_size=1,
            correlation_id="corr-resume-deferred",
            idempotency_key="resume-deferred",
        )
    )
    projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref=f"business_media_deferred:{source.source_fact_id}",
    )

    assert resumed.loaded_media_refs == [
        asset.media_ref for asset in source.media_assets[1:]
    ]
    assert resumed.deferred_media_refs == []
    assert projection is not None
    assert projection.degraded is False
    assert projection.degraded_reasons == []
    assert projection.state["state"] == "completed"
    assert projection.state["pending_media_refs"] == []
    assert projection.state["processed_media_refs"] == [
        asset.media_ref for asset in source.media_assets
    ]
    assert calls == [asset.media_ref for asset in source.media_assets]


async def test_media_artifact_batch_learning_runs_chunks_in_bounded_parallel(
    db_session: AsyncSession,
    workspace: Workspace,
    tmp_path,
) -> None:
    active_calls = 0
    max_active_calls = 0
    started: list[str] = []

    async def provider(request) -> LLMProviderResponse:
        nonlocal active_calls, max_active_calls
        media_ref = request.input_payload["analyzed_media_refs"][0]
        started.append(media_ref)
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0.05)
        active_calls -= 1
        payload: dict[str, Any] = {
            "schema_version": "business_source_learning_output.v1",
            "catalog_candidates": [],
            "memory_candidates": [
                {
                    "schema_version": "business_source_memory_candidate.v1",
                    "fact_id": f"knowledge:parallel:{media_ref.split(':')[-2]}",
                    "fact_type": "knowledge_fact",
                    "entity_ref": "business:pdf",
                    "value": {
                        "topic": "parallel page",
                        "answer": "Parallel chunk learned.",
                    },
                    "confidence": 0.76,
                    "risk_tier": "low",
                    "evidence_refs": [media_ref],
                }
            ],
        }
        return LLMProviderResponse(text=json.dumps(payload), model_used="test-model")

    artifact_store = SourceMediaArtifactStore(base_path=tmp_path / "source-media")
    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(
        repository=repository,
        media_artifact_store=artifact_store,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:parallel",
            source_kind="pdf",
            source_payload={"file_name": "parallel.pdf"},
            content_bytes=_multi_image_pdf_bytes(),
            correlation_id="corr-parallel-ingest",
            idempotency_key="parallel-ingest",
        )
    )
    service = BusinessMediaArtifactBatchLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
        media_artifact_store=artifact_store,
    )

    result = await service.learn_from_artifact_batches(
        BusinessMediaArtifactBatchLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:parallel",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            media_refs=[asset.media_ref for asset in source.media_assets],
            chunk_size=1,
            max_parallel_chunks=2,
            correlation_id="corr-parallel-learn",
            idempotency_key="parallel-learn",
        )
    )

    assert result.chunk_count == 3
    assert result.completed_chunk_count == 3
    assert result.memory_candidate_count == 3
    assert max_active_calls == 2
    assert result.chunks == sorted(result.chunks, key=lambda chunk: chunk.chunk_index)
    assert [chunk.requested_media_refs[0] for chunk in result.chunks] == [
        asset.media_ref for asset in source.media_assets
    ]
    assert set(started) == {asset.media_ref for asset in source.media_assets}

    trace_count = len(
        (
            await db_session.execute(
                select(LLMGatewayTraceRecord).where(
                    LLMGatewayTraceRecord.correlation_id.like(
                        "corr-parallel-learn:media-chunk:%"
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    assert trace_count == 3


async def test_media_semantic_learning_rejects_model_output_with_fake_media_ref(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(_request) -> LLMProviderResponse:
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "product_ref": "catalog_product:fake",
                            "product": {
                                "title": "Fake",
                                "identity_ref": "catalog_product:fake",
                            },
                            "variants": [],
                            "offers": [],
                            "media": [],
                            "source_fact": {
                                "source_ref": "onboarding:pdf:scan",
                                "source_type": "pdf",
                                "content_refs": ["source_media:fake"],
                            },
                            "confidence": 0.9,
                            "risk_tier": "low",
                            "evidence_refs": ["source_media:fake"],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:scan",
            source_kind="pdf",
            source_payload={"file_name": "scan.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-fake-media-ingest",
            idempotency_key="fake-media-ingest",
        )
    )
    service = BusinessMediaSemanticLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await service.learn_from_media(
        BusinessMediaSemanticLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:scan",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            media_inputs=[
                BusinessMediaInput(
                    media_ref=source.media_assets[0].media_ref,
                    mime_type="image/jpeg",
                    data_base64=base64.b64encode(_image_bytes()).decode("ascii"),
                )
            ],
            correlation_id="corr-fake-media-learn",
            idempotency_key="fake-media-learn",
        )
    )
    product = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:fake",
    )

    assert result.source_learning.catalog_candidate_count == 0
    assert result.degraded_reasons == ["unsupported_evidence_refs"]
    assert result.source_learning.rejected_candidates == [
        {
            "candidate_ref": "catalog_product:fake",
            "candidate_type": "catalog_product",
            "reason": "unsupported_evidence_refs",
            "unsupported_refs": ["source_media:fake"],
        }
    ]
    assert product is None


async def test_media_semantic_learning_blocks_when_media_content_is_unavailable(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(_request) -> LLMProviderResponse:
        raise AssertionError("provider should not be called without media bytes or file URI")

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:scan",
            source_kind="pdf",
            source_payload={"file_name": "scan.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-no-content-ingest",
            idempotency_key="no-content-ingest",
        )
    )
    service = BusinessMediaSemanticLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await service.learn_from_media(
        BusinessMediaSemanticLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:scan",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            media_inputs=[
                BusinessMediaInput(
                    media_ref=source.media_assets[0].media_ref,
                    mime_type="image/jpeg",
                )
            ],
            correlation_id="corr-no-content-learn",
            idempotency_key="no-content-learn",
        )
    )

    assert result.gateway_status == "blocked"
    assert result.analyzed_media_refs == []
    assert result.degraded_reasons == ["media_content_unavailable"]
