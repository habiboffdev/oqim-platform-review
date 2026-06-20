from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.brain.prompt_registry import PromptRegistryError, get_prompt_registry
from app.modules.business_brain.contracts import BusinessBrainFactUpdateInput
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.business_brain.write_service import BusinessBrainWriteService
from app.modules.commercial_spine.contracts import (
    BusinessBrainProjection,
    GatewayStatus,
    LLMGatewayRequest,
    RiskTier,
)
from app.modules.commercial_spine.llm_gateway import LLMGateway
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.extraction_runtime.adapters import (
    build_business_source_extraction_request,
)
from app.modules.extraction_runtime.contracts import (
    ExtractionCandidate,
    ProposeCandidatesRequest,
    RejectedExtractionCandidate,
)
from app.modules.extraction_runtime.persistence import (
    ExtractionCandidatePersistenceService,
)
from app.modules.extraction_runtime.runtime import (
    StaticCandidateProvider,
    UniversalExtractionRuntime,
)

SourceKind = Literal[
    "website",
    "pdf",
    "text",
    "telegram_channel",
    "screenshot",
    "voice_note",
    "spreadsheet",
    "past_conversation",
]
MemoryCandidateType = Literal[
    "knowledge_fact",
    "seller_rule_fact",
    "integration_intent_fact",
    "voice_fact",
    "conversation_pair_fact",
]
_SOURCE_LEARNING_INSTRUCTION = (
    "Extract only facts directly supported by allowed_evidence_refs. Every "
    "candidate must cite source unit refs or media refs from allowed_evidence_refs. "
    "If a detail is not supported, omit it."
)


class SourceLearningModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceLearningFactValue(BaseModel):
    model_config = ConfigDict(extra="allow")


class BusinessSourceCatalogProductValue(SourceLearningFactValue):
    title: str | None = None
    identity_ref: str | None = None
    category: str | None = None
    description: str | None = None
    sku: str | None = None
    material_codes: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class BusinessSourceCatalogVariantValue(SourceLearningFactValue):
    variant_ref: str | None = None
    product_ref: str | None = None
    title: str | None = None
    sku: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class BusinessSourceCatalogOfferValue(SourceLearningFactValue):
    offer_ref: str | None = None
    product_ref: str | None = None
    variant_ref: str | None = None
    price: dict[str, Any] | None = None
    stock: dict[str, Any] | None = None
    active: bool | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BusinessSourceCatalogMediaValue(SourceLearningFactValue):
    media_ref: str | None = None
    product_ref: str | None = None
    variant_ref: str | None = None
    source_media_ref: str | None = None
    media_type: str | None = None
    url: str | None = None
    quality_state: Literal[
        "product_media",
        "page_media_only",
        "source_link_only",
        "crop_pending",
    ] | None = None
    crop_state: Literal["not_needed", "pending", "ready", "failed"] | None = None
    approved: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class BusinessSourceCatalogSourceFactValue(SourceLearningFactValue):
    source_ref: str | None = None
    source_type: str | None = None
    content_refs: list[str] = Field(default_factory=list)
    extraction_state: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BusinessSourceLearningRequest(SourceLearningModel):
    schema_version: Literal["business_source_learning_request.v1"] = (
        "business_source_learning_request.v1"
    )
    workspace_id: int = Field(gt=0)
    source_ref: str = Field(min_length=1)
    source_kind: SourceKind
    source_fact_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    route_key: str = "structured_fast"
    prompt_id: str = "business_brain.source_learning"
    prompt_version: str = "1.0.0"
    max_source_units: int = Field(default=12, ge=1, le=50)
    max_media_assets: int = Field(default=40, ge=1, le=100)
    content_parts: list[dict[str, Any]] = Field(default_factory=list, exclude=True)


class BusinessSourceCatalogCandidate(SourceLearningModel):
    schema_version: Literal["business_source_catalog_candidate.v1"] = (
        "business_source_catalog_candidate.v1"
    )
    product_ref: str = Field(min_length=1)
    product: BusinessSourceCatalogProductValue
    variants: list[BusinessSourceCatalogVariantValue] = Field(default_factory=list)
    offers: list[BusinessSourceCatalogOfferValue] = Field(default_factory=list)
    media: list[BusinessSourceCatalogMediaValue] = Field(default_factory=list)
    source_fact: BusinessSourceCatalogSourceFactValue
    confidence: float = Field(ge=0.0, le=1.0)
    risk_tier: RiskTier
    evidence_refs: list[str] = Field(min_length=1)


class BusinessSourceMemoryValue(SourceLearningModel):
    model_config = ConfigDict(extra="allow")

    topic: str | None = None
    question: str | None = None
    answer: str | None = None
    summary: str | None = None
    requirement: str | None = None
    rule: str | None = None
    date: str | None = None
    contact: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)


class BusinessSourceMemoryCandidate(SourceLearningModel):
    schema_version: Literal["business_source_memory_candidate.v1"] = (
        "business_source_memory_candidate.v1"
    )
    fact_id: str = Field(min_length=1)
    fact_type: MemoryCandidateType
    entity_ref: str = Field(min_length=1)
    value: BusinessSourceMemoryValue
    confidence: float = Field(ge=0.0, le=1.0)
    risk_tier: RiskTier
    evidence_refs: list[str] = Field(min_length=1)


class BusinessSourceLearningOutput(SourceLearningModel):
    schema_version: Literal["business_source_learning_output.v1"] = (
        "business_source_learning_output.v1"
    )
    catalog_candidates: list[BusinessSourceCatalogCandidate] = Field(
        default_factory=list
    )
    memory_candidates: list[BusinessSourceMemoryCandidate] = Field(default_factory=list)


