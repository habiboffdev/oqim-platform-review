from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
import json
from typing import Any

from app.brain import reranker
from app.brain.embedding_service import (
    EmbeddingService,
    ensure_embedding_dimensions,
)
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import (
    AgentGroundingBundle,
    ContextualRetrievalCandidate,
    ContextualRetrievalRequest,
    ContextualRetrievalResult,
    ContextualRetrievalTrace,
)
from app.modules.commercial_spine.contracts import BusinessBrainFact
from app.modules.commercial_spine.contracts import LLMGatewayRequest
from app.modules.commercial_spine.llm_gateway import LLMGateway
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.retrieval_core.contracts import (
    RetrievalAgenticSearchOutput,
    RetrievalAgentGroundingRequest,
    RetrievalContextRequest,
    RetrievalQueryRewriteOutput,
)

_ALLOWED_QUERY_MODALITIES = {"text", "image", "audio", "video", "pdf", "file"}
_FACT_TYPE_ALIASES = {
    "media_evidence_fact": ("business_source_media_fact", "catalog_media"),
    "source_media_fact": ("business_source_media_fact",),
}
_DEFAULT_RETRIEVAL_FACT_TYPES = (
    "catalog_product",
    "catalog_variant",
    "catalog_offer",
    "catalog_media",
    "catalog_conflict",
    "business_source_media_fact",
    "knowledge_fact",
    "seller_rule_fact",
    "voice_fact",
    "conversation_pair_fact",
    "correction_episode_fact",
    "payment_state",
    "order_state",
)
RerankProvider = Callable[
    [str, list[dict[str, Any]]],
    Awaitable[list[dict[str, Any]]],
]


