from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from app.modules.business_brain.media_learning import (
    BusinessMediaArtifactBatchLearningRequest,
    BusinessMediaArtifactBatchLearningService,
)
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import SourceUnitRebuildRequest
from app.modules.business_brain.source_learning import (
    BusinessSourceLearningRequest,
    BusinessSourceLearningService,
)
from app.modules.business_brain.source_media_artifacts import (
    SourceMediaArtifactStore,
)
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.source_ingestion import (
    OnboardingSourceIngestionRequest,
    OnboardingSourceIngestionService,
    SourceFetchResult,
)
from app.modules.retrieval_core.contracts import RetrievalContextRequest
from app.modules.retrieval_core.service import RetrievalCoreService

SourceKind = Literal[
    "text",
    "website",
    "pdf",
    "screenshot",
    "telegram_channel",
    "voice_note",
    "spreadsheet",
    "past_conversation",
]


@dataclass(frozen=True, slots=True)
class CompanyBrainEvalFixture:
    source_id: str
    source_kind: SourceKind
    description: str
    source_ref: str
    source_payload: dict[str, Any]
    content_bytes: bytes | None
    query_text: str
    expected_product_title: str | None
    expected_retrieval_text: str | None = None
    requires_product: bool = True
    requires_knowledge: bool = True
    requires_media: bool | None = None
    expected_missing_slots: tuple[str, ...] = ()


class CompanyBrainEvalCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class CompanyBrainSourceEvalResult(BaseModel):
    source_id: str
    source_kind: SourceKind
    description: str
    passed: bool
    product_count: int = Field(ge=0)
    knowledge_count: int = Field(ge=0)
    conversation_pair_count: int = Field(default=0, ge=0)
    media_count: int = Field(ge=0)
    learned_product_titles: list[str] = Field(default_factory=list)
    retrieved_product_titles: list[str] = Field(default_factory=list)
    retrieved_media_refs: list[str] = Field(default_factory=list)
    deferred_media_count: int = Field(default=0, ge=0)
    embedding_ready_count: int = Field(default=0, ge=0)
    embedding_degraded_count: int = Field(default=0, ge=0)
    embedding_pending_count: int = Field(default=0, ge=0)
    embedding_model_ids: list[str] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)
    retrieval_hit: bool
    degraded_reasons: list[str] = Field(default_factory=list)
    checks: list[CompanyBrainEvalCheck] = Field(default_factory=list)


class CompanyBrainEvalSuiteReport(BaseModel):
    suite: str
    total_sources: int = Field(ge=0)
    passed_sources: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    retrieval_pass_rate: float = Field(ge=0.0, le=1.0)
    product_count: int = Field(ge=0)
    knowledge_count: int = Field(ge=0)
    conversation_pair_count: int = Field(default=0, ge=0)
    media_count: int = Field(ge=0)
    deferred_media_count: int = Field(default=0, ge=0)
    embedding_ready_count: int = Field(default=0, ge=0)
    embedding_degraded_count: int = Field(default=0, ge=0)
    embedding_pending_count: int = Field(default=0, ge=0)
    embedding_model_ids: list[str] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)
    median_source_duration_ms: int = Field(default=0, ge=0)
    p95_source_duration_ms: int = Field(default=0, ge=0)
    max_source_duration_ms: int = Field(default=0, ge=0)
    live_provider: bool = False
    semantic_retrieval_enabled: bool = False
    contextual_source_units_enabled: bool = False
    hallucinated_source_ref_failures: list[str] = Field(default_factory=list)
    results: list[CompanyBrainSourceEvalResult] = Field(default_factory=list)


ProviderFactory = Callable[[tuple[CompanyBrainEvalFixture, ...]], LLMGateway]


