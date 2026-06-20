from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.brain.prompt_registry import get_prompt_registry
from app.modules.business_brain.source_learning import (
    BusinessSourceLearningOutput,
    BusinessSourceLearningRequest,
    BusinessSourceLearningResult,
    BusinessSourceLearningService,
)
from app.modules.business_brain.source_media_artifacts import SourceMediaArtifactStore
from app.modules.commercial_spine.contracts import (
    BusinessBrainProjection,
    GatewayStatus,
    LLMGatewayRequest,
    LLMGatewayResult,
    LLMGatewayTrace,
)
from app.modules.commercial_spine.llm_gateway import LLMGateway
from app.modules.commercial_spine.repository import CommercialSpineRepository

SourceKind = Literal["website", "pdf", "text", "telegram_channel", "screenshot"]


class MediaLearningModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusinessMediaInput(MediaLearningModel):
    schema_version: Literal["business_media_input.v1"] = "business_media_input.v1"
    media_ref: str = Field(min_length=1)
    mime_type: str = Field(default="image/jpeg", min_length=1)
    data_base64: str | None = Field(default=None, exclude=True)
    file_uri: str | None = None
    url: str | None = None
    page_number: int | None = Field(default=None, ge=1)


class BusinessMediaSemanticLearningRequest(MediaLearningModel):
    schema_version: Literal["business_media_semantic_learning_request.v1"] = (
        "business_media_semantic_learning_request.v1"
    )
    workspace_id: int = Field(gt=0)
    source_ref: str = Field(min_length=1)
    source_kind: SourceKind
    source_fact_id: str = Field(min_length=1)
    media_inputs: list[BusinessMediaInput] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    route_key: str = "media_rich"
    prompt_id: str = "business_brain.media_semantic_learning"
    prompt_version: str = "1.0.0"


class BusinessMediaSemanticLearningResult(MediaLearningModel):
    schema_version: Literal["business_media_semantic_learning_result.v1"] = (
        "business_media_semantic_learning_result.v1"
    )
    gateway_status: GatewayStatus
    analyzed_media_refs: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    source_learning: BusinessSourceLearningResult


class BusinessMediaArtifactLearningRequest(MediaLearningModel):
    schema_version: Literal["business_media_artifact_learning_request.v1"] = (
        "business_media_artifact_learning_request.v1"
    )
    workspace_id: int = Field(gt=0)
    source_ref: str = Field(min_length=1)
    source_kind: SourceKind
    source_fact_id: str = Field(min_length=1)
    media_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    route_key: str = "media_rich"
    prompt_id: str = "business_brain.media_semantic_learning"
    prompt_version: str = "1.0.0"


class BusinessMediaArtifactLearningResult(MediaLearningModel):
    schema_version: Literal["business_media_artifact_learning_result.v1"] = (
        "business_media_artifact_learning_result.v1"
    )
    loaded_media_refs: list[str] = Field(default_factory=list)
    missing_artifact_refs: list[str] = Field(default_factory=list)
    semantic_learning: BusinessMediaSemanticLearningResult


class BusinessMediaArtifactBatchLearningRequest(MediaLearningModel):
    schema_version: Literal["business_media_artifact_batch_learning_request.v1"] = (
        "business_media_artifact_batch_learning_request.v1"
    )
    workspace_id: int = Field(gt=0)
    source_ref: str = Field(min_length=1)
    source_kind: SourceKind
    source_fact_id: str = Field(min_length=1)
    media_refs: list[str] = Field(default_factory=list)
    chunk_size: int = Field(default=1, ge=1, le=5)
    max_media_assets: int = Field(default=40, ge=1, le=100)
    max_parallel_chunks: int = Field(default=1, ge=1, le=8)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    route_key: str = "media_rich"
    prompt_id: str = "business_brain.media_semantic_learning"
    prompt_version: str = "1.0.0"