class RetrievalCoreService:
    """Shared contextual RAG service.

    Agents and intelligence services should call this boundary instead of
    embedding queries or assembling Business Brain retrieval requests directly.
    It centralizes contextual source-unit retrieval, hybrid lexical/semantic
    channels, rerank state, and degraded retrieval reasons.
    """

    def __init__(
        self,
        *,
        repository: CommercialSpineRepository,
        embedding_service: Any | None = None,
        gateway: LLMGateway | None = None,
        rerank_provider: RerankProvider | None = None,
    ) -> None:
        self._repository = repository
        self._memory = BusinessBrainMemoryService(repository=repository)
        self._embedding_service = embedding_service or EmbeddingService()
        self._gateway = gateway
        self._rerank_provider = rerank_provider

    async def retrieve_contextual(
        self,
        request: RetrievalContextRequest,
    ) -> ContextualRetrievalResult:
        seed_queries = (
            _query_rewrite_values(
                request.search_probes,
                original=request.query_text or "",
                limit=5,
            )
            if request.search_probes
            else []
        )
        seed_fact_types = _fact_type_values(request.search_fact_types, limit=12)
        seed_modalities = _query_modalities(request.search_modalities, limit=6)
        if seed_queries or seed_fact_types or seed_modalities:
            agentic_queries = seed_queries
            agentic_fact_types = seed_fact_types
            agentic_modalities = seed_modalities
            agentic_degraded: list[str] = []
            agentic_trace_ids: list[str] = []
        else:
            agentic_queries, agentic_fact_types, agentic_modalities, agentic_degraded, agentic_trace_ids = (
                await self._agentic_search_plan(request)
            )
        requested_fact_types = (
            list(request.requested_fact_types)
            if request.requested_fact_types
            else list(_DEFAULT_RETRIEVAL_FACT_TYPES)
        )
        planned_fact_types = _unique([*requested_fact_types, *agentic_fact_types])
        planned_modalities = _unique([*request.query_modalities, *agentic_modalities])
        expanded_fact_types = _expanded_fact_types(
            requested_fact_types=planned_fact_types,
            query_modalities=planned_modalities,
        )
        retrieval_request = request.model_copy(
            update={
                "requested_fact_types": expanded_fact_types,
                "query_modalities": planned_modalities,
            }
        )
        if retrieval_request.enable_query_rewrite and agentic_queries:
            query_rewrites = _query_rewrite_values(
                agentic_queries,
                original=retrieval_request.query_text,
                limit=3,
            )
            rewrite_degraded_reasons: list[str] = []
            rewrite_trace_ids: list[str] = []
        else:
            query_rewrites, rewrite_degraded_reasons, rewrite_trace_ids = (
                await self._query_rewrites(retrieval_request)
            )
        query_embedding, degraded_reasons = await self._query_embedding(
            retrieval_request.query_text,
            context="retrieval_core.context",
            enable_semantic=retrieval_request.enable_semantic,
            precomputed_embedding=retrieval_request.query_embedding,
        )
        result = await self._memory.retrieve_contextual(
            ContextualRetrievalRequest(
                workspace_id=retrieval_request.workspace_id,
                requested_fact_types=list(retrieval_request.requested_fact_types),
                entity_refs=list(retrieval_request.entity_refs),
                candidate_fact_ids=list(retrieval_request.candidate_fact_ids),
                requested_slots=list(retrieval_request.requested_slots),
                query_text=retrieval_request.query_text,
                query_modalities=list(retrieval_request.query_modalities),
                query_embedding=query_embedding,
                minimum_lexical_score=retrieval_request.minimum_lexical_score,
                enable_semantic=retrieval_request.enable_semantic,
                enable_rerank=retrieval_request.enable_rerank,
                include_proposed=retrieval_request.include_proposed,
                include_source_units=retrieval_request.include_source_units,
                limit=retrieval_request.limit,
            )
        )
        result = _with_expanded_fact_type_trace(
            result,
            requested_fact_types=request.requested_fact_types,
            expanded_fact_types=expanded_fact_types,
        )
        if retrieval_request.query_text:
            result = await self._add_keyword_candidates(
                request=retrieval_request,
                result=result,
            )
        for rewrite in query_rewrites:
            result = await self._add_keyword_candidates(
                request=retrieval_request.model_copy(update={"query_text": rewrite}),
                result=result,
                score_channel="query_rewrite_keyword",
                retrieval_channel="query_rewrite",
            )
        for query in agentic_queries:
            result = await self._add_keyword_candidates(
                request=retrieval_request.model_copy(update={"query_text": query}),
                result=result,
                score_channel="agentic_keyword",
                retrieval_channel="agentic_search",
            )
        result = _with_query_rewrite_trace(
            result,
            query_rewrites=query_rewrites,
            llm_trace_ids=rewrite_trace_ids,
            degraded_reasons=rewrite_degraded_reasons,
        )
        result = _with_agentic_search_trace(
            result,
            agentic_queries=agentic_queries,
            agentic_fact_types=agentic_fact_types,
            agentic_modalities=agentic_modalities,
            llm_trace_ids=agentic_trace_ids,
            degraded_reasons=agentic_degraded,
        )
        result = _fuse_contextual_result(
            result,
            limit=retrieval_request.limit,
        )
        result = await self._rerank_contextual_result(
            request=retrieval_request,
            result=result,
        )
        return _merge_contextual_degraded(result, degraded_reasons)

    async def build_agent_grounding(
        self,
        request: RetrievalAgentGroundingRequest,
    ) -> AgentGroundingBundle:
        has_query = bool((request.query_text or "").strip())
        enable_query_rewrite = request.enable_query_rewrite or (
            request.enable_contextual_rank and has_query
        )
        enable_rerank = request.enable_rerank or (
            request.enable_contextual_rank and has_query
        )
        result = await self.retrieve_contextual(
            RetrievalContextRequest(
                workspace_id=request.workspace_id,
                requested_fact_types=list(request.requested_fact_types),
                entity_refs=list(request.entity_refs),
                requested_slots=list(request.requested_slots),
                search_probes=list(request.search_probes),
                search_fact_types=list(request.search_fact_types),
                search_modalities=list(request.search_modalities),
                query_text=request.query_text,
                query_modalities=list(request.query_modalities),
                query_embedding=request.query_embedding,
                minimum_lexical_score=request.minimum_lexical_score,
                enable_semantic=request.enable_semantic,
                enable_query_rewrite=enable_query_rewrite,
                enable_agentic_search=request.enable_agentic_search,
                enable_rerank=enable_rerank,
                include_proposed=request.include_proposed,
                include_source_units=True,
            )
        )
        families: dict[str, list[dict[str, Any]]] = {}
        for candidate in result.candidates:
            families.setdefault(candidate.fact_type, []).append(
                candidate.model_dump(mode="json")
            )
        unavailable = [
            fact_type
            for fact_type in request.requested_fact_types
            if fact_type not in families
        ]
        return AgentGroundingBundle(
            workspace_id=request.workspace_id,
            agent_kind=request.agent_kind,
            families=families,
            missing_evidence=list(result.missing_evidence),
            unavailable_families=unavailable,
            degraded_reasons=list(result.degraded_reasons),
            trace=result.trace,
        )

    async def _query_embedding(
        self,
        query_text: str | None,
        *,
        context: str,
        enable_semantic: bool,
        precomputed_embedding: list[float] | None = None,
    ) -> tuple[list[float] | None, list[str]]:
        if precomputed_embedding is not None:
            embedding = ensure_embedding_dimensions(precomputed_embedding, context=context)
            if embedding is None:
                return None, ["query_embedding_dimension_mismatch"]
            return embedding, []
        text = (query_text or "").strip()
        if not enable_semantic or not text:
            return None, []
        try:
            raw = await self._embedding_service.embed_query(text)
        except Exception:
            return None, ["query_embedding_unavailable"]
        embedding = ensure_embedding_dimensions(raw, context=context)
        if embedding is None and raw is not None:
            return None, ["query_embedding_dimension_mismatch"]
        return embedding, []

    async def _add_keyword_candidates(
        self,
        *,
        request: RetrievalContextRequest,
        result: ContextualRetrievalResult,
        score_channel: str = "keyword",
        retrieval_channel: str = "keyword",
    ) -> ContextualRetrievalResult:
        facts = await self._keyword_facts(request)
        if not facts:
            return result
        seen = {candidate.fact_id for candidate in result.candidates}
        candidates = list(result.candidates)
        selected_ids = list(result.trace.selected_fact_ids)
        candidate_scores = dict(result.trace.candidate_scores)
        for fact, score in facts:
            rounded = round(score, 6)
            candidate_scores.setdefault(fact.fact_id, {})[score_channel] = rounded
            if fact.fact_id in seen:
                candidates = [
                    _candidate_with_score(
                        candidate,
                        channel=score_channel,
                        score=rounded,
                    )
                    if candidate.fact_id == fact.fact_id
                    else candidate
                    for candidate in candidates
                ]
                continue
            seen.add(fact.fact_id)
            selected_ids.append(fact.fact_id)
            candidates.append(
                _fact_keyword_candidate(
                    fact,
                    score=score,
                    score_channel=score_channel,
                )
            )
        channels = _unique([*result.trace.retrieval_channels, retrieval_channel])
        trace = result.trace.model_copy(
            update={
                "selected_fact_ids": _unique(selected_ids),
                "retrieval_channels": channels,
                "candidate_scores": candidate_scores,
            }
        )
        candidates.sort(
            key=lambda candidate: -sum(candidate.retrieval_scores.values())
        )
        return result.model_copy(
            update={
                "candidates": candidates[: request.limit],
                "trace": trace,
            }
        )

    async def _query_rewrites(
        self,
        request: RetrievalContextRequest,
    ) -> tuple[list[str], list[str], list[str]]:
        query = (request.query_text or "").strip()
        if not request.enable_query_rewrite or not query:
            return [], [], []
        gateway = self._gateway or LLMGateway(repository=self._repository)
        prompt_id = "retrieval_core.query_rewrite"
        prompt_version = "1.0.0"
        result = await gateway.generate(
            LLMGatewayRequest(
                route_key="structured_fast",
                workflow_name="retrieval_query_rewrite",
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                input_payload={
                    "query_text": query,
                    "requested_fact_types": list(request.requested_fact_types),
                    "requested_slots": list(request.requested_slots),
                    "query_modalities": list(request.query_modalities),
                },
                output_schema_name="RetrievalQueryRewriteOutput",
                workspace_id=request.workspace_id,
                correlation_id=f"retrieval-query-rewrite:{request.workspace_id}",
                source_refs=[],
                budget={"max_rewrites": 3},
                timeout_ms=10_000,
                fallback_policy=["use_original_query"],
            ),
            output_model=RetrievalQueryRewriteOutput,
        )
        if result.status != "ok" or result.parsed_output is None:
            return [], [f"query_rewrite:{result.status}"], [result.trace_id]
        rewrites = _query_rewrite_values(
            result.parsed_output.get("rewrites"),
            original=query,
            limit=3,
        )
        return rewrites, [], [result.trace_id]

    async def _agentic_search_plan(
        self,
        request: RetrievalContextRequest,
    ) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
        query = (request.query_text or "").strip()
        if not request.enable_agentic_search or not query:
            return [], [], [], [], []
        gateway = self._gateway or LLMGateway(repository=self._repository)
        prompt_id = "retrieval_core.agentic_search_plan"
        prompt_version = "1.0.0"
        result = await gateway.generate(
            LLMGatewayRequest(
                route_key="structured_fast",
                workflow_name="retrieval_agentic_search_plan",
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                input_payload={
                    "query_text": query,
                    "requested_fact_types": list(request.requested_fact_types),
                    "requested_slots": list(request.requested_slots),
                    "query_modalities": list(request.query_modalities),
                },
                output_schema_name="RetrievalAgenticSearchOutput",
                workspace_id=request.workspace_id,
                correlation_id=f"retrieval-agentic-search:{request.workspace_id}",
                source_refs=[],
                budget={"max_queries": 5},
                timeout_ms=10_000,
                fallback_policy=["use_original_request"],
            ),
            output_model=RetrievalAgenticSearchOutput,
        )
        if result.status != "ok" or result.parsed_output is None:
            return [], [], [], [f"agentic_search:{result.status}"], [result.trace_id]
        queries = _query_rewrite_values(
            result.parsed_output.get("queries"),
            original=query,
            limit=5,
        )
        fact_types = _fact_type_values(
            result.parsed_output.get("fact_types"),
            limit=12,
        )
        modalities = _query_modalities(
            result.parsed_output.get("query_modalities"),
            limit=6,
        )
        return queries, fact_types, modalities, [], [result.trace_id]

    async def _keyword_facts(
        self,
        request: RetrievalContextRequest,
    ) -> list[tuple[BusinessBrainFact, float]]:
        query = (request.query_text or "").strip()
        if not query:
            return []
        statuses = (
            ("proposed", "active", "confirmed")
            if request.include_proposed
            else ("active", "confirmed")
        )
        facts = await self._keyword_candidate_facts(
            request=request,
            statuses=statuses,
        )
        requested = set(request.requested_fact_types)
        scored: list[tuple[BusinessBrainFact, float]] = []
        for fact in facts:
            if requested and fact.fact_type not in requested:
                continue
            if request.entity_refs and fact.entity_ref not in request.entity_refs:
                continue
            if request.candidate_fact_ids and fact.fact_id not in request.candidate_fact_ids:
                continue
            score = _keyword_score(query, _fact_search_text(fact))
            if score > 0:
                scored.append((fact, score))
        scored.sort(key=lambda item: (-item[1], item[0].fact_id))
        return scored[: request.limit]

    async def _keyword_candidate_facts(
        self,
        *,
        request: RetrievalContextRequest,
        statuses: tuple[str, ...],
    ) -> list[BusinessBrainFact]:
        fact_types = list(request.requested_fact_types)
        if not fact_types:
            return list(
                await self._repository.list_facts(
                    workspace_id=request.workspace_id,
                    statuses=statuses,
                    limit=250,
                )
            )
        facts: list[BusinessBrainFact] = []
        seen: set[str] = set()
        for fact_type in fact_types:
            for fact in await self._repository.list_facts(
                workspace_id=request.workspace_id,
                fact_type=fact_type,
                statuses=statuses,
                limit=250,
            ):
                if fact.fact_id in seen:
                    continue
                seen.add(fact.fact_id)
                facts.append(fact)
        return facts

    async def _rerank_contextual_result(
        self,
        *,
        request: RetrievalContextRequest,
        result: ContextualRetrievalResult,
    ) -> ContextualRetrievalResult:
        if not request.enable_rerank:
            return result
        query = (request.query_text or "").strip()
        if not query:
            return _with_rerank_trace(
                result,
                rerank_state="degraded",
                degraded_reasons=["rerank_query_missing"],
            )
        if not result.candidates:
            return _with_rerank_trace(
                result,
                rerank_state="degraded",
                degraded_reasons=["rerank_candidates_missing"],
            )
        payloads = [
            {
                "fact_id": candidate.fact_id,
                "text": _candidate_rerank_text(candidate),
            }
            for candidate in result.candidates
        ]
        if self._rerank_provider is not None:
            ranked = await self._rerank_provider(query, payloads)
            ranked = ranked[: request.limit]
        else:
            ranked = await reranker.rerank(
                query,
                payloads,
                text_field="text",
                top_n=request.limit,
            )
        if not ranked:
            return _with_rerank_trace(
                result,
                rerank_state="degraded",
                degraded_reasons=["rerank_empty_result"],
            )
        candidates_by_id = {candidate.fact_id: candidate for candidate in result.candidates}
        reranked_candidates: list[ContextualRetrievalCandidate] = []
        candidate_scores = {
            fact_id: dict(scores)
            for fact_id, scores in result.trace.candidate_scores.items()
        }
        rerank_score_seen = False
        for item in ranked:
            fact_id = str(item.get("fact_id") or "").strip()
            candidate = candidates_by_id.get(fact_id)
            if candidate is None:
                continue
            score = _numeric_score(item.get("relevance_score"))
            if score is not None:
                rerank_score_seen = True
                candidate_scores.setdefault(fact_id, {})["rerank"] = round(score, 6)
                candidate = candidate.model_copy(
                    update={
                        "retrieval_scores": {
                            **candidate.retrieval_scores,
                            "rerank": round(score, 6),
                        }
                    }
                )
            reranked_candidates.append(candidate)
        if not reranked_candidates:
            return _with_rerank_trace(
                result,
                rerank_state="degraded",
                degraded_reasons=["rerank_no_matching_candidates"],
            )
        degraded_reasons = [] if rerank_score_seen else ["rerank_unavailable"]
        trace = result.trace.model_copy(
            update={
                "selected_fact_ids": [candidate.fact_id for candidate in reranked_candidates],
                "retrieval_channels": _unique(
                    [*result.trace.retrieval_channels, "rerank"]
                ),
                "candidate_scores": candidate_scores,
                "rerank_state": "requested" if rerank_score_seen else "degraded",
                "degraded_reasons": sorted(
                    _unique([*result.trace.degraded_reasons, *degraded_reasons])
                ),
            }
        )
        return result.model_copy(
            update={
                "candidates": reranked_candidates,
                "degraded_reasons": trace.degraded_reasons,
                "trace": trace,
            }
        )