class BusinessSourceLearningResult(SourceLearningModel):
    schema_version: Literal["business_source_learning_result.v1"] = (
        "business_source_learning_result.v1"
    )
    gateway_status: GatewayStatus
    catalog_candidate_count: int = Field(ge=0)
    memory_candidate_count: int = Field(ge=0)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    evidence_summary: dict[str, int]
    projection: BusinessBrainProjection
    extraction_run_id: str | None = None
    extraction_candidate_count: int = Field(default=0, ge=0)
    extraction_proposal_refs: list[str] = Field(default_factory=list)


class BusinessSourceLearningService:
    """Turns ingested source evidence into proposed Business Brain facts.

    The LLM may propose product, FAQ/KB, rule, voice, and integration facts.
    This service only accepts candidates that cite evidence refs we actually
    ingested. Candidate rejection is deterministic source-ref validation, not a
    semantic classifier.
    """

    def __init__(
        self,
        *,
        repository: CommercialSpineRepository,
        gateway: LLMGateway,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._memory = BusinessBrainMemoryService(repository=repository)
        self._write = BusinessBrainWriteService(repository=repository)

    async def learn_from_source(
        self,
        request: BusinessSourceLearningRequest,
    ) -> BusinessSourceLearningResult:
        source_fact = await self._repository.get_fact(
            workspace_id=request.workspace_id,
            fact_id=request.source_fact_id,
        )
        if source_fact is None:
            raise ValueError("business source fact not found")

        source_units = await self._source_units(request)
        media_assets = _source_media_assets(source_fact.value)[: request.max_media_assets]
        evidence_summary = _evidence_summary(source_units, media_assets)
        allowed_refs = _allowed_evidence_refs(source_units, media_assets)
        if not allowed_refs:
            projection = await self._persist_projection(
                request=request,
                gateway_status="blocked",
                evidence_summary=evidence_summary,
                catalog_count=0,
                memory_count=0,
                rejected=[],
                degraded_reasons=["no_source_evidence"],
            )
            return BusinessSourceLearningResult(
                gateway_status="blocked",
                catalog_candidate_count=0,
                memory_candidate_count=0,
                rejected_candidates=[],
                degraded_reasons=["no_source_evidence"],
                evidence_summary=evidence_summary,
                projection=projection,
            )
        extraction_request = build_business_source_extraction_request(
            workspace_id=request.workspace_id,
            source_ref=request.source_ref,
            source_kind=request.source_kind,
            source_units=source_units,
            media_assets=media_assets,
            correlation_id=request.correlation_id,
            idempotency_key=request.idempotency_key,
            max_source_units=request.max_source_units,
            max_media_assets=request.max_media_assets,
        )
        allowed_refs = list(extraction_request.allowed_evidence_refs())
        structured_output = _structured_source_learning_output(
            request=request,
            source_fact_value=dict(source_fact.value),
            source_units=source_units,
            media_assets=media_assets,
        )
        if structured_output is not None:
            return await self.apply_learning_output(
                request=request,
                output=structured_output,
                gateway_status="ok",
            )

        gateway = await self._gateway.generate(
            LLMGatewayRequest(
                route_key=request.route_key,
                workflow_name="business_source_learning",
                prompt_id=request.prompt_id,
                prompt_version=request.prompt_version,
                input_payload={
                    "source_ref": request.source_ref,
                    "source_kind": request.source_kind,
                    "source_fact": {
                        "fact_id": source_fact.fact_id,
                        "value": dict(source_fact.value),
                        "source_refs": list(source_fact.source_refs),
                    },
                    "source_units": [_source_unit_payload(item) for item in source_units],
                    "media_assets": media_assets,
                    "allowed_evidence_refs": allowed_refs,
                    "extraction_request": extraction_request.model_dump(mode="json"),
                    "instruction": _source_learning_instruction(request),
                },
                output_schema_name="BusinessSourceLearningOutput",
                workspace_id=request.workspace_id,
                correlation_id=request.correlation_id,
                source_refs=[request.source_ref, request.source_fact_id],
                content_parts=list(request.content_parts),
            ),
            output_model=BusinessSourceLearningOutput,
        )
        if gateway.status != "ok" or gateway.parsed_output is None:
            degraded = list(gateway.validation_errors or [gateway.status])
            projection = await self._persist_projection(
                request=request,
                gateway_status=gateway.status,
                evidence_summary=evidence_summary,
                catalog_count=0,
                memory_count=0,
                rejected=[],
                degraded_reasons=degraded,
            )
            return BusinessSourceLearningResult(
                gateway_status=gateway.status,
                catalog_candidate_count=0,
                memory_candidate_count=0,
                rejected_candidates=[],
                degraded_reasons=degraded,
                evidence_summary=evidence_summary,
                projection=projection,
            )

        return await self.apply_learning_output(
            request=request,
            output=BusinessSourceLearningOutput.model_validate(gateway.parsed_output),
            gateway_status=gateway.status,
        )

    async def apply_learning_output(  # noqa: C901
        self,
        *,
        request: BusinessSourceLearningRequest,
        output: BusinessSourceLearningOutput,
        gateway_status: GatewayStatus = "ok",
        extra_degraded_reasons: list[str] | None = None,
    ) -> BusinessSourceLearningResult:
        source_fact = await self._repository.get_fact(
            workspace_id=request.workspace_id,
            fact_id=request.source_fact_id,
        )
        if source_fact is None:
            raise ValueError("business source fact not found")

        source_units = await self._source_units(request)
        media_assets = _source_media_assets(source_fact.value)[: request.max_media_assets]
        evidence_summary = _evidence_summary(source_units, media_assets)
        allowed_refs = _allowed_evidence_refs(source_units, media_assets)
        if not allowed_refs:
            projection = await self._persist_projection(
                request=request,
                gateway_status="blocked",
                evidence_summary=evidence_summary,
                catalog_count=0,
                memory_count=0,
                rejected=[],
                degraded_reasons=["no_source_evidence"],
            )
            return BusinessSourceLearningResult(
                gateway_status="blocked",
                catalog_candidate_count=0,
                memory_candidate_count=0,
                rejected_candidates=[],
                degraded_reasons=["no_source_evidence"],
                evidence_summary=evidence_summary,
                projection=projection,
            )
        extraction_request = build_business_source_extraction_request(
            workspace_id=request.workspace_id,
            source_ref=request.source_ref,
            source_kind=request.source_kind,
            source_units=source_units,
            media_assets=media_assets,
            correlation_id=request.correlation_id,
            idempotency_key=request.idempotency_key,
            max_source_units=request.max_source_units,
            max_media_assets=request.max_media_assets,
        )
        rejected: list[dict[str, Any]] = []
        extraction_candidates: list[ExtractionCandidate] = []
        pre_rejected_extraction_candidates: list[RejectedExtractionCandidate] = []
        catalog_candidates_by_id: dict[str, BusinessSourceCatalogCandidate] = {}
        memory_candidates_by_id: dict[
            str,
            tuple[BusinessSourceMemoryCandidate, dict[str, Any]],
        ] = {}
        catalog_count = 0
        memory_count = 0
        for raw_candidate in output.catalog_candidates:
            candidate = _normalized_catalog_candidate(
                raw_candidate,
                request=request,
            )
            validation_errors = _catalog_candidate_validation_errors(
                candidate,
                request=request,
            )
            if validation_errors:
                rejected.append(
                    _rejected_candidate(
                        candidate_ref=candidate.product_ref,
                        candidate_type="catalog_product",
                        reason="malformed_catalog_candidate",
                        unsupported_refs=[],
                        validation_errors=validation_errors,
                    )
                )
                pre_rejected_extraction_candidates.append(
                    _rejected_extraction_candidate(
                        candidate_ref=candidate.product_ref,
                        candidate_type="catalog_product",
                        reason="malformed_catalog_candidate",
                        unsupported_refs=[],
                        validation_errors=validation_errors,
                    )
                )
                continue
            extraction_candidate = _catalog_universal_candidate(
                candidate,
                request=request,
                source_fact_value=dict(source_fact.value),
            )
            extraction_candidates.append(extraction_candidate)
            catalog_candidates_by_id[extraction_candidate.candidate_id] = candidate

        for candidate in output.memory_candidates:
            value = _memory_value_payload(candidate.value)
            if not value:
                rejected.append(
                    _rejected_candidate(
                        candidate_ref=candidate.fact_id,
                        candidate_type=candidate.fact_type,
                        reason="empty_candidate_value",
                        unsupported_refs=[],
                    )
                )
                pre_rejected_extraction_candidates.append(
                    _rejected_extraction_candidate(
                        candidate_ref=candidate.fact_id,
                        candidate_type=candidate.fact_type,
                        reason="empty_candidate_value",
                        unsupported_refs=[],
                    )
                )
                continue
            extraction_candidate = _memory_universal_candidate(
                candidate,
                request=request,
                value=value,
            )
            extraction_candidates.append(extraction_candidate)
            memory_candidates_by_id[extraction_candidate.candidate_id] = (
                candidate,
                value,
            )

        extraction_result = None
        if extraction_candidates or pre_rejected_extraction_candidates:
            runtime = UniversalExtractionRuntime(
                candidate_provider=StaticCandidateProvider(extraction_candidates)
            )
            extraction_result = await runtime.extract(extraction_request)
            if pre_rejected_extraction_candidates:
                merged_rejected = [
                    *pre_rejected_extraction_candidates,
                    *extraction_result.rejected_candidates,
                ]
                extraction_result = extraction_result.model_copy(
                    update={
                        "status": "degraded",
                        "rejected_candidates": merged_rejected,
                        "degraded_reasons": _unique(
                            [
                                *extraction_result.degraded_reasons,
                                *[
                                    candidate.reason
                                    for candidate in pre_rejected_extraction_candidates
                                ],
                            ]
                        ),
                    }
                )
            pre_rejected_ids = {
                candidate.candidate_id
                for candidate in pre_rejected_extraction_candidates
            }
            for runtime_rejected in extraction_result.rejected_candidates:
                if runtime_rejected.candidate_id in pre_rejected_ids:
                    continue
                rejected.append(
                    _rejected_candidate_from_extraction(
                        runtime_rejected,
                        catalog_candidates_by_id=catalog_candidates_by_id,
                        memory_candidates_by_id=memory_candidates_by_id,
                    )
                )

            for accepted in extraction_result.accepted_candidates:
                catalog_candidate = catalog_candidates_by_id.get(accepted.candidate_id)
                if catalog_candidate is not None:
                    await self._write_catalog_family(
                        request=request,
                        candidate=catalog_candidate,
                        source_fact_value=dict(source_fact.value),
                    )
                    catalog_count += 1
                    continue
                memory_entry = memory_candidates_by_id.get(accepted.candidate_id)
                if memory_entry is None:
                    continue
                memory_candidate, value = memory_entry
                await self._memory.write_memory_fact(
                    MemoryFactWriteInput(
                        workspace_id=request.workspace_id,
                        fact_id=memory_candidate.fact_id,
                        fact_type=memory_candidate.fact_type,
                        entity_ref=memory_candidate.entity_ref,
                        value=value,
                        source_refs=_source_fact_refs(
                            request=request,
                            evidence_refs=memory_candidate.evidence_refs,
                        ),
                        source="ai_proposal",
                        status="proposed",
                        approval_state="proposed",
                        confidence=memory_candidate.confidence,
                        risk_tier=memory_candidate.risk_tier,
                        correlation_id=request.correlation_id,
                        idempotency_key=(
                            f"{request.idempotency_key}:{memory_candidate.fact_id}"
                        ),
                        actor_ref="business_source_learning",
                    )
                )
                memory_count += 1

        degraded = list(extra_degraded_reasons or [])
        rejected_reasons = _unique(
            [
                str(item.get("reason") or "rejected_candidate")
                for item in rejected
            ]
        )
        if rejected_reasons:
            degraded.extend(rejected_reasons)
        elif rejected:
            degraded.append("unsupported_evidence_refs")
        projection = await self._persist_projection(
            request=request,
            gateway_status=gateway_status,
            evidence_summary=evidence_summary,
            catalog_count=catalog_count,
            memory_count=memory_count,
            rejected=rejected,
            degraded_reasons=degraded,
        )
        extraction_run_id = None
        extraction_proposal_refs: list[str] = []
        if extraction_result is not None:
            extraction_run_id = extraction_result.run_id
            persistence = ExtractionCandidatePersistenceService(
                repository=self._repository
            )
            await persistence.persist_result(extraction_result)
            if extraction_result.accepted_candidates:
                proposed = await persistence.propose_candidates(
                    ProposeCandidatesRequest(
                        workspace_id=request.workspace_id,
                        run_id=extraction_result.run_id,
                        candidate_ids=[
                            candidate.candidate_id
                            for candidate in extraction_result.accepted_candidates
                        ],
                        correlation_id=request.correlation_id,
                        idempotency_key=(
                            f"{request.idempotency_key}:propose-extraction-candidates"
                        ),
                    )
                )
                extraction_proposal_refs = list(proposed.proposal_refs)
        return BusinessSourceLearningResult(
            gateway_status=gateway_status,
            catalog_candidate_count=catalog_count,
            memory_candidate_count=memory_count,
            rejected_candidates=rejected,
            degraded_reasons=degraded,
            evidence_summary=evidence_summary,
            projection=projection,
            extraction_run_id=extraction_run_id,
            extraction_candidate_count=(
                len(extraction_result.accepted_candidates)
                + len(extraction_result.rejected_candidates)
                if extraction_result is not None
                else 0
            ),
            extraction_proposal_refs=extraction_proposal_refs,
        )

    async def _write_catalog_family(
        self,
        *,
        request: BusinessSourceLearningRequest,
        candidate: BusinessSourceCatalogCandidate,
        source_fact_value: dict[str, Any],
    ) -> None:
        await self._write_catalog_fact(
            request=request,
            candidate=candidate,
            fact_id=candidate.product_ref,
            fact_type="catalog_product",
            value=_normalized_catalog_product_value(candidate),
            source_fact_value=source_fact_value,
        )
        for variant in candidate.variants:
            await self._write_catalog_fact(
                request=request,
                candidate=candidate,
                fact_id=_catalog_fact_id(
                    candidate.product_ref,
                    "catalog_variant",
                    str(variant.variant_ref),
                ),
                fact_type="catalog_variant",
                value=_catalog_value_payload(variant),
                source_fact_value=source_fact_value,
            )
        for offer in candidate.offers:
            await self._write_catalog_fact(
                request=request,
                candidate=candidate,
                fact_id=_catalog_fact_id(
                    candidate.product_ref,
                    "catalog_offer",
                    str(offer.offer_ref),
                ),
                fact_type="catalog_offer",
                value=_catalog_value_payload(offer),
                source_fact_value=source_fact_value,
            )
        for media in _normalized_catalog_media_items(candidate):
            await self._write_catalog_fact(
                request=request,
                candidate=candidate,
                fact_id=_catalog_fact_id(
                    candidate.product_ref,
                    "catalog_media",
                    str(media["media_ref"]),
                ),
                fact_type="catalog_media",
                value=dict(media),
                source_fact_value=source_fact_value,
            )
        await self._write_catalog_fact(
            request=request,
            candidate=candidate,
            fact_id=_catalog_fact_id(
                candidate.product_ref,
                "catalog_source",
                str(
                    _normalized_catalog_source_fact(
                        candidate,
                        request=request,
                    )["source_ref"]
                ),
            ),
            fact_type="catalog_source",
            value=_normalized_catalog_source_fact(candidate, request=request),
            source_fact_value=source_fact_value,
        )

    async def _write_catalog_fact(
        self,
        *,
        request: BusinessSourceLearningRequest,
        candidate: BusinessSourceCatalogCandidate,
        fact_id: str,
        fact_type: str,
        value: dict[str, Any],
        source_fact_value: dict[str, Any],
    ) -> None:
        supersedes_fact_id: str | None = None
        target_fact_id = fact_id
        update_context = _source_update_context(source_fact_value)
        if update_context:
            existing = await self._repository.get_fact(
                workspace_id=request.workspace_id,
                fact_id=fact_id,
            )
            if existing is not None and existing.status != "proposed":
                supersedes_fact_id = fact_id
                target_fact_id = _catalog_update_fact_id(
                    fact_id=fact_id,
                    source_fact_value=source_fact_value,
                )
                value = {
                    **value,
                    "updates_fact_id": fact_id,
                }
        value = _with_source_update_context(value, source_fact_value)
        await self._write.apply(
            BusinessBrainFactUpdateInput(
                update_id=(
                    f"source-learning:{fact_type}:{target_fact_id}:"
                    f"{request.correlation_id}"
                ),
                fact_id=target_fact_id,
                workspace_id=request.workspace_id,
                fact_type=fact_type,
                entity_ref=candidate.product_ref,
                value=value,
                confidence=candidate.confidence,
                status="proposed",
                risk_tier=candidate.risk_tier,
                source="ai_proposal",
                approval_state="proposed",
                source_refs=_source_fact_refs(
                    request=request,
                    evidence_refs=candidate.evidence_refs,
                ),
                idempotency_key=(
                    f"source-learning:{request.idempotency_key}:{fact_type}:"
                    f"{target_fact_id}"
                ),
                supersedes_fact_id=supersedes_fact_id,
                actor_type="agent",
                actor_ref="business_source_learning",
                correlation_id=request.correlation_id,
            )
        )

    async def _source_units(
        self,
        request: BusinessSourceLearningRequest,
    ) -> tuple[Any, ...]:
        records = await self._repository.list_index_records(
            workspace_id=request.workspace_id,
            fact_id=request.source_fact_id,
        )
        return tuple(records[: request.max_source_units])

    async def _persist_projection(
        self,
        *,
        request: BusinessSourceLearningRequest,
        gateway_status: GatewayStatus,
        evidence_summary: dict[str, int],
        catalog_count: int,
        memory_count: int,
        rejected: list[dict[str, Any]],
        degraded_reasons: list[str],
    ) -> BusinessBrainProjection:
        existing = await self._repository.get_projection(
            workspace_id=request.workspace_id,
            projection_ref=f"business_source_learning:{request.source_ref}",
        )
        existing_state = dict(existing.state or {}) if existing is not None else {}
        source_refs = [request.source_ref, request.source_fact_id]
        existing_source_refs = (
            list(getattr(existing, "source_refs", []) or []) if existing is not None else []
        )
        for ref in existing_source_refs:
            if ref not in source_refs:
                source_refs.append(ref)
        projection = BusinessBrainProjection(
            projection_ref=f"business_source_learning:{request.source_ref}",
            workspace_id=request.workspace_id,
            projection_type="business_source_learning",
            entity_ref=f"workspace:source:{request.source_ref}",
            state={
                **existing_state,
                "source_ref": request.source_ref,
                "source_kind": request.source_kind,
                "source_fact_id": request.source_fact_id,
                "gateway_status": gateway_status,
                "catalog_candidate_count": catalog_count,
                "memory_candidate_count": memory_count,
                "rejected_candidate_count": len(rejected),
                "rejected_candidates": list(rejected),
                "evidence_summary": dict(evidence_summary),
            },
            source_refs=source_refs,
            degraded=bool(degraded_reasons),
            degraded_reasons=list(degraded_reasons),
        )
        await self._repository.upsert_projection(projection)
        return projection


def _source_unit_payload(record: Any) -> dict[str, Any]:
    return {
        "unit_ref": record.unit_ref,
        "source_refs": list(record.source_refs),
        "state": record.state,
        "embedding_state": record.embedding_state,
        "degraded_reason": record.degraded_reason,
        "text": str(record.source_text or "")[:6000],
    }


def _source_learning_instruction(request: BusinessSourceLearningRequest) -> str:
    try:
        return get_prompt_registry().load(
            request.prompt_id,
            version=request.prompt_version,
        ).body.strip()
    except PromptRegistryError:
        return _SOURCE_LEARNING_INSTRUCTION


def _source_media_assets(source_value: dict[str, Any]) -> list[dict[str, Any]]:
    raw_assets = source_value.get("media_assets")
    if not isinstance(raw_assets, list):
        return []
    return [dict(item) for item in raw_assets if isinstance(item, dict)]


def _structured_source_learning_output(
    *,
    request: BusinessSourceLearningRequest,
    source_fact_value: dict[str, Any],
    source_units: tuple[Any, ...],
    media_assets: list[dict[str, Any]],
) -> BusinessSourceLearningOutput | None:
    metadata = source_fact_value.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if metadata.get("structured_source") != "shopify_products_json":
        return None

    candidates: list[BusinessSourceCatalogCandidate] = []
    for product in _shopify_source_products(source_units):
        title = product.get("title")
        if not title:
            continue
        handle = product.get("handle") or _slug_for_ref(title)
        product_ref = _structured_product_ref(
            source_ref=request.source_ref,
            handle=handle,
            title=title,
        )
        matched_media = _matching_shopify_media(
            media_assets,
            title=title,
            handle=handle,
        )
        evidence_refs = _unique(
            [
                str(product["unit_ref"]),
                *[
                    str(item.get("media_ref"))
                    for item in matched_media
                    if item.get("media_ref")
                ],
            ]
        )
        offers: list[BusinessSourceCatalogOfferValue] = []
        variants: list[BusinessSourceCatalogVariantValue] = []
        for index, variant in enumerate(product.get("variants") or []):
            variant_ref = f"variant:{index + 1}"
            variants.append(
                BusinessSourceCatalogVariantValue(
                    variant_ref=variant_ref,
                    product_ref=product_ref,
                    title=variant.get("title"),
                    sku=variant.get("sku"),
                    attributes={"options": variant.get("options")},
                )
            )
            price = _shopify_price(variant.get("price"))
            if price is not None or variant.get("availability"):
                offers.append(
                    BusinessSourceCatalogOfferValue(
                        offer_ref=f"offer:{index + 1}",
                        product_ref=product_ref,
                        variant_ref=variant_ref,
                        price=(
                            {"amount": price, "currency": "UZS"}
                            if price is not None
                            else None
                        ),
                        stock={"state": variant.get("availability")},
                        active=variant.get("availability") != "unavailable",
                    )
                )
        candidates.append(
            BusinessSourceCatalogCandidate(
                product_ref=product_ref,
                product=BusinessSourceCatalogProductValue(
                    title=title,
                    identity_ref=product_ref,
                    category=product.get("product_type"),
                    description=product.get("body"),
                    attributes={
                        "vendor": product.get("vendor"),
                        "handle": handle,
                        "source": "shopify_products_json",
                    },
                ),
                variants=variants,
                offers=offers,
                media=[
                    BusinessSourceCatalogMediaValue(
                        media_ref=_structured_media_ref(
                            product_ref=product_ref,
                            index=index,
                        ),
                        product_ref=product_ref,
                        source_media_ref=str(item.get("media_ref")),
                        media_type=str(item.get("media_type") or "image"),
                        url=str(item.get("url") or "") or None,
                        quality_state="product_media",
                        crop_state="not_needed",
                        approved=False,
                    )
                    for index, item in enumerate(matched_media)
                    if item.get("media_ref")
                ],
                source_fact=BusinessSourceCatalogSourceFactValue(
                    source_ref=request.source_ref,
                    source_type=request.source_kind,
                    content_refs=evidence_refs,
                    extraction_state="structured",
                ),
                confidence=0.94,
                risk_tier="low",
                evidence_refs=evidence_refs,
            )
        )

    if not candidates:
        return None
    return BusinessSourceLearningOutput(
        catalog_candidates=candidates,
        memory_candidates=[],
    )


def _shopify_source_products(source_units: tuple[Any, ...]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for unit in source_units:
        unit_ref = str(getattr(unit, "unit_ref", "") or "")
        for raw_line in str(getattr(unit, "source_text", "") or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("Product:"):
                if current is not None:
                    products.append(current)
                current = {
                    "unit_ref": unit_ref,
                    "title": line.removeprefix("Product:").strip(),
                    "variants": [],
                }
                continue
            if current is None:
                continue
            if line.startswith("Handle:"):
                current["handle"] = line.removeprefix("Handle:").strip()
            elif line.startswith("Vendor:"):
                current["vendor"] = line.removeprefix("Vendor:").strip()
            elif line.startswith("Type:"):
                current["product_type"] = line.removeprefix("Type:").strip()
            elif line.startswith("Variant:"):
                current["variants"].append(_shopify_variant_from_line(line))
            elif not line.startswith("Option:") and "body" not in current:
                current["body"] = line
    if current is not None:
        products.append(current)
    return products


def _shopify_variant_from_line(line: str) -> dict[str, Any]:
    parts = [
        part.strip()
        for part in line.removeprefix("Variant:").split(";")
        if part.strip()
    ]
    variant: dict[str, Any] = {"title": parts[0] if parts else "Default"}
    options: list[str] = []
    for part in parts[1:]:
        if part.startswith("sku="):
            variant["sku"] = part.removeprefix("sku=").strip()
        elif part.startswith("price="):
            variant["price"] = part.removeprefix("price=").strip()
        elif part.startswith("availability="):
            variant["availability"] = part.removeprefix("availability=").strip()
        elif part.startswith("options="):
            options = [
                item.strip()
                for item in part.removeprefix("options=").split(",")
                if item.strip()
            ]
    if options:
        variant["options"] = options
    return variant


def _matching_shopify_media(
    media_assets: list[dict[str, Any]],
    *,
    title: str,
    handle: str,
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    title_lower = title.lower()
    for asset in media_assets:
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        product_handle = str(metadata.get("product_handle") or "").strip()
        alt_text = str(asset.get("alt_text") or "").strip()
        if product_handle == handle or alt_text.lower() == title_lower:
            matched.append(asset)
    return matched


def _shopify_price(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    cleaned = text.replace(" ", "").replace(",", ".")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return int(amount) if amount.is_integer() else amount


def _allowed_evidence_refs(
    source_units: tuple[Any, ...],
    media_assets: list[dict[str, Any]],
) -> list[str]:
    refs: list[str] = []
    for unit in source_units:
        refs.append(str(unit.unit_ref))
    for asset in media_assets:
        media_ref = str(asset.get("media_ref") or "").strip()
        if media_ref:
            refs.append(media_ref)
    return _unique(refs)


def _source_fact_refs(
    *,
    request: BusinessSourceLearningRequest,
    evidence_refs: Iterable[str],
) -> list[str]:
    return _unique([*[str(ref) for ref in evidence_refs], request.source_ref])


def _evidence_summary(
    source_units: tuple[Any, ...],
    media_assets: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        "source_unit_count": len(source_units),
        "media_asset_count": len(media_assets),
        "allowed_evidence_ref_count": len(
            _allowed_evidence_refs(source_units, media_assets)
        ),
    }


def _catalog_universal_candidate(
    candidate: BusinessSourceCatalogCandidate,
    *,
    request: BusinessSourceLearningRequest,
    source_fact_value: dict[str, Any],
) -> ExtractionCandidate:
    update_context = _source_update_context(source_fact_value)
    value = candidate.model_dump(mode="json")
    if update_context:
        value.update(update_context)
    return ExtractionCandidate(
        candidate_id=f"business_source_catalog:{candidate.product_ref}",
        workspace_id=request.workspace_id,
        owner="commerce_core",
        profile_ref="commerce_generic.v1",
        kind="catalog_family",
        entity_ref=candidate.product_ref,
        operation="update" if update_context else "create",
        value=value,
        confidence=candidate.confidence,
        risk_tier=candidate.risk_tier,
        evidence_refs=list(candidate.evidence_refs),
        evidence_state="valid",
        requires_review=True,
        reason_code=(
            "business_source_catalog_update_candidate"
            if update_context
            else "business_source_catalog_candidate"
        ),
    )


def _memory_universal_candidate(
    candidate: BusinessSourceMemoryCandidate,
    *,
    request: BusinessSourceLearningRequest,
    value: dict[str, Any],
) -> ExtractionCandidate:
    return ExtractionCandidate(
        candidate_id=f"business_source_memory:{candidate.fact_id}",
        workspace_id=request.workspace_id,
        owner="business_brain",
        profile_ref=_memory_profile_ref(candidate.fact_type),
        kind=_memory_universal_kind(candidate.fact_type),
        entity_ref=candidate.entity_ref,
        operation="create",
        value={
            "fact_id": candidate.fact_id,
            "fact_type": candidate.fact_type,
            "entity_ref": candidate.entity_ref,
            "value": dict(value),
        },
        confidence=candidate.confidence,
        risk_tier=candidate.risk_tier,
        evidence_refs=list(candidate.evidence_refs),
        evidence_state="valid",
        requires_review=True,
        reason_code="business_source_memory_candidate",
    )


def _with_source_update_context(
    value: dict[str, Any],
    source_fact_value: dict[str, Any],
) -> dict[str, Any]:
    update_context = _source_update_context(source_fact_value)
    if not update_context:
        return value
    return {**value, **update_context}


def _source_update_context(source_fact_value: dict[str, Any]) -> dict[str, Any]:
    events = _source_change_events(source_fact_value)
    if not events:
        return {}
    policy = _catalog_update_policy(events)
    return {
        "source_change_events": events,
        "catalog_update_policy": policy,
    }


def _source_change_events(source_fact_value: dict[str, Any]) -> list[dict[str, Any]]:
    raw_events = source_fact_value.get("source_change_events")
    if not isinstance(raw_events, list):
        return []
    return [dict(item) for item in raw_events if isinstance(item, dict)]


def _catalog_update_policy(events: list[dict[str, Any]]) -> str:
    for event in events:
        policy = str(event.get("catalog_update_policy") or "").strip()
        if policy:
            return policy
    return "create_update_proposal"


def _catalog_update_fact_id(
    *,
    fact_id: str,
    source_fact_value: dict[str, Any],
) -> str:
    seed = json.dumps(
        {
            "fact_id": fact_id,
            "source_ref": str(source_fact_value.get("source_ref") or ""),
            "source_change_events": _source_change_events(source_fact_value),
        },
        sort_keys=True,
    )
    digest = hashlib.sha1(seed.encode()).hexdigest()[:10]
    return f"{fact_id}:update:{digest}"


def _rejected_extraction_candidate(
    *,
    candidate_ref: str,
    candidate_type: str,
    reason: str,
    unsupported_refs: list[str],
    validation_errors: list[str] | None = None,
) -> RejectedExtractionCandidate:
    return RejectedExtractionCandidate(
        candidate_id=f"business_source_rejected:{candidate_type}:{candidate_ref}",
        profile_ref=_candidate_type_profile_ref(candidate_type),
        kind=_candidate_type_extraction_kind(candidate_type),
        owner=_candidate_type_owner(candidate_type),
        reason=reason,
        unsupported_refs=list(unsupported_refs),
        validation_errors=list(validation_errors or []),
    )


def _memory_profile_ref(fact_type: MemoryCandidateType) -> str:
    if fact_type == "conversation_pair_fact":
        return "conversation_pairs.v1"
    if fact_type == "voice_fact":
        return "seller_voice.v1"
    return "generic_kb.v1"


def _memory_universal_kind(fact_type: MemoryCandidateType) -> str:
    if fact_type == "conversation_pair_fact":
        return "conversation_pair"
    if fact_type == "seller_rule_fact":
        return "seller_rule"
    if fact_type == "voice_fact":
        return "voice_observation"
    return "kb_entry"


def _candidate_type_profile_ref(candidate_type: str) -> str:
    if candidate_type == "catalog_product":
        return "commerce_generic.v1"
    if candidate_type == "conversation_pair_fact":
        return "conversation_pairs.v1"
    if candidate_type == "voice_fact":
        return "seller_voice.v1"
    return "generic_kb.v1"


def _candidate_type_extraction_kind(candidate_type: str) -> str:
    if candidate_type == "catalog_product":
        return "catalog_family"
    if candidate_type == "conversation_pair_fact":
        return "conversation_pair"
    if candidate_type == "seller_rule_fact":
        return "seller_rule"
    if candidate_type == "voice_fact":
        return "voice_observation"
    return "kb_entry"


def _candidate_type_owner(candidate_type: str) -> str:
    if candidate_type == "catalog_product":
        return "commerce_core"
    return "business_brain"


def _rejected_candidate_from_extraction(
    candidate: RejectedExtractionCandidate,
    *,
    catalog_candidates_by_id: dict[str, BusinessSourceCatalogCandidate],
    memory_candidates_by_id: dict[
        str,
        tuple[BusinessSourceMemoryCandidate, dict[str, Any]],
    ],
) -> dict[str, Any]:
    catalog_candidate = catalog_candidates_by_id.get(candidate.candidate_id)
    if catalog_candidate is not None:
        return _rejected_candidate(
            candidate_ref=catalog_candidate.product_ref,
            candidate_type="catalog_product",
            reason=candidate.reason,
            unsupported_refs=list(candidate.unsupported_refs),
            validation_errors=list(candidate.validation_errors) or None,
        )
    memory_entry = memory_candidates_by_id.get(candidate.candidate_id)
    if memory_entry is not None:
        memory_candidate, _value = memory_entry
        return _rejected_candidate(
            candidate_ref=memory_candidate.fact_id,
            candidate_type=memory_candidate.fact_type,
            reason=candidate.reason,
            unsupported_refs=list(candidate.unsupported_refs),
            validation_errors=list(candidate.validation_errors) or None,
        )
    return _rejected_candidate(
        candidate_ref=candidate.candidate_id,
        candidate_type=candidate.kind,
        reason=candidate.reason,
        unsupported_refs=list(candidate.unsupported_refs),
        validation_errors=list(candidate.validation_errors) or None,
    )


def _rejected_candidate(
    *,
    candidate_ref: str,
    candidate_type: str,
    reason: str = "unsupported_evidence_refs",
    unsupported_refs: list[str],
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "candidate_ref": candidate_ref,
        "candidate_type": candidate_type,
        "reason": reason,
        "unsupported_refs": list(unsupported_refs),
    }
    if validation_errors is not None:
        payload["validation_errors"] = list(validation_errors)
    return payload


def _catalog_candidate_validation_errors(
    candidate: BusinessSourceCatalogCandidate,
    *,
    request: BusinessSourceLearningRequest,
) -> list[str]:
    errors: list[str] = []
    _normalized_catalog_source_fact(candidate, request=request)
    title = str(candidate.product.title or "").strip()
    if (
        not title
        or title == candidate.product_ref
        or title.startswith("catalog_product:")
        or title.startswith("onboarding:")
    ):
        errors.append("product.title_human_readable")
    for index, variant in enumerate(candidate.variants):
        if not str(variant.variant_ref or "").strip():
            errors.append(f"variants.{index}.variant_ref")
    for index, offer in enumerate(candidate.offers):
        if not str(offer.offer_ref or "").strip():
            errors.append(f"offers.{index}.offer_ref")
    for index, media in enumerate(candidate.media):
        if not str(media.media_ref or media.source_media_ref or "").strip():
            errors.append(f"media.{index}.media_ref")
    return errors


def _normalized_catalog_source_fact(
    candidate: BusinessSourceCatalogCandidate,
    *,
    request: BusinessSourceLearningRequest,
) -> dict[str, Any]:
    source_fact = _catalog_value_payload(candidate.source_fact)
    source_ref = str(source_fact.get("source_ref") or "").strip() or request.source_ref
    source_fact["source_ref"] = source_ref
    source_fact.setdefault("source_type", request.source_kind)
    source_fact.setdefault("content_refs", list(candidate.evidence_refs))
    return source_fact


def _normalized_catalog_candidate(
    candidate: BusinessSourceCatalogCandidate,
    *,
    request: BusinessSourceLearningRequest,
) -> BusinessSourceCatalogCandidate:
    product_ref = _normalized_product_ref(candidate, request=request)
    return candidate.model_copy(
        update={
            "product_ref": product_ref,
            "product": candidate.product.model_copy(
                update={"identity_ref": product_ref}
            ),
            "variants": [
                _normalized_catalog_variant(
                    variant,
                    product_ref=product_ref,
                    index=index,
                )
                for index, variant in enumerate(candidate.variants)
            ],
            "offers": [
                _normalized_catalog_offer(
                    offer,
                    product_ref=product_ref,
                    index=index,
                )
                for index, offer in enumerate(candidate.offers)
            ],
            "media": [
                media.model_copy(update={"product_ref": product_ref})
                for media in candidate.media
            ],
        }
    )


def _normalized_product_ref(
    candidate: BusinessSourceCatalogCandidate,
    *,
    request: BusinessSourceLearningRequest,
) -> str:
    product_ref = str(candidate.product_ref or "").strip()
    if product_ref.startswith("catalog_product:"):
        return product_ref
    identity_ref = str(candidate.product.identity_ref or "").strip()
    if identity_ref.startswith("catalog_product:"):
        return identity_ref
    title = str(candidate.product.title or "").strip()
    seed = title or product_ref or request.source_ref
    slug = _slug_for_ref(seed)[:90]
    digest = hashlib.sha1(
        f"{request.source_ref}:{product_ref}:{title}".encode()
    ).hexdigest()[:10]
    return f"catalog_product:source:{slug}:{digest}"


def _normalized_catalog_variant(
    variant: BusinessSourceCatalogVariantValue,
    *,
    product_ref: str,
    index: int,
) -> BusinessSourceCatalogVariantValue:
    variant_ref = str(variant.variant_ref or "").strip() or f"variant:{index + 1}"
    return variant.model_copy(
        update={
            "product_ref": product_ref,
            "variant_ref": variant_ref,
        }
    )


def _normalized_catalog_offer(
    offer: BusinessSourceCatalogOfferValue,
    *,
    product_ref: str,
    index: int,
) -> BusinessSourceCatalogOfferValue:
    offer_ref = str(offer.offer_ref or "").strip() or f"offer:{index + 1}"
    return offer.model_copy(
        update={
            "product_ref": product_ref,
            "offer_ref": offer_ref,
        }
    )


def _normalized_catalog_product_value(
    candidate: BusinessSourceCatalogCandidate,
) -> dict[str, Any]:
    product = _catalog_value_payload(candidate.product)
    product["identity_ref"] = candidate.product_ref
    return product


def _normalized_catalog_media_items(
    candidate: BusinessSourceCatalogCandidate,
) -> list[dict[str, Any]]:
    if candidate.media:
        media_items: list[dict[str, Any]] = []
        for index, item in enumerate(candidate.media):
            media = _catalog_value_payload(item)
            if not str(media.get("media_ref") or "").strip():
                media["media_ref"] = f"catalog_media:{candidate.product_ref}:source-media:{index}"
            media.setdefault("product_ref", candidate.product_ref)
            if media.get("source_media_ref") and not media.get("quality_state"):
                media["quality_state"] = "page_media_only"
            if media.get("source_media_ref") and not media.get("crop_state"):
                media["crop_state"] = "pending"
            media["approved"] = False
            media_items.append(media)
        return media_items
    media_items: list[dict[str, Any]] = []
    for index, source_ref in enumerate(candidate.evidence_refs):
        if not source_ref.startswith("source_media:"):
            continue
        media_items.append(
            {
                "media_ref": f"catalog_media:{candidate.product_ref}:source-page:{index}",
                "product_ref": candidate.product_ref,
                "source_media_ref": source_ref,
                "media_type": "source_page",
                "quality_state": "page_media_only",
                "crop_state": "pending",
                "approved": False,
            }
        )
    return media_items


def _catalog_value_payload(value: BaseModel) -> dict[str, Any]:
    return _compact_value(value.model_dump(mode="json", exclude_none=True))


def _memory_value_payload(value: BusinessSourceMemoryValue) -> dict[str, Any]:
    return _compact_value(value.model_dump(mode="json", exclude_none=True))


def _compact_value(value: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            nested = _compact_value(item)
            if nested:
                compacted[key] = nested
            continue
        if isinstance(item, list):
            nested_items = [
                _compact_value(child) if isinstance(child, dict) else child
                for child in item
                if child not in (None, "", [], {})
            ]
            nested_items = [child for child in nested_items if child not in ({}, [])]
            if nested_items:
                compacted[key] = nested_items
            continue
        if item not in (None, ""):
            compacted[key] = item
    return compacted


def _catalog_fact_id(product_ref: str, fact_type: str, item_ref: str) -> str:
    return f"{fact_type}:{product_ref}:{item_ref}"


def _slug_for_ref(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else "-"
        for character in value.strip()
    )
    return "-".join(part for part in normalized.split("-") if part) or "item"


def _structured_product_ref(*, source_ref: str, handle: str, title: str) -> str:
    slug = _slug_for_ref(handle or title)[:90]
    digest = hashlib.sha1(f"{source_ref}:{handle or title}".encode()).hexdigest()[:10]
    return f"catalog_product:shopify:{slug}:{digest}"


def _structured_media_ref(*, product_ref: str, index: int) -> str:
    digest = hashlib.sha1(product_ref.encode("utf-8")).hexdigest()[:10]
    return f"catalog_media:shopify:{digest}:m{index + 1}"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