class BusinessMediaDeferredBatchLearningRequest(MediaLearningModel):
    schema_version: Literal["business_media_deferred_batch_learning_request.v1"] = (
        "business_media_deferred_batch_learning_request.v1"
    )
    workspace_id: int = Field(gt=0)
    source_ref: str = Field(min_length=1)
    source_kind: SourceKind
    source_fact_id: str = Field(min_length=1)
    max_media_assets: int = Field(default=2, ge=1, le=100)
    chunk_size: int = Field(default=1, ge=1, le=5)
    max_parallel_chunks: int = Field(default=1, ge=1, le=8)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    route_key: str = "media_rich"
    prompt_id: str = "business_brain.media_semantic_learning"
    prompt_version: str = "1.0.0"


class BusinessMediaArtifactBatchChunkResult(MediaLearningModel):
    schema_version: Literal["business_media_artifact_batch_chunk_result.v1"] = (
        "business_media_artifact_batch_chunk_result.v1"
    )
    chunk_index: int = Field(ge=0)
    requested_media_refs: list[str] = Field(default_factory=list)
    loaded_media_refs: list[str] = Field(default_factory=list)
    missing_artifact_refs: list[str] = Field(default_factory=list)
    gateway_status: GatewayStatus
    catalog_candidate_count: int = Field(ge=0)
    memory_candidate_count: int = Field(ge=0)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)


class BusinessMediaArtifactBatchLearningResult(MediaLearningModel):
    schema_version: Literal["business_media_artifact_batch_learning_result.v1"] = (
        "business_media_artifact_batch_learning_result.v1"
    )
    chunk_count: int = Field(ge=0)
    completed_chunk_count: int = Field(ge=0)
    loaded_media_refs: list[str] = Field(default_factory=list)
    missing_artifact_refs: list[str] = Field(default_factory=list)
    deferred_media_refs: list[str] = Field(default_factory=list)
    catalog_candidate_count: int = Field(ge=0)
    memory_candidate_count: int = Field(ge=0)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    chunks: list[BusinessMediaArtifactBatchChunkResult] = Field(default_factory=list)


@dataclass(slots=True)
class _MediaSemanticExtraction:
    request: BusinessMediaSemanticLearningRequest
    source_request: BusinessSourceLearningRequest
    gateway_result: LLMGatewayResult | None
    gateway_trace: LLMGatewayTrace | None
    analyzed_media_refs: list[str]
    degraded_reasons: list[str]


@dataclass(slots=True)
class _MediaArtifactExtraction:
    loaded_media_refs: list[str]
    missing_artifact_refs: list[str]
    semantic_extraction: _MediaSemanticExtraction


@dataclass(slots=True)
class _MediaArtifactBatchExtraction:
    chunk_index: int
    requested_media_refs: list[str]
    artifact_extraction: _MediaArtifactExtraction