def _merge_contextual_degraded(
    result: ContextualRetrievalResult,
    degraded_reasons: list[str],
) -> ContextualRetrievalResult:
    if not degraded_reasons:
        return result
    merged = sorted(_unique([*result.degraded_reasons, *degraded_reasons]))
    trace = ContextualRetrievalTrace(
        **{
            **result.trace.model_dump(mode="json"),
            "degraded_reasons": merged,
        }
    )
    return result.model_copy(update={"degraded_reasons": merged, "trace": trace})


def _with_rerank_trace(
    result: ContextualRetrievalResult,
    *,
    rerank_state: str,
    degraded_reasons: list[str],
) -> ContextualRetrievalResult:
    merged = sorted(_unique([*result.degraded_reasons, *degraded_reasons]))
    trace = result.trace.model_copy(
        update={
            "retrieval_channels": _unique(
                [*result.trace.retrieval_channels, "rerank"]
            ),
            "degraded_reasons": merged,
            "rerank_state": rerank_state,
        }
    )
    return result.model_copy(update={"degraded_reasons": merged, "trace": trace})


def _with_query_rewrite_trace(
    result: ContextualRetrievalResult,
    *,
    query_rewrites: list[str],
    llm_trace_ids: list[str],
    degraded_reasons: list[str],
) -> ContextualRetrievalResult:
    if not query_rewrites and not llm_trace_ids and not degraded_reasons:
        return result
    merged_degraded = sorted(_unique([*result.degraded_reasons, *degraded_reasons]))
    trace = result.trace.model_copy(
        update={
            "query_rewrites": _unique([*result.trace.query_rewrites, *query_rewrites]),
            "llm_trace_ids": _unique([*result.trace.llm_trace_ids, *llm_trace_ids]),
            "degraded_reasons": merged_degraded,
        }
    )
    return result.model_copy(
        update={
            "degraded_reasons": merged_degraded,
            "trace": trace,
        }
    )