async def run_company_brain_eval_suite(
    *,
    workspace_id: int,
    repository: CommercialSpineRepository,
    media_artifact_store: SourceMediaArtifactStore,
    provider_factory: ProviderFactory | None = None,
    fixtures: tuple[CompanyBrainEvalFixture, ...] | None = None,
    fetch_live_sources: bool = False,
    embed_source_units: bool = False,
    contextualize_source_units: bool = False,
    enable_semantic_retrieval: bool = False,
    max_media_assets_per_source: int = 40,
) -> CompanyBrainEvalSuiteReport:
    fixtures = fixtures or _company_brain_fixtures()
    started = time.monotonic()
    gateway = (
        provider_factory(fixtures)
        if provider_factory is not None
        else LLMGateway(
            repository=repository,
            provider=_deterministic_company_brain_provider(fixtures),
        )
    )
    ingestion = OnboardingSourceIngestionService(
        repository=repository,
        fetch_source=None if fetch_live_sources else _fixture_fetcher(fixtures),
        media_artifact_store=media_artifact_store,
        gateway=gateway,
    )
    source_learning = BusinessSourceLearningService(repository=repository, gateway=gateway)
    media_learning = BusinessMediaArtifactBatchLearningService(
        repository=repository,
        gateway=gateway,
        media_artifact_store=media_artifact_store,
    )
    results: list[CompanyBrainSourceEvalResult] = []
    for fixture in fixtures:
        fixture_started = time.monotonic()
        ingested = await ingestion.ingest(
            OnboardingSourceIngestionRequest(
                workspace_id=workspace_id,
                source_ref=fixture.source_ref,
                source_kind=fixture.source_kind,
                source_payload=fixture.source_payload,
                content_bytes=fixture.content_bytes,
                correlation_id=f"company-brain-eval:{fixture.source_id}:ingest",
                idempotency_key=f"company-brain-eval:{fixture.source_id}:ingest",
                embed_source_units=embed_source_units,
                contextualize_source_units=contextualize_source_units,
            )
        )
        if fixture.source_kind in {"pdf", "screenshot"}:
            learning = await media_learning.learn_from_artifact_batches(
                BusinessMediaArtifactBatchLearningRequest(
                    workspace_id=workspace_id,
                    source_ref=fixture.source_ref,
                    source_kind=fixture.source_kind,
                    source_fact_id=ingested.source_fact_id,
                    media_refs=[asset.media_ref for asset in ingested.media_assets],
                    chunk_size=1,
                    max_media_assets=max_media_assets_per_source,
                    max_parallel_chunks=2,
                    correlation_id=f"company-brain-eval:{fixture.source_id}:media",
                    idempotency_key=f"company-brain-eval:{fixture.source_id}:media",
                )
            )
            product_count = learning.catalog_candidate_count
            knowledge_count = learning.memory_candidate_count
            deferred_media_count = len(learning.deferred_media_refs)
            degraded = list(learning.degraded_reasons)
        else:
            learning = await source_learning.learn_from_source(
                BusinessSourceLearningRequest(
                    workspace_id=workspace_id,
                    source_ref=fixture.source_ref,
                    source_kind=fixture.source_kind,
                    source_fact_id=ingested.source_fact_id,
                    correlation_id=f"company-brain-eval:{fixture.source_id}:source",
                    idempotency_key=f"company-brain-eval:{fixture.source_id}:source",
                )
            )
            product_count = learning.catalog_candidate_count
            knowledge_count = learning.memory_candidate_count
            deferred_media_count = 0
            degraded = list(learning.degraded_reasons)

        evidence_refs = _source_evidence_refs(
            source_ref=fixture.source_ref,
            source_fact_id=ingested.source_fact_id,
            source_units=ingested.source_units,
            media_assets=ingested.media_assets,
        )
        if fixture.source_kind == "past_conversation":
            pair_facts = _facts_with_source_refs(
                await repository.list_facts(
                    workspace_id=workspace_id,
                    fact_type="conversation_pair_fact",
                    statuses=("proposed", "active", "confirmed"),
                    limit=250,
                ),
                evidence_refs,
            )
            pair_fact_ids = [fact.fact_id for fact in pair_facts]
            await BusinessBrainMemoryService(repository=repository).rebuild_contextual_source_units(
                SourceUnitRebuildRequest(
                    workspace_id=workspace_id,
                    fact_types=["conversation_pair_fact"],
                    candidate_fact_ids=pair_fact_ids,
                    embed_source_units=embed_source_units,
                    contextualize_source_units=contextualize_source_units,
                )
            )
            pair_retrieval = await RetrievalCoreService(
                repository=repository,
            ).retrieve_contextual(
                RetrievalContextRequest(
                    workspace_id=workspace_id,
                    requested_fact_types=["conversation_pair_fact"],
                    requested_slots=["conversation_pair_fact"],
                    query_text=fixture.query_text,
                    enable_semantic=enable_semantic_retrieval,
                    include_proposed=True,
                    include_source_units=True,
                    limit=5,
                )
            )
            degraded.extend(pair_retrieval.degraded_reasons)
            expected = (fixture.expected_retrieval_text or "").lower()
            retrieval_text = "\n".join(
                "\n".join(
                    [
                        str(candidate.contextual_text or ""),
                        json.dumps(candidate.value, ensure_ascii=False),
                    ]
                )
                for candidate in pair_retrieval.candidates
            ).lower()
            retrieval_hit = bool(pair_retrieval.candidates) and (
                not expected or expected in retrieval_text
            )
            index_states = await _embedding_state_counts(
                repository=repository,
                workspace_id=workspace_id,
                fact_ids=pair_fact_ids,
            )
            embedding_model_ids = await _embedding_model_ids(
                repository=repository,
                workspace_id=workspace_id,
                fact_ids=pair_fact_ids,
            )
            checks = [
                CompanyBrainEvalCheck(
                    name="learned_conversation_pair",
                    passed=len(pair_facts) > 0,
                    detail=f"learned {len(pair_facts)} conversation pairs",
                ),
                CompanyBrainEvalCheck(
                    name="retrieval_hit",
                    passed=retrieval_hit,
                    detail=f"query={fixture.query_text!r}",
                ),
            ]
            if embed_source_units:
                checks.append(
                    CompanyBrainEvalCheck(
                        name="embedding_ready",
                        passed=index_states["ready"] > 0,
                        detail=(
                            f"ready={index_states['ready']} pending={index_states['pending']} "
                            f"degraded={index_states['degraded']}"
                        ),
                    )
                )
            results.append(
                CompanyBrainSourceEvalResult(
                    source_id=fixture.source_id,
                    source_kind=fixture.source_kind,
                    description=fixture.description,
                    passed=all(check.passed for check in checks),
                    product_count=0,
                    knowledge_count=0,
                    conversation_pair_count=len(pair_facts),
                    media_count=0,
                    learned_product_titles=[],
                    retrieved_product_titles=[],
                    retrieved_media_refs=[],
                    deferred_media_count=deferred_media_count,
                    embedding_ready_count=index_states["ready"],
                    embedding_degraded_count=index_states["degraded"],
                    embedding_pending_count=index_states["pending"],
                    embedding_model_ids=embedding_model_ids,
                    duration_ms=int((time.monotonic() - fixture_started) * 1000),
                    retrieval_hit=retrieval_hit,
                    degraded_reasons=degraded,
                    checks=checks,
                )
            )
            continue
        if not fixture.requires_product:
            memory_fact_types = ["knowledge_fact", "seller_rule_fact", "voice_fact"]
            memory_facts: list[Any] = []
            for fact_type in memory_fact_types:
                memory_facts.extend(
                    _facts_with_source_refs(
                        await repository.list_facts(
                            workspace_id=workspace_id,
                            fact_type=fact_type,
                            statuses=("proposed", "active", "confirmed"),
                            limit=250,
                        ),
                        evidence_refs,
                    )
                )
            memory_fact_ids = [fact.fact_id for fact in memory_facts]
            if memory_fact_ids:
                await BusinessBrainMemoryService(
                    repository=repository
                ).rebuild_contextual_source_units(
                    SourceUnitRebuildRequest(
                        workspace_id=workspace_id,
                        fact_types=memory_fact_types,
                        candidate_fact_ids=memory_fact_ids,
                        embed_source_units=embed_source_units,
                        contextualize_source_units=contextualize_source_units,
                    )
                )
            memory_retrieval = await RetrievalCoreService(
                repository=repository,
            ).retrieve_contextual(
                RetrievalContextRequest(
                    workspace_id=workspace_id,
                    requested_fact_types=memory_fact_types,
                    requested_slots=memory_fact_types,
                    candidate_fact_ids=memory_fact_ids,
                    query_text=fixture.query_text,
                    enable_semantic=enable_semantic_retrieval,
                    include_proposed=True,
                    include_source_units=True,
                    limit=5,
                )
            )
            degraded.extend(memory_retrieval.degraded_reasons)
            expected = (fixture.expected_retrieval_text or "").lower()
            retrieval_text = "\n".join(
                "\n".join(
                    [
                        str(candidate.contextual_text or ""),
                        json.dumps(candidate.value, ensure_ascii=False),
                    ]
                )
                for candidate in memory_retrieval.candidates
            ).lower()
            retrieval_hit = bool(memory_retrieval.candidates) and (
                not expected or expected in retrieval_text
            )
            index_states = await _embedding_state_counts(
                repository=repository,
                workspace_id=workspace_id,
                fact_ids=memory_fact_ids,
            )
            embedding_model_ids = await _embedding_model_ids(
                repository=repository,
                workspace_id=workspace_id,
                fact_ids=memory_fact_ids,
            )
            checks = [
                CompanyBrainEvalCheck(
                    name="learned_product",
                    passed=True,
                    detail=f"learned {product_count} product candidates",
                ),
                CompanyBrainEvalCheck(
                    name="learned_knowledge",
                    passed=knowledge_count > 0 if fixture.requires_knowledge else True,
                    detail=f"learned {knowledge_count} knowledge candidates",
                ),
                CompanyBrainEvalCheck(
                    name="retrieval_hit",
                    passed=retrieval_hit,
                    detail=f"query={fixture.query_text!r}",
                ),
            ]
            if embed_source_units:
                checks.append(
                    CompanyBrainEvalCheck(
                        name="embedding_ready",
                        passed=index_states["ready"] > 0,
                        detail=(
                            f"ready={index_states['ready']} pending={index_states['pending']} "
                            f"degraded={index_states['degraded']}"
                        ),
                    )
                )
            results.append(
                CompanyBrainSourceEvalResult(
                    source_id=fixture.source_id,
                    source_kind=fixture.source_kind,
                    description=fixture.description,
                    passed=all(check.passed for check in checks),
                    product_count=product_count,
                    knowledge_count=knowledge_count,
                    media_count=0,
                    learned_product_titles=_product_titles([]),
                    retrieved_product_titles=[],
                    retrieved_media_refs=[],
                    deferred_media_count=deferred_media_count,
                    embedding_ready_count=index_states["ready"],
                    embedding_degraded_count=index_states["degraded"],
                    embedding_pending_count=index_states["pending"],
                    embedding_model_ids=embedding_model_ids,
                    duration_ms=int((time.monotonic() - fixture_started) * 1000),
                    retrieval_hit=retrieval_hit,
                    degraded_reasons=degraded,
                    checks=checks,
                )
            )
            continue
        product_facts = _facts_with_source_refs(
            await repository.list_facts(
                workspace_id=workspace_id,
                fact_type="catalog_product",
                statuses=("proposed", "active", "confirmed"),
                limit=250,
            ),
            evidence_refs,
        )
        product_refs = [fact.fact_id for fact in product_facts]
        if product_refs:
            await BusinessBrainMemoryService(
                repository=repository
            ).rebuild_contextual_source_units(
                SourceUnitRebuildRequest(
                    workspace_id=workspace_id,
                    fact_types=["catalog_product"],
                    candidate_fact_ids=product_refs,
                    embed_source_units=embed_source_units,
                    contextualize_source_units=contextualize_source_units,
                )
            )
        retrieval = await RetrievalCoreService(
            repository=repository,
        ).retrieve_contextual(
            RetrievalContextRequest(
                workspace_id=workspace_id,
                requested_fact_types=["catalog_product"],
                requested_slots=["media"],
                candidate_fact_ids=product_refs,
                query_text=fixture.query_text,
                enable_semantic=enable_semantic_retrieval,
                include_proposed=True,
                include_source_units=True,
                limit=5,
            )
        )
        degraded.extend(retrieval.degraded_reasons)
        if fixture.expected_product_title:
            expected = fixture.expected_product_title.lower()
            retrieval_hit = any(
                expected in str(candidate.value.get("title") or "").lower()
                for candidate in retrieval.candidates
            )
        else:
            retrieval_hit = bool(retrieval.candidates)
        media_count = sum(
            len(_candidate_media_refs(candidate.value))
            for candidate in retrieval.candidates
            if fixture.expected_product_title is None
            or fixture.expected_product_title.lower()
            in str(candidate.value.get("title") or "").lower()
        )
        retrieved_titles = _unique(
            str(candidate.value.get("title") or "")
            for candidate in retrieval.candidates
        )
        retrieved_media_refs = _unique(
            media_ref
            for candidate in retrieval.candidates
            for media_ref in _candidate_media_refs(candidate.value)
        )
        index_states = await _embedding_state_counts(
            repository=repository,
            workspace_id=workspace_id,
            fact_ids=product_refs,
        )
        embedding_model_ids = await _embedding_model_ids(
            repository=repository,
            workspace_id=workspace_id,
            fact_ids=product_refs,
        )
        checks = [
            CompanyBrainEvalCheck(
                name="learned_product",
                passed=product_count > 0 if fixture.requires_product else True,
                detail=f"learned {product_count} product candidates",
            ),
            CompanyBrainEvalCheck(
                name="learned_knowledge",
                passed=knowledge_count > 0 if fixture.requires_knowledge else True,
                detail=f"learned {knowledge_count} knowledge candidates",
            ),
            CompanyBrainEvalCheck(
                name="retrieval_hit",
                passed=retrieval_hit,
                detail=f"query={fixture.query_text!r}",
            ),
        ]
        requires_media = (
            fixture.requires_media
            if fixture.requires_media is not None
            else fixture.source_kind in {"pdf", "screenshot", "website", "telegram_channel"}
        )
        if requires_media:
            checks.append(
                CompanyBrainEvalCheck(
                    name="media_evidence",
                    passed=media_count > 0,
                    detail=f"retrieved {media_count} media refs",
                )
            )
        if embed_source_units:
            checks.append(
                CompanyBrainEvalCheck(
                    name="embedding_ready",
                    passed=index_states["ready"] > 0,
                    detail=(
                        f"ready={index_states['ready']} pending={index_states['pending']} "
                        f"degraded={index_states['degraded']}"
                    ),
                )
            )
        results.append(
            CompanyBrainSourceEvalResult(
                source_id=fixture.source_id,
                source_kind=fixture.source_kind,
                description=fixture.description,
                passed=all(check.passed for check in checks),
                product_count=product_count,
                knowledge_count=knowledge_count,
                media_count=media_count,
                learned_product_titles=_product_titles(product_facts),
                retrieved_product_titles=retrieved_titles,
                retrieved_media_refs=retrieved_media_refs,
                deferred_media_count=deferred_media_count,
                embedding_ready_count=index_states["ready"],
                embedding_degraded_count=index_states["degraded"],
                embedding_pending_count=index_states["pending"],
                embedding_model_ids=embedding_model_ids,
                duration_ms=int((time.monotonic() - fixture_started) * 1000),
                retrieval_hit=retrieval_hit,
                degraded_reasons=degraded,
                checks=checks,
            )
        )

    product_total = sum(result.product_count for result in results)
    knowledge_total = sum(result.knowledge_count for result in results)
    media_total = sum(result.media_count for result in results)
    conversation_pair_total = sum(result.conversation_pair_count for result in results)
    passed = sum(1 for result in results if result.passed)
    retrieval_passes = sum(1 for result in results if result.retrieval_hit)
    durations = [result.duration_ms for result in results]
    return CompanyBrainEvalSuiteReport(
        suite="company-brain-mixed-sources",
        total_sources=len(results),
        passed_sources=passed,
        pass_rate=(passed / len(results)) if results else 0.0,
        retrieval_pass_rate=(retrieval_passes / len(results)) if results else 0.0,
        product_count=product_total,
        knowledge_count=knowledge_total,
        conversation_pair_count=conversation_pair_total,
        media_count=media_total,
        deferred_media_count=sum(result.deferred_media_count for result in results),
        embedding_ready_count=sum(result.embedding_ready_count for result in results),
        embedding_degraded_count=sum(result.embedding_degraded_count for result in results),
        embedding_pending_count=sum(result.embedding_pending_count for result in results),
        embedding_model_ids=_unique(
            model_id
            for result in results
            for model_id in result.embedding_model_ids
        ),
        duration_ms=int((time.monotonic() - started) * 1000),
        median_source_duration_ms=_percentile_ms(durations, 0.5),
        p95_source_duration_ms=_percentile_ms(durations, 0.95),
        max_source_duration_ms=max(durations, default=0),
        live_provider=provider_factory is not None,
        semantic_retrieval_enabled=enable_semantic_retrieval,
        contextual_source_units_enabled=contextualize_source_units,
        hallucinated_source_ref_failures=[],
        results=results,
    )