class BusinessMediaSemanticLearningService:
    """Runs multimodal extraction, then uses the source-learning evidence gate."""

    def __init__(
        self,
        *,
        repository: CommercialSpineRepository,
        gateway: LLMGateway,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._source_learning = BusinessSourceLearningService(
            repository=repository,
            gateway=gateway,
        )

    async def learn_from_media(
        self,
        request: BusinessMediaSemanticLearningRequest,
    ) -> BusinessMediaSemanticLearningResult:
        extraction = await self.extract_from_media_detached(request)
        return await self.apply_media_extraction(extraction)

    async def extract_from_media_detached(
        self,
        request: BusinessMediaSemanticLearningRequest,
    ) -> _MediaSemanticExtraction:
        source_fact = await self._repository.get_fact(
            workspace_id=request.workspace_id,
            fact_id=request.source_fact_id,
        )
        if source_fact is None:
            raise ValueError("business source fact not found")

        source_request = _source_learning_request(request)
        instruction = _media_semantic_learning_instruction(request)
        content_parts, analyzed_refs, degraded = _media_content_parts(
            request=request,
            source_value=source_fact.value,
            instruction=instruction,
        )
        if not analyzed_refs:
            return _MediaSemanticExtraction(
                request=request,
                source_request=source_request,
                gateway_result=None,
                gateway_trace=None,
                analyzed_media_refs=[],
                degraded_reasons=_unique(["media_content_unavailable", *degraded]),
            )

        gateway, trace = await self._gateway.generate_detached(
            LLMGatewayRequest(
                route_key=request.route_key,
                workflow_name="business_media_semantic_learning",
                prompt_id=request.prompt_id,
                prompt_version=request.prompt_version,
                input_payload={
                    "source_ref": request.source_ref,
                    "source_kind": request.source_kind,
                    "source_fact_id": request.source_fact_id,
                    "media_inputs": [
                        _media_input_metadata(item) for item in request.media_inputs
                    ],
                    "analyzed_media_refs": analyzed_refs,
                    "instruction": instruction,
                },
                content_parts=content_parts,
                output_schema_name="BusinessSourceLearningOutput",
                workspace_id=request.workspace_id,
                correlation_id=request.correlation_id,
                source_refs=[request.source_ref, request.source_fact_id, *analyzed_refs],
            ),
            output_model=BusinessSourceLearningOutput,
        )
        return _MediaSemanticExtraction(
            request=request,
            source_request=source_request,
            gateway_result=gateway,
            gateway_trace=trace,
            analyzed_media_refs=analyzed_refs,
            degraded_reasons=degraded,
        )

    async def apply_media_extraction(
        self,
        extraction: _MediaSemanticExtraction,
    ) -> BusinessMediaSemanticLearningResult:
        if extraction.gateway_trace is not None:
            await self._repository.persist_llm_trace(extraction.gateway_trace)

        gateway = extraction.gateway_result
        if gateway is None:
            source_result = await self._source_learning.apply_learning_output(
                request=extraction.source_request,
                output=BusinessSourceLearningOutput(),
                gateway_status="blocked",
                extra_degraded_reasons=extraction.degraded_reasons,
            )
            return BusinessMediaSemanticLearningResult(
                gateway_status="blocked",
                analyzed_media_refs=[],
                degraded_reasons=source_result.degraded_reasons,
                source_learning=source_result,
            )
        if gateway.status != "ok" or gateway.parsed_output is None:
            source_result = await self._source_learning.apply_learning_output(
                request=extraction.source_request,
                output=BusinessSourceLearningOutput(),
                gateway_status=gateway.status,
                extra_degraded_reasons=list(gateway.validation_errors or [gateway.status]),
            )
            return BusinessMediaSemanticLearningResult(
                gateway_status=gateway.status,
                analyzed_media_refs=extraction.analyzed_media_refs,
                degraded_reasons=source_result.degraded_reasons,
                source_learning=source_result,
            )

        source_result = await self._source_learning.apply_learning_output(
            request=extraction.source_request,
            output=BusinessSourceLearningOutput.model_validate(gateway.parsed_output),
            gateway_status=gateway.status,
            extra_degraded_reasons=extraction.degraded_reasons,
        )
        return BusinessMediaSemanticLearningResult(
            gateway_status=gateway.status,
            analyzed_media_refs=extraction.analyzed_media_refs,
            degraded_reasons=source_result.degraded_reasons,
            source_learning=source_result,
        )


class BusinessMediaArtifactLearningService:
    """Loads persisted source media artifacts and delegates semantic learning."""

    def __init__(
        self,
        *,
        repository: CommercialSpineRepository,
        gateway: LLMGateway,
        media_artifact_store: SourceMediaArtifactStore,
    ) -> None:
        self._repository = repository
        self._media_artifact_store = media_artifact_store
        self._semantic_learning = BusinessMediaSemanticLearningService(
            repository=repository,
            gateway=gateway,
        )

    async def learn_from_artifacts(
        self,
        request: BusinessMediaArtifactLearningRequest,
    ) -> BusinessMediaArtifactLearningResult:
        extraction = await self.extract_from_artifacts_detached(request)
        return await self.apply_artifact_extraction(extraction)

    async def extract_from_artifacts_detached(
        self,
        request: BusinessMediaArtifactLearningRequest,
    ) -> _MediaArtifactExtraction:
        source_fact = await self._repository.get_fact(
            workspace_id=request.workspace_id,
            fact_id=request.source_fact_id,
        )
        if source_fact is None:
            raise ValueError("business source fact not found")

        media_assets = _source_media_asset_lookup(source_fact.value)
        requested_refs = request.media_refs or [
            media_ref
            for media_ref, asset in media_assets.items()
            if str(asset.get("artifact_ref") or "").strip()
        ]
        media_inputs: list[BusinessMediaInput] = []
        loaded_refs: list[str] = []
        missing_refs: list[str] = []
        for media_ref in requested_refs:
            asset = media_assets.get(media_ref)
            if asset is None:
                missing_refs.append(media_ref)
                continue
            artifact_ref = str(asset.get("artifact_ref") or "").strip()
            if not artifact_ref:
                missing_refs.append(media_ref)
                continue
            stored = await self._media_artifact_store.read(
                artifact_ref=artifact_ref,
                workspace_id=request.workspace_id,
            )
            if stored is None:
                missing_refs.append(media_ref)
                continue
            media_inputs.append(
                BusinessMediaInput(
                    media_ref=media_ref,
                    mime_type=(
                        stored.content_type
                        or str(asset.get("content_type") or "").strip()
                        or "application/octet-stream"
                    ),
                    data_base64=base64.b64encode(stored.content_bytes).decode("ascii"),
                    url=_optional_string(asset.get("url")),
                    page_number=_optional_int(asset.get("page_number")),
                )
            )
            loaded_refs.append(media_ref)

        if not media_inputs:
            fallback_refs = requested_refs or list(media_assets)
            if not fallback_refs:
                raise ValueError("no source media assets available")
            media_inputs = [
                BusinessMediaInput(
                    media_ref=media_ref,
                    mime_type=(
                        str(media_assets.get(media_ref, {}).get("content_type") or "").strip()
                        or "application/octet-stream"
                    ),
                    url=_optional_string(media_assets.get(media_ref, {}).get("url")),
                    page_number=_optional_int(
                        media_assets.get(media_ref, {}).get("page_number")
                    ),
                )
                for media_ref in fallback_refs
            ]

        semantic_extraction = await self._semantic_learning.extract_from_media_detached(
            BusinessMediaSemanticLearningRequest(
                workspace_id=request.workspace_id,
                source_ref=request.source_ref,
                source_kind=request.source_kind,
                source_fact_id=request.source_fact_id,
                media_inputs=media_inputs,
                correlation_id=request.correlation_id,
                idempotency_key=request.idempotency_key,
                route_key=request.route_key,
                prompt_id=request.prompt_id,
                prompt_version=request.prompt_version,
            )
        )
        return _MediaArtifactExtraction(
            loaded_media_refs=_unique(loaded_refs),
            missing_artifact_refs=_unique(missing_refs),
            semantic_extraction=semantic_extraction,
        )

    async def apply_artifact_extraction(
        self,
        extraction: _MediaArtifactExtraction,
    ) -> BusinessMediaArtifactLearningResult:
        semantic_result = await self._semantic_learning.apply_media_extraction(
            extraction.semantic_extraction
        )
        return BusinessMediaArtifactLearningResult(
            loaded_media_refs=extraction.loaded_media_refs,
            missing_artifact_refs=extraction.missing_artifact_refs,
            semantic_learning=semantic_result,
        )


class BusinessMediaArtifactBatchLearningService:
    """Runs artifact media learning in small chunks for retryable workers."""

    def __init__(
        self,
        *,
        repository: CommercialSpineRepository,
        gateway: LLMGateway,
        media_artifact_store: SourceMediaArtifactStore,
    ) -> None:
        self._repository = repository
        self._artifact_learning = BusinessMediaArtifactLearningService(
            repository=repository,
            gateway=gateway,
            media_artifact_store=media_artifact_store,
        )

    async def learn_from_artifact_batches(
        self,
        request: BusinessMediaArtifactBatchLearningRequest,
    ) -> BusinessMediaArtifactBatchLearningResult:
        source_fact = await self._repository.get_fact(
            workspace_id=request.workspace_id,
            fact_id=request.source_fact_id,
        )
        if source_fact is None:
            raise ValueError("business source fact not found")

        media_assets = _source_media_asset_lookup(source_fact.value)
        all_ordered_refs = _ordered_media_refs(
            media_assets=media_assets,
            requested_refs=request.media_refs,
        )
        ordered_refs = all_ordered_refs[: request.max_media_assets]
        deferred_refs = all_ordered_refs[request.max_media_assets :]
        chunk_inputs = list(enumerate(_chunk_values(ordered_refs, request.chunk_size)))
        semaphore = asyncio.Semaphore(request.max_parallel_chunks)

        async def learn_chunk(
            chunk_index: int,
            media_refs: list[str],
        ) -> _MediaArtifactBatchExtraction:
            async with semaphore:
                artifact_extraction = (
                    await self._artifact_learning.extract_from_artifacts_detached(
                        BusinessMediaArtifactLearningRequest(
                            workspace_id=request.workspace_id,
                            source_ref=request.source_ref,
                            source_kind=request.source_kind,
                            source_fact_id=request.source_fact_id,
                            media_refs=media_refs,
                            correlation_id=(
                                f"{request.correlation_id}:media-chunk:{chunk_index:03d}"
                            ),
                            idempotency_key=(
                                f"{request.idempotency_key}:media-chunk:{chunk_index:03d}"
                            ),
                            route_key=request.route_key,
                            prompt_id=request.prompt_id,
                            prompt_version=request.prompt_version,
                        )
                    )
                )
                return _MediaArtifactBatchExtraction(
                    chunk_index=chunk_index,
                    requested_media_refs=list(media_refs),
                    artifact_extraction=artifact_extraction,
                )

        extractions = sorted(
            await asyncio.gather(
                *[
                    learn_chunk(chunk_index, media_refs)
                    for chunk_index, media_refs in chunk_inputs
                ]
            ),
            key=lambda chunk: chunk.chunk_index,
        )

        chunks: list[BusinessMediaArtifactBatchChunkResult] = []
        for extraction in extractions:
            chunk_result = await self._artifact_learning.apply_artifact_extraction(
                extraction.artifact_extraction
            )
            source_result = chunk_result.semantic_learning.source_learning
            chunks.append(
                BusinessMediaArtifactBatchChunkResult(
                    chunk_index=extraction.chunk_index,
                    requested_media_refs=list(extraction.requested_media_refs),
                    loaded_media_refs=list(chunk_result.loaded_media_refs),
                    missing_artifact_refs=list(chunk_result.missing_artifact_refs),
                    gateway_status=chunk_result.semantic_learning.gateway_status,
                    catalog_candidate_count=source_result.catalog_candidate_count,
                    memory_candidate_count=source_result.memory_candidate_count,
                    rejected_candidates=list(source_result.rejected_candidates),
                    degraded_reasons=list(source_result.degraded_reasons),
                )
            )

        loaded_refs: list[str] = []
        missing_refs: list[str] = []
        rejected: list[dict[str, Any]] = []
        degraded: list[str] = []
        catalog_count = 0
        memory_count = 0
        for chunk in chunks:
            loaded_refs.extend(chunk.loaded_media_refs)
            missing_refs.extend(chunk.missing_artifact_refs)
            rejected.extend(chunk.rejected_candidates)
            degraded.extend(chunk.degraded_reasons)
            catalog_count += chunk.catalog_candidate_count
            memory_count += chunk.memory_candidate_count
        if deferred_refs:
            degraded.append("media_assets_deferred")

        result = BusinessMediaArtifactBatchLearningResult(
            chunk_count=len(chunks),
            completed_chunk_count=len(chunks),
            loaded_media_refs=_unique(loaded_refs),
            missing_artifact_refs=_unique(missing_refs),
            deferred_media_refs=_unique(deferred_refs),
            catalog_candidate_count=catalog_count,
            memory_candidate_count=memory_count,
            rejected_candidates=rejected,
            degraded_reasons=_unique(degraded),
            chunks=chunks,
        )
        await self._upsert_deferred_projection(
            request=request,
            pending_media_refs=result.deferred_media_refs,
            processed_media_refs=result.loaded_media_refs,
            last_result=result,
        )
        return result

    async def learn_from_deferred_artifact_batches(
        self,
        request: BusinessMediaDeferredBatchLearningRequest,
    ) -> BusinessMediaArtifactBatchLearningResult:
        projection = await self._repository.get_projection(
            workspace_id=request.workspace_id,
            projection_ref=_deferred_projection_ref(request.source_fact_id),
        )
        pending_refs = _projection_pending_media_refs(projection)
        if not pending_refs:
            return BusinessMediaArtifactBatchLearningResult(
                chunk_count=0,
                completed_chunk_count=0,
                loaded_media_refs=[],
                missing_artifact_refs=[],
                deferred_media_refs=[],
                catalog_candidate_count=0,
                memory_candidate_count=0,
                rejected_candidates=[],
                degraded_reasons=[],
                chunks=[],
            )
        return await self.learn_from_artifact_batches(
            BusinessMediaArtifactBatchLearningRequest(
                workspace_id=request.workspace_id,
                source_ref=request.source_ref,
                source_kind=request.source_kind,
                source_fact_id=request.source_fact_id,
                media_refs=pending_refs,
                chunk_size=request.chunk_size,
                max_media_assets=request.max_media_assets,
                max_parallel_chunks=request.max_parallel_chunks,
                correlation_id=request.correlation_id,
                idempotency_key=request.idempotency_key,
                route_key=request.route_key,
                prompt_id=request.prompt_id,
                prompt_version=request.prompt_version,
            )
        )

    async def _upsert_deferred_projection(
        self,
        *,
        request: BusinessMediaArtifactBatchLearningRequest,
        pending_media_refs: list[str],
        processed_media_refs: list[str],
        last_result: BusinessMediaArtifactBatchLearningResult,
    ) -> None:
        existing = await self._repository.get_projection(
            workspace_id=request.workspace_id,
            projection_ref=_deferred_projection_ref(request.source_fact_id),
        )
        previous_processed = (
            _projection_processed_media_refs(existing) if existing is not None else []
        )
        source_refs = _unique([request.source_ref, request.source_fact_id])
        projection = BusinessBrainProjection(
            projection_ref=_deferred_projection_ref(request.source_fact_id),
            workspace_id=request.workspace_id,
            projection_type="business_media_deferred_learning",
            entity_ref=request.source_fact_id,
            state={
                "source_ref": request.source_ref,
                "source_kind": request.source_kind,
                "source_fact_id": request.source_fact_id,
                "pending_media_refs": list(pending_media_refs),
                "processed_media_refs": _unique(
                    [*previous_processed, *processed_media_refs]
                ),
                "last_batch": {
                    "chunk_count": last_result.chunk_count,
                    "completed_chunk_count": last_result.completed_chunk_count,
                    "loaded_media_refs": list(last_result.loaded_media_refs),
                    "missing_artifact_refs": list(last_result.missing_artifact_refs),
                    "deferred_media_refs": list(last_result.deferred_media_refs),
                    "catalog_candidate_count": last_result.catalog_candidate_count,
                    "memory_candidate_count": last_result.memory_candidate_count,
                    "degraded_reasons": list(last_result.degraded_reasons),
                },
                "state": "pending" if pending_media_refs else "completed",
            },
            source_refs=source_refs,
            degraded=bool(pending_media_refs),
            degraded_reasons=["media_assets_deferred"] if pending_media_refs else [],
        )
        await self._repository.upsert_projection(projection)


def _source_learning_request(
    request: BusinessMediaSemanticLearningRequest,
) -> BusinessSourceLearningRequest:
    return BusinessSourceLearningRequest(
        workspace_id=request.workspace_id,
        source_ref=request.source_ref,
        source_kind=request.source_kind,
        source_fact_id=request.source_fact_id,
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        route_key="structured_fast",
        prompt_id="business_brain.source_learning",
        prompt_version="1.0.0",
    )


def _deferred_projection_ref(source_fact_id: str) -> str:
    return f"business_media_deferred:{source_fact_id}"


def _projection_pending_media_refs(
    projection: BusinessBrainProjection | None,
) -> list[str]:
    if projection is None:
        return []
    raw = projection.state.get("pending_media_refs")
    if not isinstance(raw, list):
        return []
    return _unique([str(item) for item in raw if str(item or "").strip()])


def _projection_processed_media_refs(
    projection: BusinessBrainProjection | None,
) -> list[str]:
    if projection is None:
        return []
    raw = projection.state.get("processed_media_refs")
    if not isinstance(raw, list):
        return []
    return _unique([str(item) for item in raw if str(item or "").strip()])


def _media_content_parts(
    *,
    request: BusinessMediaSemanticLearningRequest,
    source_value: dict[str, Any],
    instruction: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    source_media_refs = {
        str(item.get("media_ref"))
        for item in source_value.get("media_assets", [])
        if isinstance(item, dict) and item.get("media_ref")
    }
    content_parts: list[dict[str, Any]] = [
        {
            "kind": "text",
            "text": _media_learning_prompt(
                request=request,
                source_value=source_value,
                instruction=instruction,
            ),
        }
    ]
    analyzed_refs: list[str] = []
    degraded: list[str] = []
    for item in request.media_inputs:
        if item.media_ref not in source_media_refs:
            degraded.append("media_ref_not_in_source")
            continue
        if item.data_base64:
            content_parts.append(
                {
                    "kind": "inline_data",
                    "mime_type": item.mime_type,
                    "data_base64": item.data_base64,
                }
            )
            analyzed_refs.append(item.media_ref)
            continue
        if item.file_uri:
            content_parts.append(
                {
                    "kind": "file_uri",
                    "mime_type": item.mime_type,
                    "file_uri": item.file_uri,
                }
            )
            analyzed_refs.append(item.media_ref)
            continue
        degraded.append("media_content_unavailable")
    return content_parts, _unique(analyzed_refs), _unique(degraded)


def _media_learning_prompt(
    *,
    request: BusinessMediaSemanticLearningRequest,
    source_value: dict[str, Any],
    instruction: str,
) -> str:
    media_assets = [
        item
        for item in source_value.get("media_assets", [])
        if isinstance(item, dict)
    ]
    return "\n\n".join(
        [
            instruction.strip(),
            "Runtime context:",
            f"Source ref: {request.source_ref}",
            f"Source kind: {request.source_kind}",
            f"Media refs to inspect: {[item.media_ref for item in request.media_inputs]}",
            f"Known source media assets: {media_assets[:20]}",
        ]
    )


def _media_semantic_learning_instruction(
    request: BusinessMediaSemanticLearningRequest,
) -> str:
    return get_prompt_registry().load(
        request.prompt_id,
        version=request.prompt_version,
    ).body.strip()


def _media_input_metadata(item: BusinessMediaInput) -> dict[str, Any]:
    return {
        "media_ref": item.media_ref,
        "mime_type": item.mime_type,
        "file_uri": item.file_uri,
        "url": item.url,
        "page_number": item.page_number,
        "has_inline_data": bool(item.data_base64),
    }


def _source_media_asset_lookup(source_value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_assets = source_value.get("media_assets")
    if not isinstance(raw_assets, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        media_ref = str(item.get("media_ref") or "").strip()
        if media_ref:
            result[media_ref] = item
    return result


def _ordered_media_refs(
    *,
    media_assets: dict[str, dict[str, Any]],
    requested_refs: list[str],
) -> list[str]:
    if requested_refs:
        return _unique([ref for ref in requested_refs if ref in media_assets])
    ordered = sorted(
        media_assets.items(),
        key=lambda item: (
            _optional_int(item[1].get("page_number")) or 10**9,
            str(item[0]),
        ),
    )
    return [
        media_ref
        for media_ref, asset in ordered
        if str(asset.get("artifact_ref") or "").strip()
    ]


def _chunk_values(values: list[str], chunk_size: int) -> list[list[str]]:
    return [
        values[index : index + chunk_size]
        for index in range(0, len(values), chunk_size)
    ]


def _optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