def _with_agentic_search_trace(
    result: ContextualRetrievalResult,
    *,
    agentic_queries: list[str],
    agentic_fact_types: list[str],
    agentic_modalities: list[str],
    llm_trace_ids: list[str],
    degraded_reasons: list[str],
) -> ContextualRetrievalResult:
    if (
        not agentic_queries
        and not agentic_fact_types
        and not agentic_modalities
        and not llm_trace_ids
        and not degraded_reasons
    ):
        return result
    merged_degraded = sorted(_unique([*result.degraded_reasons, *degraded_reasons]))
    channels = list(result.trace.retrieval_channels)
    if agentic_queries or agentic_fact_types or agentic_modalities:
        channels = _unique([*channels, "agentic_search"])
    trace = result.trace.model_copy(
        update={
            "agentic_queries": _unique(
                [*result.trace.agentic_queries, *agentic_queries]
            ),
            "agentic_fact_types": _unique(
                [*result.trace.agentic_fact_types, *agentic_fact_types]
            ),
            "agentic_modalities": _unique(
                [*result.trace.agentic_modalities, *agentic_modalities]
            ),
            "llm_trace_ids": _unique([*result.trace.llm_trace_ids, *llm_trace_ids]),
            "retrieval_channels": channels,
            "degraded_reasons": merged_degraded,
        }
    )
    return result.model_copy(
        update={
            "degraded_reasons": merged_degraded,
            "trace": trace,
        }
    )