async def _embedding_state_counts(
    *,
    repository: CommercialSpineRepository,
    workspace_id: int,
    fact_ids: list[str],
) -> dict[str, int]:
    counts = {"ready": 0, "degraded": 0, "pending": 0}
    for fact_id in fact_ids:
        records = await repository.list_index_records(
            workspace_id=workspace_id,
            fact_id=fact_id,
        )
        for record in records:
            state = str(record.embedding_state or "pending")
            if state not in counts:
                continue
            counts[state] += 1
    return counts


def _source_evidence_refs(
    *,
    source_ref: str,
    source_fact_id: str,
    source_units: tuple[Any, ...],
    media_assets: list[Any],
) -> set[str]:
    refs = {source_ref, source_fact_id}
    for unit in source_units:
        unit_ref = getattr(unit, "unit_ref", None)
        if unit_ref:
            refs.add(str(unit_ref))
    for asset in media_assets:
        media_ref = getattr(asset, "media_ref", None)
        if media_ref:
            refs.add(str(media_ref))
    return refs


def _facts_with_source_refs(
    facts: tuple[Any, ...],
    evidence_refs: set[str],
) -> list[Any]:
    return [
        fact
        for fact in facts
        if evidence_refs.intersection(str(ref) for ref in fact.source_refs)
    ]