def _with_expanded_fact_type_trace(
    result: ContextualRetrievalResult,
    *,
    requested_fact_types: list[str],
    expanded_fact_types: list[str],
) -> ContextualRetrievalResult:
    if expanded_fact_types == requested_fact_types:
        return result
    trace = result.trace.model_copy(
        update={
            "expanded_fact_types": expanded_fact_types,
            "retrieval_channels": _unique(
                [*result.trace.retrieval_channels, "multimodal"]
            ),
        }
    )
    return result.model_copy(update={"trace": trace})


def _fuse_contextual_result(
    result: ContextualRetrievalResult,
    *,
    limit: int,
) -> ContextualRetrievalResult:
    candidates_by_id = {candidate.fact_id: candidate for candidate in result.candidates}
    channel_rankings = _channel_rankings(
        result.trace.candidate_scores,
        candidate_ids=set(candidates_by_id),
    )
    if len(channel_rankings) < 2:
        return result
    fusion_scores: dict[str, float] = {}
    for ranked_ids in channel_rankings.values():
        for index, fact_id in enumerate(ranked_ids, start=1):
            fusion_scores[fact_id] = fusion_scores.get(fact_id, 0.0) + (
                1.0 / (60.0 + index)
            )
    if not fusion_scores:
        return result
    candidate_scores = {
        fact_id: dict(scores)
        for fact_id, scores in result.trace.candidate_scores.items()
    }
    fused_candidates: list[ContextualRetrievalCandidate] = []
    for candidate in result.candidates:
        score = fusion_scores.get(candidate.fact_id)
        if score is None:
            fused_candidates.append(candidate)
            continue
        rounded = round(score, 6)
        candidate_scores.setdefault(candidate.fact_id, {})["fusion"] = rounded
        fused_candidates.append(
            candidate.model_copy(
                update={
                    "retrieval_scores": {
                        **candidate.retrieval_scores,
                        "fusion": rounded,
                    }
                }
            )
        )
    fused_candidates.sort(
        key=lambda candidate: (
            -fusion_scores.get(candidate.fact_id, 0.0),
            -_non_fusion_score(candidate_scores.get(candidate.fact_id, {})),
            candidate.fact_id,
        )
    )
    fused_candidates = fused_candidates[:limit]
    trace = result.trace.model_copy(
        update={
            "selected_fact_ids": [candidate.fact_id for candidate in fused_candidates],
            "retrieval_channels": _unique(
                [*result.trace.retrieval_channels, "fusion"]
            ),
            "candidate_scores": candidate_scores,
        }
    )
    return result.model_copy(
        update={
            "candidates": fused_candidates,
            "trace": trace,
        }
    )


def _channel_rankings(
    candidate_scores: dict[str, dict[str, float]],
    *,
    candidate_ids: set[str],
) -> dict[str, list[str]]:
    channels = {
        channel
        for scores in candidate_scores.values()
        for channel in scores
        if channel not in {"fusion", "rerank"}
    }
    rankings: dict[str, list[str]] = {}
    for channel in sorted(channels):
        ranked = [
            (fact_id, float(scores[channel]))
            for fact_id, scores in candidate_scores.items()
            if fact_id in candidate_ids and channel in scores
        ]
        if not ranked:
            continue
        ranked.sort(key=lambda item: (-item[1], item[0]))
        rankings[channel] = [fact_id for fact_id, _score in ranked]
    return rankings


def _non_fusion_score(scores: dict[str, float]) -> float:
    return sum(score for channel, score in scores.items() if channel != "fusion")


def _expanded_fact_types(
    *,
    requested_fact_types: list[str],
    query_modalities: list[str],
) -> list[str]:
    expanded = list(requested_fact_types)
    modalities = {str(modality).strip().lower() for modality in query_modalities}
    if not modalities.intersection({"image", "video", "audio", "pdf", "file"}):
        return expanded
    if not expanded:
        expanded.extend(["business_source_media_fact", "catalog_media"])
    if "catalog_product" in expanded:
        expanded.append("catalog_media")
        expanded.append("business_source_media_fact")
    if "business_source_fact" in expanded:
        expanded.append("business_source_media_fact")
    return _unique(expanded)