def _product_titles(facts: list[Any]) -> list[str]:
    return _unique(
        str((fact.value.get("title") if isinstance(fact.value, dict) else "") or "")
        for fact in facts
    )


def _candidate_media_refs(value: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("media_refs", "media", "images"):
        raw = value.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    refs.append(item)
                elif isinstance(item, dict):
                    ref = str(item.get("media_ref") or item.get("asset_id") or item.get("url") or "").strip()
                    if ref:
                        refs.append(ref)
    return _unique(refs)


async def _embedding_model_ids(
    *,
    repository: CommercialSpineRepository,
    workspace_id: int,
    fact_ids: list[str],
) -> list[str]:
    model_ids: list[str] = []
    for fact_id in fact_ids:
        records = await repository.list_index_records(
            workspace_id=workspace_id,
            fact_id=fact_id,
        )
        model_ids.extend(str(record.embedding_model) for record in records if record.embedding_model)
    return _unique(model_ids)


def _deterministic_company_brain_provider(
    fixtures: tuple[CompanyBrainEvalFixture, ...],
) -> Callable[[Any], Awaitable[LLMProviderResponse]]:
    async def provider(request: Any) -> LLMProviderResponse:
        if request.output_schema_name == "SourceUnitContextualizationOutput":
            source_text = str(request.input_payload.get("source_text") or "")
            return LLMProviderResponse(
                text=json.dumps(
                    {
                        "schema_version": "source_unit_contextualization_output.v1",
                        "context": (
                            "Company Brain eval source unit for retrieval. "
                            f"{source_text[:300]}"
                        ),
                    }
                ),
                model_used="deterministic-company-brain-contextualizer",
            )
        source_ref = str(request.input_payload.get("source_ref") or "")
        fixture = next(item for item in fixtures if item.source_ref == source_ref)
        evidence_ref = _request_evidence_ref(request)
        output = _source_learning_output(fixture, evidence_ref=evidence_ref)
        return LLMProviderResponse(
            text=json.dumps(output),
            model_used="deterministic-company-brain-eval",
        )

    return provider


def _source_learning_output(
    fixture: CompanyBrainEvalFixture,
    *,
    evidence_ref: str,
) -> dict[str, Any]:
    if fixture.source_kind == "past_conversation":
        turns = fixture.source_payload.get("turns")
        customer_turn = ""
        seller_turn = ""
        if isinstance(turns, list):
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                sender = str(turn.get("sender_type") or "")
                content = str(turn.get("content") or "").strip()
                if sender == "customer" and not customer_turn:
                    customer_turn = content
                if sender == "seller" and not seller_turn:
                    seller_turn = content
        return {
            "schema_version": "business_source_learning_output.v1",
            "catalog_candidates": [],
            "memory_candidates": [
                {
                    "fact_id": f"conversation_pair:{fixture.source_id}:approved",
                    "fact_type": "conversation_pair_fact",
                    "entity_ref": "business:conversation_pairs",
                    "value": {
                        "customer_turn": customer_turn,
                        "seller_turn": seller_turn,
                        "intent": "delivery_question",
                        "source_refs": [evidence_ref],
                    },
                    "confidence": 0.86,
                    "risk_tier": "low",
                    "evidence_refs": [evidence_ref],
                }
            ],
        }
    products, knowledge = _fixture_expected_payload(fixture)
    catalog_candidates = []
    for product in products:
        media = []
        if evidence_ref.startswith("source_media:"):
            media.append(
                {
                    "source_media_ref": evidence_ref,
                    "media_type": "source_page",
                    "quality_state": "page_media_only",
                    "crop_state": "pending",
                }
            )
        elif fixture.source_kind == "website":
            media.append(
                {
                    "media_ref": f"catalog_media:{product['ref']}:website-image",
                    "url": "https://example.test/static/product.jpg",
                    "media_type": "image",
                    "quality_state": "source_link_only",
                    "crop_state": "pending",
                }
            )
        elif fixture.source_kind == "telegram_channel":
            media.append(
                {
                    "media_ref": f"catalog_media:{product['ref']}:channel-image",
                    "url": "https://cdn.example.test/channel-product.jpg",
                    "media_type": "image",
                    "quality_state": "source_link_only",
                    "crop_state": "pending",
                }
            )
        catalog_candidates.append(
            {
                "product_ref": product["ref"],
                "product": {
                    "title": product["title"],
                    "category": product["category"],
                    "description": product["description"],
                    "attributes": product.get("attributes", {}),
                },
                "variants": product.get("variants", []),
                "offers": [
                    {
                        "offer_ref": f"{product['ref']}:offer",
                        "product_ref": product["ref"],
                        "price": {"amount": product["price"], "currency": "UZS"},
                        "stock": {"state": "available"},
                        "active": True,
                    }
                ],
                "media": media,
                "source_fact": {
                    "source_ref": fixture.source_ref,
                    "source_type": fixture.source_kind,
                    "content_refs": [evidence_ref],
                    "extraction_state": "proposed",
                },
                "confidence": 0.82,
                "risk_tier": "medium",
                "evidence_refs": [evidence_ref],
            }
        )
    memory_candidates = [
        {
            "fact_id": item["fact_id"],
            "fact_type": "knowledge_fact",
            "entity_ref": item["entity_ref"],
            "value": {
                "topic": item["topic"],
                "question": item["question"],
                "answer": item["answer"],
            },
            "confidence": 0.84,
            "risk_tier": "low",
            "evidence_refs": [evidence_ref],
        }
        for item in knowledge
    ]
    return {
        "schema_version": "business_source_learning_output.v1",
        "catalog_candidates": catalog_candidates,
        "memory_candidates": memory_candidates,
    }


def _request_evidence_ref(request: Any) -> str:
    analyzed = request.input_payload.get("analyzed_media_refs")
    if isinstance(analyzed, list) and analyzed:
        return str(analyzed[0])
    units = request.input_payload.get("source_units")
    if isinstance(units, list) and units:
        unit_ref = units[0].get("unit_ref") if isinstance(units[0], dict) else None
        if unit_ref:
            return str(unit_ref)
    allowed = request.input_payload.get("allowed_evidence_refs")
    if isinstance(allowed, list) and allowed:
        return str(allowed[0])
    return str(request.input_payload.get("source_ref") or "source:missing")


def _fixture_fetcher(
    fixtures: tuple[CompanyBrainEvalFixture, ...],
) -> Callable[[str], Awaitable[SourceFetchResult]]:
    async def fetch(url: str) -> SourceFetchResult:
        fixture = next(item for item in fixtures if item.source_payload.get("url") == url)
        return SourceFetchResult(
            content=str(fixture.source_payload["html"]).encode("utf-8"),
            content_type="text/html; charset=utf-8",
            final_url=url,
        )

    return fetch


def _company_brain_fixtures() -> tuple[CompanyBrainEvalFixture, ...]:
    return (
        CompanyBrainEvalFixture(
            source_id="owner_text",
            source_kind="text",
            description="Owner pasted product list and FAQ text.",
            source_ref="eval:text:owner-products",
            source_payload={
                "text": (
                    "Atlas Mini blender - 210000 UZS, compact kitchen blender. "
                    "Nova Bottle - 89000 UZS, insulated bottle. "
                    "FAQ: Toshkent ichida yetkazib berish 24 soat."
                )
            },
            content_bytes=None,
            query_text="Atlas Mini blender narxi",
            expected_product_title="Atlas Mini blender",
        ),
        CompanyBrainEvalFixture(
            source_id="webpage",
            source_kind="website",
            description="Website page with product, FAQ, and image.",
            source_ref="eval:website:prism",
            source_payload={
                "url": "https://example.test/prism-hoodie",
                "html": """
                <html><head><title>Prism Hoodie</title>
                <meta property="og:image" content="/static/prism-hoodie.jpg"></head>
                <body><h1>Prism Hoodie</h1><p>Price: 320000 UZS.</p>
                <p>FAQ: returns accepted within 7 days if unused.</p>
                <img src="/static/prism-hoodie.jpg" alt="Prism Hoodie front view"></body></html>
                """,
            },
            content_bytes=None,
            query_text="Prism Hoodie qaytarish",
            expected_product_title="Prism Hoodie",
        ),
        CompanyBrainEvalFixture(
            source_id="support_center_site",
            source_kind="website",
            description="Company support website with SLA, refund, and escalation KB.",
            source_ref="eval:website:support-center",
            source_payload={
                "url": "https://example.test/support-center",
                "html": """
                <html><head><title>OQIM Academy Support</title></head>
                <body>
                <h1>OQIM Academy Support Center</h1>
                <p>Mentors reply within 24 hours on business days.</p>
                <p>Students can request a refund before the first live session.</p>
                <p>Escalate billing issues to billing@oqim.test.</p>
                <p>The company voice is practical, clear, and supportive.</p>
                </body></html>
                """,
            },
            content_bytes=None,
            query_text="mentor javobi qancha vaqtda keladi",
            expected_product_title=None,
            expected_retrieval_text="24 hours",
            requires_product=False,
            requires_knowledge=True,
            requires_media=False,
        ),
        CompanyBrainEvalFixture(
            source_id="screenshot",
            source_kind="screenshot",
            description="Uploaded screenshot with product and support note.",
            source_ref="eval:screenshot:luna-ring",
            source_payload={
                "file_name": "luna-ring-screenshot.png",
                "content_type": "image/png",
                "caption": "Luna Ring screenshot",
            },
            content_bytes=_image_bytes(
                "Luna Ring\n925 silver\nPrice 180000 UZS\nFAQ: sizes 16-20"
            ),
            query_text="Luna Ring 925 silver",
            expected_product_title="Luna Ring",
        ),
        CompanyBrainEvalFixture(
            source_id="pdf",
            source_kind="pdf",
            description="Image-only PDF brochure page.",
            source_ref="eval:pdf:navo-candle",
            source_payload={"file_name": "navo-candle.pdf"},
            content_bytes=_image_pdf_bytes(
                "Navo Candle Set\nSoy wax gift set\nPrice 95000 UZS\nFAQ: gift wrap available"
            ),
            query_text="Navo Candle gift wrap",
            expected_product_title="Navo Candle Set",
        ),
        CompanyBrainEvalFixture(
            source_id="startup_program_pdf",
            source_kind="pdf",
            description="Program PDF with startup application, grant, and date KB.",
            source_ref="eval:pdf:startup-sprint",
            source_payload={"file_name": "startup-sprint-program.pdf"},
            content_bytes=_image_pdf_bytes(
                "Startup Sprint Program\nApplications close June 10\n"
                "Grants up to $5,000\nWeekly office hours\nPitch day July 20\n"
                "Voice: concise, ambitious, founder-friendly"
            ),
            query_text="Startup Sprint grant amount and office hours",
            expected_product_title=None,
            expected_retrieval_text="$5,000",
            requires_product=False,
            requires_knowledge=True,
            requires_media=False,
        ),
        CompanyBrainEvalFixture(
            source_id="clinic_policy_pdf",
            source_kind="pdf",
            description="Messy clinic service PDF with appointments, safety policy, and escalation KB.",
            source_ref="eval:pdf:clinic-policy",
            source_payload={"file_name": "clinic-policy-messy.pdf"},
            content_bytes=_image_pdf_bytes(
                "CLINIC GUIDE / page 1\n"
                "appointments -> cardiology and lab tests require booking 1 day before\n"
                "urgent symptoms: hand off to duty doctor immediately\n"
                "refunds: lab package refund before sample collection only\n"
                "contact: clinic-support@oqim.test"
            ),
            query_text="clinic lab refund and duty doctor escalation",
            expected_product_title=None,
            expected_retrieval_text="before sample collection",
            requires_product=False,
            requires_knowledge=True,
            requires_media=False,
        ),
        CompanyBrainEvalFixture(
            source_id="real_estate_company_pdf",
            source_kind="pdf",
            description="Messy real-estate agency PDF with viewing, deposit, and handoff rules.",
            source_ref="eval:pdf:real-estate-company",
            source_payload={"file_name": "real-estate-company-info.pdf"},
            content_bytes=_image_pdf_bytes(
                "AGENCY NOTES // not a product catalog\n"
                "viewing windows: weekdays 10:00-18:00, Sunday by appointment\n"
                "reservation deposit is 2% and must be confirmed by manager\n"
                "do not promise mortgage approval; collect budget and district first"
            ),
            query_text="real estate viewing deposit mortgage approval rule",
            expected_product_title=None,
            expected_retrieval_text="2%",
            requires_product=False,
            requires_knowledge=True,
            requires_media=False,
        ),
        CompanyBrainEvalFixture(
            source_id="course_terms_messy_pdf",
            source_kind="pdf",
            description="Messy course terms PDF with schedule, certificate, and support FAQ.",
            source_ref="eval:pdf:course-terms",
            source_payload={"file_name": "course-terms-messy.pdf"},
            content_bytes=_image_pdf_bytes(
                "COURSE TERMS\n"
                "Evening cohort: Mon/Wed/Fri 19:30\n"
                "certificate: issued after final project review\n"
                "mentor response SLA: 24 business hours\n"
                "payment split: 50% before start, 50% before week 3"
            ),
            query_text="evening cohort certificate mentor response payment split",
            expected_product_title=None,
            expected_retrieval_text="19:30",
            requires_product=False,
            requires_knowledge=True,
            requires_media=False,
        ),
        CompanyBrainEvalFixture(
            source_id="telegram_channel",
            source_kind="telegram_channel",
            description="Telegram channel posts with product text, FAQ, and image media.",
            source_ref="eval:telegram:@nafis:may",
            source_payload={
                "channel_id": "@nafis",
                "messages": [
                    {
                        "message_id": "900",
                        "text": "Yashil Atlas Dress. Narxi 250000 UZS. Toshkent yetkazish bugun.",
                        "media_type": "photo",
                        "media_metadata": {
                            "mime_type": "image/jpeg",
                            "url": "https://cdn.example.test/yashil-atlas.jpg",
                        },
                    }
                ],
            },
            content_bytes=None,
            query_text="Yashil Atlas Dress yetkazish",
            expected_product_title="Yashil Atlas Dress",
            requires_media=True,
        ),
        CompanyBrainEvalFixture(
            source_id="spreadsheet",
            source_kind="spreadsheet",
            description="CSV/XLSX-like price list normalized as spreadsheet rows.",
            source_ref="eval:spreadsheet:price-list",
            source_payload={
                "file_name": "price-list.csv",
                "content_type": "text/csv",
            },
            content_bytes=(
                "name,price,currency,stock\n"
                "Madelyn Ring,155000,UZS,8\n"
                "Samar Wallet,220000,UZS,3\n"
            ).encode("utf-8"),
            query_text="Madelyn Ring price stock",
            expected_product_title="Madelyn Ring",
            requires_knowledge=False,
            requires_media=False,
        ),
        CompanyBrainEvalFixture(
            source_id="voice_note",
            source_kind="voice_note",
            description="Owner voice note transcript with product and seller rule.",
            source_ref="eval:voice:owner-rule",
            source_payload={
                "file_name": "voice-rule.ogg",
                "content_type": "audio/ogg",
                "transcript": (
                    "Ravza Tea Set narxi 175000 so'm. Mijoz ulgurji so'rasa "
                    "5 tadan boshlab chegirma borligini ayt."
                ),
            },
            content_bytes=b"fake-voice-note-bytes",
            query_text="Ravza Tea Set ulgurji chegirma",
            expected_product_title="Ravza Tea Set",
            requires_media=False,
        ),
        CompanyBrainEvalFixture(
            source_id="past_conversation",
            source_kind="past_conversation",
            description="Imported past seller/customer conversation pair.",
            source_ref="eval:conversation:approved-pair",
            source_payload={
                "conversation_id": 93001,
                "turns": [
                    {
                        "message_ref": "eval:conversation:customer:1",
                        "sender_type": "customer",
                        "content": "Yetkazib berish bugun bo'ladimi?",
                        "created_at": "2026-05-14T07:00:00+00:00",
                    },
                    {
                        "message_ref": "eval:conversation:seller:2",
                        "sender_type": "seller",
                        "content": "Ha, Toshkent ichida bugun yetkazib beramiz.",
                        "created_at": "2026-05-14T07:01:00+00:00",
                        "quality_label": "approved",
                        "outcome": "continued",
                    },
                ],
            },
            content_bytes=None,
            query_text="bugun yetkazib berish javobi",
            expected_product_title=None,
            expected_retrieval_text="Toshkent ichida bugun yetkazib beramiz",
            requires_product=False,
            requires_knowledge=False,
            requires_media=False,
        ),
    )


def brutal_company_brain_fixtures(
    *,
    pdf_paths: tuple[Path, ...] = (),
) -> tuple[CompanyBrainEvalFixture, ...]:
    """Return live-source fixtures for manual/provider quality probes."""
    fixtures: list[CompanyBrainEvalFixture] = [
        CompanyBrainEvalFixture(
            source_id="uzbek_owner_text",
            source_kind="text",
            description="Uzbek owner text with products, delivery, and automation rule.",
            source_ref="eval:brutal:text:owner",
            source_payload={
                "text": (
                    "Do'konimiz ayollar aksessuarlari sotadi. Mahsulotlar: "
                    "Zarina Silk Scarf narxi 145000 so'm, ipak sharf, ranglari oq va bordo. "
                    "Miro Leather Wallet narxi 220000 so'm, tabiiy charm hamyon. "
                    "FAQ: Toshkent bo'ylab yetkazish 1 kunda, viloyatlarga 2-3 kunda. "
                    "Qoida: mijoz ulgurji so'rasa, 10 tadan boshlab chegirma borligini ayt."
                )
            },
            content_bytes=None,
            query_text="Zarina Silk Scarf narxi va yetkazish",
            expected_product_title="Zarina Silk Scarf",
            requires_media=False,
        ),
        CompanyBrainEvalFixture(
            source_id="books_toscrape_product",
            source_kind="website",
            description="Live product webpage from books.toscrape.com.",
            source_ref="eval:brutal:website:books:a-light-in-the-attic",
            source_payload={
                "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
            },
            content_bytes=None,
            query_text="A Light in the Attic price",
            expected_product_title="A Light in the Attic",
            requires_knowledge=False,
            requires_media=True,
        ),
        CompanyBrainEvalFixture(
            source_id="scrapeme_product",
            source_kind="website",
            description="Live WooCommerce-style product webpage from scrapeme.live.",
            source_ref="eval:brutal:website:scrapeme:bulbasaur",
            source_payload={"url": "https://scrapeme.live/shop/Bulbasaur/"},
            content_bytes=None,
            query_text="Bulbasaur product price",
            expected_product_title="Bulbasaur",
            requires_knowledge=False,
            requires_media=True,
        ),
        CompanyBrainEvalFixture(
            source_id="uzbek_screenshot",
            source_kind="screenshot",
            description="Uploaded screenshot with Uzbek product card and FAQ.",
            source_ref="eval:brutal:screenshot:rayhon-set",
            source_payload={
                "file_name": "rayhon-set-screenshot.png",
                "content_type": "image/png",
                "caption": "Rayhon Set screenshot",
            },
            content_bytes=_image_bytes(
                "Rayhon Set\nAyollar komplekti\nNarxi 390000 so'm\n"
                "O'lchamlar: S, M, L\nFAQ: almashish 3 kun ichida"
            ),
            query_text="Rayhon Set o'lcham va almashish",
            expected_product_title="Rayhon Set",
            requires_media=True,
        ),
    ]
    for index, pdf_path in enumerate(pdf_paths):
        fixtures.append(
            CompanyBrainEvalFixture(
                source_id=f"local_pdf_{index + 1}",
                source_kind="pdf",
                description=f"Local uploaded PDF: {pdf_path.name}",
                source_ref=f"eval:brutal:pdf:{index + 1}:{pdf_path.stem}",
                source_payload={"file_name": pdf_path.name},
                content_bytes=pdf_path.read_bytes(),
                query_text=f"{pdf_path.stem} product information",
                expected_product_title=None,
                requires_knowledge=False,
                requires_media=True,
            )
        )
    return tuple(fixtures)


def macbro_company_brain_fixtures() -> tuple[CompanyBrainEvalFixture, ...]:
    """Return a real Shopify seller fixture for Macbro.uz catalog probes."""
    return (
        CompanyBrainEvalFixture(
            source_id="macbro_shopify_products_json",
            source_kind="website",
            description="Macbro.uz Shopify product JSON with variants, prices, and images.",
            source_ref="eval:macbro:shopify:products-json",
            source_payload={
                "url": "https://macbro.uz/collections/all/products.json?limit=20"
            },
            content_bytes=None,
            query_text="Apple Lightning to Digital AV Adapter narxi",
            expected_product_title="Apple Lightning to Digital AV Adapter",
            requires_knowledge=False,
            requires_media=True,
        ),
    )


def _fixture_expected_payload(
    fixture: CompanyBrainEvalFixture,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if fixture.source_id == "macbro_shopify_products_json":
        return (
            [
                {
                    "ref": "catalog_product:eval:macbro:apple-lightning-digital-av",
                    "title": "Apple Lightning to Digital AV Adapter",
                    "category": "electronics_accessory",
                    "description": (
                        "Apple adapter for HDMI video output from iPhone, iPad, or iPod."
                    ),
                    "price": 804000,
                    "attributes": {
                        "vendor": "Apple",
                        "source": "macbro_shopify_products_json",
                    },
                }
            ],
            [],
        )
    if fixture.source_id == "owner_text":
        return (
            [
                {
                    "ref": "catalog_product:eval:atlas-mini-blender",
                    "title": "Atlas Mini blender",
                    "category": "kitchen",
                    "description": "Compact kitchen blender.",
                    "price": 210000,
                    "attributes": {"kind": "blender"},
                },
                {
                    "ref": "catalog_product:eval:nova-bottle",
                    "title": "Nova Bottle",
                    "category": "drinkware",
                    "description": "Insulated bottle.",
                    "price": 89000,
                    "attributes": {"kind": "bottle"},
                },
            ],
            [
                {
                    "fact_id": "knowledge:eval:text:delivery",
                    "entity_ref": "business:delivery",
                    "topic": "delivery",
                    "question": "Toshkent ichida yetkazish qancha vaqtda?",
                    "answer": "Toshkent ichida yetkazib berish 24 soat.",
                }
            ],
        )
    if fixture.source_id == "webpage":
        return (
            [
                {
                    "ref": "catalog_product:eval:prism-hoodie",
                    "title": "Prism Hoodie",
                    "category": "clothing",
                    "description": "Hoodie from website page.",
                    "price": 320000,
                    "attributes": {"source": "website"},
                }
            ],
            [
                {
                    "fact_id": "knowledge:eval:website:returns",
                    "entity_ref": "business:returns",
                    "topic": "returns",
                    "question": "Qaytarish muddati qancha?",
                    "answer": "Unused items can be returned within 7 days.",
                }
            ],
        )
    if fixture.source_id == "support_center_site":
        return (
            [],
            [
                {
                    "fact_id": "knowledge:eval:support:mentor-sla",
                    "entity_ref": "business:support",
                    "topic": "mentor support SLA",
                    "question": "Mentors reply within how long?",
                    "answer": "Mentors reply within 24 hours on business days.",
                },
                {
                    "fact_id": "knowledge:eval:support:refund",
                    "entity_ref": "business:refunds",
                    "topic": "student refunds",
                    "question": "When can students request a refund?",
                    "answer": "Students can request a refund before the first live session.",
                },
            ],
        )
    if fixture.source_id == "screenshot":
        return (
            [
                {
                    "ref": "catalog_product:eval:luna-ring",
                    "title": "Luna Ring",
                    "category": "jewelry",
                    "description": "925 silver ring.",
                    "price": 180000,
                    "attributes": {"material": "925 silver"},
                }
            ],
            [
                {
                    "fact_id": "knowledge:eval:screenshot:ring-sizes",
                    "entity_ref": "business:sizes",
                    "topic": "ring sizes",
                    "question": "Luna Ring qaysi razmerlarda bor?",
                    "answer": "Sizes 16-20 are shown in the screenshot.",
                }
            ],
        )
    if fixture.source_id == "startup_program_pdf":
        return (
            [],
            [
                {
                    "fact_id": "knowledge:eval:program:startup-grant",
                    "entity_ref": "program:startup_sprint",
                    "topic": "Startup Sprint grant",
                    "question": "How much grant support is available?",
                    "answer": "Startup Sprint grants are up to $5,000.",
                },
                {
                    "fact_id": "knowledge:eval:program:office-hours",
                    "entity_ref": "program:startup_sprint",
                    "topic": "Startup Sprint office hours",
                    "question": "Are office hours available?",
                    "answer": "Startup Sprint includes weekly office hours.",
                },
            ],
        )
    if fixture.source_id == "clinic_policy_pdf":
        return (
            [],
            [
                {
                    "fact_id": "knowledge:eval:clinic:lab-refund",
                    "entity_ref": "clinic:policy",
                    "topic": "clinic lab refund",
                    "question": "When can a lab package be refunded?",
                    "answer": "A lab package can be refunded before sample collection only.",
                },
                {
                    "fact_id": "knowledge:eval:clinic:duty-doctor",
                    "entity_ref": "clinic:safety",
                    "topic": "urgent symptoms handoff",
                    "question": "What happens with urgent symptoms?",
                    "answer": "Urgent symptoms must be handed off to the duty doctor immediately.",
                },
            ],
        )
    if fixture.source_id == "real_estate_company_pdf":
        return (
            [],
            [
                {
                    "fact_id": "knowledge:eval:real-estate:deposit",
                    "entity_ref": "real_estate:reservation",
                    "topic": "reservation deposit",
                    "question": "How much is the reservation deposit?",
                    "answer": "The reservation deposit is 2% and must be confirmed by a manager.",
                },
                {
                    "fact_id": "knowledge:eval:real-estate:mortgage-rule",
                    "entity_ref": "real_estate:seller_rules",
                    "topic": "mortgage approval rule",
                    "question": "Can the agent promise mortgage approval?",
                    "answer": "Do not promise mortgage approval; collect budget and district first.",
                },
            ],
        )
    if fixture.source_id == "course_terms_messy_pdf":
        return (
            [],
            [
                {
                    "fact_id": "knowledge:eval:course:evening-cohort",
                    "entity_ref": "course:terms",
                    "topic": "evening cohort schedule",
                    "question": "When is the evening cohort?",
                    "answer": "The evening cohort runs Mon/Wed/Fri at 19:30.",
                },
                {
                    "fact_id": "knowledge:eval:course:certificate",
                    "entity_ref": "course:certificate",
                    "topic": "certificate requirement",
                    "question": "When is the certificate issued?",
                    "answer": "The certificate is issued after final project review.",
                },
            ],
        )
    if fixture.source_id == "telegram_channel":
        return (
            [
                {
                    "ref": "catalog_product:eval:yashil-atlas-dress",
                    "title": "Yashil Atlas Dress",
                    "category": "women_clothing",
                    "description": "Green atlas dress from Telegram channel.",
                    "price": 250000,
                    "attributes": {"color": "green", "source": "telegram_channel"},
                }
            ],
            [
                {
                    "fact_id": "knowledge:eval:telegram:delivery",
                    "entity_ref": "business:delivery",
                    "topic": "delivery",
                    "question": "Yashil Atlas Dress qachon yetkaziladi?",
                    "answer": "Toshkent ichida bugun yetkazish mumkin.",
                }
            ],
        )
    if fixture.source_id == "spreadsheet":
        return (
            [
                {
                    "ref": "catalog_product:eval:madelyn-ring",
                    "title": "Madelyn Ring",
                    "category": "jewelry",
                    "description": "Ring imported from spreadsheet price list.",
                    "price": 155000,
                    "attributes": {"stock": "8", "source": "spreadsheet"},
                },
                {
                    "ref": "catalog_product:eval:samar-wallet",
                    "title": "Samar Wallet",
                    "category": "accessory",
                    "description": "Wallet imported from spreadsheet price list.",
                    "price": 220000,
                    "attributes": {"stock": "3", "source": "spreadsheet"},
                },
            ],
            [],
        )
    if fixture.source_id == "voice_note":
        return (
            [
                {
                    "ref": "catalog_product:eval:ravza-tea-set",
                    "title": "Ravza Tea Set",
                    "category": "home",
                    "description": "Tea set described by owner voice note.",
                    "price": 175000,
                    "attributes": {"source": "voice_note"},
                }
            ],
            [
                {
                    "fact_id": "knowledge:eval:voice:wholesale",
                    "entity_ref": "business:wholesale_rules",
                    "topic": "wholesale discount",
                    "question": "Ulgurji mijozga nima deyiladi?",
                    "answer": "5 tadan boshlab chegirma borligini ayt.",
                }
            ],
        )
    return (
        [
            {
                "ref": "catalog_product:eval:navo-candle-set",
                "title": "Navo Candle Set",
                "category": "home",
                "description": "Soy wax gift set.",
                "price": 95000,
                "attributes": {"material": "soy wax"},
            }
        ],
        [
            {
                "fact_id": "knowledge:eval:pdf:gift-wrap",
                "entity_ref": "business:gift_wrap",
                "topic": "gift wrap",
                "question": "Sovga o'rami bormi?",
                "answer": "Gift wrap is available.",
            }
        ],
    )


def _image_bytes(text: str) -> bytes:
    image = Image.new("RGB", (900, 540), "white")
    draw = ImageDraw.Draw(image)
    draw.text((36, 40), text, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _image_pdf_bytes(text: str) -> bytes:
    image = Image.new("RGB", (900, 540), "white")
    draw = ImageDraw.Draw(image)
    draw.text((36, 40), text, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PDF")
    return buffer.getvalue()


def _unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _percentile_ms(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int(round((len(ordered) - 1) * percentile))),
    )
    return ordered[index]