def _fact_keyword_candidate(
    fact: BusinessBrainFact,
    *,
    score: float,
    score_channel: str = "keyword",
) -> ContextualRetrievalCandidate:
    return ContextualRetrievalCandidate(
        fact_id=fact.fact_id,
        fact_type=fact.fact_type,
        entity_ref=fact.entity_ref,
        value=dict(fact.value),
        source_refs=list(fact.source_refs),
        confidence=fact.confidence,
        risk_tier=fact.risk_tier,
        status=fact.status,
        freshness={"state": "unknown"},
        contextual_text=_fact_search_text(fact),
        retrieval_scores={score_channel: round(score, 6)},
        source_units=[],
    )


def _candidate_with_score(
    candidate: ContextualRetrievalCandidate,
    *,
    channel: str,
    score: float,
) -> ContextualRetrievalCandidate:
    return candidate.model_copy(
        update={
            "retrieval_scores": {
                **candidate.retrieval_scores,
                channel: round(score, 6),
            }
        }
    )


def _candidate_rerank_text(candidate: ContextualRetrievalCandidate) -> str:
    values = [
        candidate.fact_type,
        candidate.entity_ref,
        candidate.contextual_text or "",
        _value_text(candidate.value),
    ]
    values.extend(unit.source_text or "" for unit in candidate.source_units)
    return "\n".join(value for value in values if value)


def _value_text(value: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "title",
        "name",
        "topic",
        "question",
        "answer",
        "rule",
        "requirement",
        "instruction",
        "instructions",
        "description",
        "summary",
        "observations",
        "details",
        "customer_turn",
        "seller_turn",
        "outcome",
        "quality_label",
        "source_kind",
    ):
        item = value.get(key)
        if item is not None:
            parts.append(_value_part_text(item))
    return "\n".join(parts)


def _numeric_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _query_rewrite_values(
    raw: Any,
    *,
    original: str,
    limit: int,
) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen = {original.casefold()}
    for value in raw:
        item = " ".join(str(value or "").split())
        if not item:
            continue
        folded = item.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _string_values(raw: Any, *, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        item = " ".join(str(value or "").split())
        if not item:
            continue
        folded = item.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _fact_type_values(raw: Any, *, limit: int) -> list[str]:
    values: list[str] = []
    for item in _string_values(raw, limit=limit):
        normalized = item.strip().lower()
        aliases = _FACT_TYPE_ALIASES.get(normalized)
        if aliases is None:
            values.append(item)
        else:
            values.extend(aliases)
        if len(values) >= limit:
            break
    return _unique(values)[:limit]


def _query_modalities(raw: Any, *, limit: int) -> list[str]:
    result: list[str] = []
    for item in _string_values(raw, limit=limit):
        normalized = item.lower()
        if normalized in _ALLOWED_QUERY_MODALITIES:
            result.append(normalized)
    return result


def _fact_search_text(fact: BusinessBrainFact) -> str:
    value = fact.value if isinstance(fact.value, dict) else {}
    return "\n".join(
        [
            fact.fact_id,
            fact.fact_type,
            fact.entity_ref,
            _value_text(value),
            str(value.get("alt_text") or ""),
            str(value.get("media_ref") or ""),
            str(value.get("source_media_ref") or ""),
            str(value.get("product_ref") or ""),
            str(value.get("identity_ref") or ""),
        ]
    )


def _value_part_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    if isinstance(value, list):
        return "\n".join(_value_part_text(item) for item in value if item is not None)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _keyword_score(query_text: str, searchable_text: str) -> float:
    query = query_text.lower()
    searchable = searchable_text.lower()
    score = 0.0
    if searchable and searchable in query:
        score += 4.0
    for line in searchable.splitlines():
        item = line.strip()
        if len(item) > 1 and item in query:
            score += 4.0
            break
    query_tokens = _tokens(query)
    if query_tokens:
        searchable_tokens = set(_tokens(searchable))
        overlap = [token for token in query_tokens if token in searchable_tokens]
        score += len(overlap) / len(query_tokens)
    return score


def _tokens(value: str) -> list[str]:
    ignored = {"narxi", "price", "uzs", "sum", "som", "so", "m", "to"}
    normalized = "".join(
        character.lower() if character.isalnum() else " "
        for character in value
    )
    return [
        token
        for token in normalized.split()
        if len(token) > 1 and token not in ignored
    ]


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
