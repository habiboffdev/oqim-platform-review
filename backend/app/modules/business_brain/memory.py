from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.modules.business_brain.contracts import (
    BusinessBrainFactUpdateInput,
    BusinessBrainIndexRecordContract,
    BusinessBrainWriteResult,
)
from app.modules.business_brain.memory_contracts import (
    AgentGroundingBundle,
    AgentGroundingRequest,
    ContextualRetrievalCandidate,
    ContextualRetrievalRequest,
    ContextualRetrievalResult,
    ContextualRetrievalTrace,
    ConversationPairMiningInput,
    ConversationPairMiningResult,
    CorrectionEpisodeInput,
    LearningLabExport,
    MemoryFactWriteInput,
    RuleCompilationRequest,
    SourceUnitContextualizationOutput,
    SourceUnitRebuildRequest,
    SourceUnitRebuildResult,
    VoiceProjectionRequest,
)
from app.modules.business_brain.write_service import BusinessBrainWriteService
from app.modules.commercial_spine.contracts import (
    BusinessBrainFact,
    BusinessBrainProjection,
    CommercialActionProposal,
    LLMGatewayRequest,
)
from app.modules.commercial_spine.llm_gateway import LLMGateway
from app.modules.commercial_spine.repository import CommercialSpineRepository

ACTIVE_STATUSES = ("active", "confirmed")
VISIBLE_STATUSES = (
    "active",
    "confirmed",
    "proposed",
    "rejected",
    "superseded",
    "historical",
    "expired",
    "conflicted",
    "degraded",
)
TRAINING_FACT_TYPES = ("conversation_pair_fact", "correction_episode_fact")
# Structured business truth the agent grounds on. Source units + conversation
# pairs are embedded by the onboarding ingestion / pair-miner paths, but these
# extracted facts were never indexed — so semantic (incl. cross-lingual) search
# could not surface the catalog/KB/rules. index_structured_facts_for_search embeds
# them. (business_source* are already indexed by onboarding; pairs by the miner.)
SEARCHABLE_STRUCTURED_FACT_TYPES = (
    "catalog_product",
    "catalog_media",
    "catalog_variant",
    "catalog_offer",
    "knowledge_fact",
    "seller_rule_fact",
    "voice_fact",
)
GOOD_QUALITY = ("approved", "high")
EXCLUDED_QUALITY = ("low", "rejected", "outdated")
class BusinessBrainMemoryService:
    def __init__(
        self,
        *,
        repository: CommercialSpineRepository,
        gateway: LLMGateway | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._write = BusinessBrainWriteService(repository=repository)

    async def write_memory_fact(
        self,
        request: MemoryFactWriteInput,
    ) -> BusinessBrainWriteResult:
        result = await self._write.apply(
            BusinessBrainFactUpdateInput(
                update_id=f"memory:{request.fact_id}:{request.correlation_id}",
                fact_id=request.fact_id,
                workspace_id=request.workspace_id,
                fact_type=request.fact_type,
                entity_ref=request.entity_ref,
                value=dict(request.value),
                confidence=request.confidence,
                status=request.status,  # type: ignore[arg-type]
                risk_tier=request.risk_tier,
                source=request.source,  # type: ignore[arg-type]
                approval_state=request.approval_state,  # type: ignore[arg-type]
                source_refs=list(request.source_refs),
                idempotency_key=f"memory:{request.idempotency_key}",
                supersedes_fact_id=request.supersedes_fact_id,
                actor_type="agent" if request.source == "ai_proposal" else "owner",
                actor_ref=request.actor_ref or "business_brain_memory",
                correlation_id=request.correlation_id,
            )
        )
        return result

    async def compile_rule_to_proposal(
        self,
        request: RuleCompilationRequest,
    ) -> CommercialActionProposal:
        fact = await self._repository.get_fact(
            workspace_id=request.workspace_id,
            fact_id=request.rule_fact_id,
        )
        if fact is None:
            raise ValueError("seller rule fact not found")
        value = dict(fact.value)
        can_prepare = (
            fact.status in ACTIVE_STATUSES
            and value.get("mode") == "automation_candidate"
            and fact.risk_tier in {"low", "medium"}
        )
        proposal = CommercialActionProposal(
            proposal_id=f"proposal-{uuid.uuid4().hex}",
            workspace_id=request.workspace_id,
            conversation_id=request.conversation_id,
            customer_id=request.customer_id,
            action_type="compile_automation_rule",
            lifecycle_state="waiting_approval" if can_prepare else "blocked",
            execution_mode=(
                "ask_seller_confirmation" if can_prepare else "blocked_until_evidence"
            ),
            risk_level=fact.risk_tier,
            requires_approval=True,
            priority="medium",
            confidence=fact.confidence,
            reason_code="seller_rule_compilation",
            source_refs=[f"fact:{fact.fact_id}", *fact.source_refs],
            payload={
                "rule_fact_id": fact.fact_id,
                "capability": value.get("capability") or "manual_review",
                "rule": value,
            },
            idempotency_key=f"compile-rule:{request.rule_fact_id}:{request.correlation_id}",
            correlation_id=request.correlation_id,
            trace_id=f"phase4:{request.correlation_id}",
        )
        await self._repository.persist_action_proposal(proposal)
        return proposal

    async def rebuild_voice_projection(
        self,
        request: VoiceProjectionRequest,
    ) -> BusinessBrainProjection:
        facts = await self._repository.list_facts(
            workspace_id=request.workspace_id,
            entity_ref=request.entity_ref,
            fact_type="voice_fact",
            statuses=VISIBLE_STATUSES,
            limit=250,
        )
        traits: list[dict[str, Any]] = []
        source_refs: list[str] = []
        excluded: list[str] = []
        for fact in sorted(facts, key=lambda item: item.fact_id):
            value = dict(fact.value)
            if not _is_active_training_fact(fact):
                excluded.append(fact.fact_id)
                continue
            traits.append(value)
            source_refs.extend(fact.source_refs)
        projection = BusinessBrainProjection(
            projection_ref=f"voice_profile:{request.entity_ref}",
            workspace_id=request.workspace_id,
            projection_type="voice_profile",
            entity_ref=request.entity_ref,
            state={
                "traits": traits,
                "excluded_fact_ids": excluded,
            },
            source_refs=_unique(source_refs),
            degraded=False,
        )
        await self._repository.upsert_projection(projection)
        return projection

    async def mine_conversation_pairs(
        self,
        request: ConversationPairMiningInput,
    ) -> ConversationPairMiningResult:
        pairs: list[BusinessBrainWriteResult] = []
        turns = list(request.turns)
        for index, turn in enumerate(turns):
            if turn.get("sender_type") != "customer":
                continue
            seller = _next_seller_turn(turns[index + 1 :])
            if seller is None:
                continue
            customer_ref = str(turn["message_ref"])
            seller_ref = str(seller["message_ref"])
            # Scoped mining: when a trigger turn is given, only write the pair it
            # completed. Avoids re-writing + re-embedding every historical pair on
            # each seller message (the O(n) storm in the pool-collapse audit, F2).
            if (
                request.trigger_message_ref is not None
                and seller_ref != request.trigger_message_ref
            ):
                continue
            fact_id = f"conversation_pair:{request.conversation_id}:{customer_ref}:{seller_ref}"
            value = {
                "conversation_id": request.conversation_id,
                "context_before": [
                    item.get("content", "")
                    for item in turns[:index]
                    if item.get("content")
                ],
                "customer_turn": turn.get("content", ""),
                "seller_turn": seller.get("content", ""),
                "customer_message_ref": customer_ref,
                "seller_message_ref": seller_ref,
                "customer_media_semantics": turn.get("media_semantics") or {},
                "seller_media_semantics": seller.get("media_semantics") or {},
                "media_semantics": {
                    "customer": turn.get("media_semantics") or {},
                    "seller": seller.get("media_semantics") or {},
                },
                "outcome": seller.get("outcome") or "unknown",
                "quality_label": seller.get("quality_label") or "unreviewed",
                "freshness_anchor": seller.get("created_at"),
            }
            pairs.append(
                await self.write_memory_fact(
                    MemoryFactWriteInput(
                        workspace_id=request.workspace_id,
                        fact_id=fact_id,
                        fact_type="conversation_pair_fact",
                        entity_ref=f"conversation:{request.conversation_id}",
                        value=value,
                        source_refs=_unique([*request.source_refs, customer_ref, seller_ref]),
                        source="replay",
                        status="active",
                        approval_state="confirmed",
                        confidence=0.85,
                        risk_tier="low",
                        correlation_id=request.correlation_id,
                        idempotency_key=f"pair:{fact_id}",
                    )
                )
            )
        return ConversationPairMiningResult(pairs=pairs)

    async def write_correction_episode(
        self,
        request: CorrectionEpisodeInput,
    ) -> BusinessBrainWriteResult:
        return await self.write_memory_fact(
            MemoryFactWriteInput(
                workspace_id=request.workspace_id,
                fact_id=request.episode_ref,
                fact_type="correction_episode_fact",
                entity_ref="learning_lab",
                value={
                    "situation": dict(request.situation),
                    "candidate_output": request.candidate_output,
                    "human_feedback": request.human_feedback,
                    "final_output": request.final_output,
                    "outcome": request.outcome,
                    "quality_label": request.quality_label,
                },
                source_refs=list(request.source_refs),
                source="correction",
                status="active",
                approval_state="confirmed",
                confidence=0.95,
                risk_tier="low",
                correlation_id=request.correlation_id,
                idempotency_key=f"correction:{request.episode_ref}",
            )
        )

    async def rebuild_contextual_source_units(
        self,
        request: SourceUnitRebuildRequest,
    ) -> SourceUnitRebuildResult:
        pending_units: list[dict[str, Any]] = []
        llm_trace_ids: list[str] = []
        degraded_reasons: list[str] = []
        facts = await self._memory_facts(
            workspace_id=request.workspace_id,
            fact_types=tuple(request.fact_types),
            include_inactive=True,
        )
        candidate_fact_ids = set(request.candidate_fact_ids)
        for fact in facts:
            if candidate_fact_ids and fact.fact_id not in candidate_fact_ids:
                continue
            for source_ref in fact.source_refs:
                reason = request.degraded_units.get(source_ref) or request.degraded_units.get(
                    fact.fact_id
                )
                pending_units.append(
                    {
                        "fact": fact,
                        "source_ref": source_ref,
                        "source_text": _contextualized_fact_source_text(
                            fact,
                            source_ref=source_ref,
                        ),
                        "reason": reason,
                        "embedding": None,
                        "embedding_model": None,
                        "embedding_state": "pending",
                    }
                )
        if request.contextualize_source_units:
            for unit in pending_units:
                if unit["reason"] is not None:
                    continue
                source_text, reason, trace_id = await self._contextualized_source_unit_text(
                    workspace_id=request.workspace_id,
                    fact=unit["fact"],
                    source_ref=unit["source_ref"],
                    source_text=unit["source_text"],
                )
                unit["source_text"] = source_text
                if trace_id:
                    llm_trace_ids.append(trace_id)
                if reason:
                    degraded_reasons.append(reason)
        if request.embed_source_units:
            embeddable_indices = [
                index for index, unit in enumerate(pending_units) if unit["reason"] is None
            ]
            if embeddable_indices:
                from app.modules.retrieval_core.indexing import (
                    RetrievalIndexEmbeddingService,
                )

                texts = [pending_units[index]["source_text"] for index in embeddable_indices]
                embeddings = await RetrievalIndexEmbeddingService().embed_texts(
                    texts,
                    enabled=True,
                    context_prefix="business_brain_index",
                )
                for unit_index, embedding_result in zip(
                    embeddable_indices,
                    embeddings,
                    strict=True,
                ):
                    unit = pending_units[unit_index]
                    unit["embedding_model"] = embedding_result.embedding_model
                    unit["embedding"] = embedding_result.embedding
                    unit["embedding_state"] = embedding_result.embedding_state
                    if embedding_result.degraded_reason:
                        unit["reason"] = embedding_result.degraded_reason

        source_units: list[BusinessBrainIndexRecordContract] = []
        for pending in pending_units:
            fact = pending["fact"]
            source_ref = pending["source_ref"]
            unit = BusinessBrainIndexRecordContract(
                index_id=f"contextual:{fact.fact_id}:{source_ref}",
                workspace_id=request.workspace_id,
                fact_id=fact.fact_id,
                unit_ref=f"source_unit:{fact.fact_id}:{source_ref}",
                state=(
                    "degraded"
                    if pending["reason"]
                    else (
                        "ready"
                        if pending["embedding_state"] == "ready"
                        else "pending"
                    )
                ),
                embedding_ref=None,
                embedding_model=pending["embedding_model"],
                embedding_state=pending["embedding_state"],
                embedding=pending["embedding"],
                source_text=pending["source_text"],
                degraded_reason=pending["reason"],
                source_refs=[source_ref],
                idempotency_key=f"contextual:{fact.fact_id}:{source_ref}",
            )
            await self._repository.persist_index_record(unit)
            source_units.append(unit)
        return SourceUnitRebuildResult(
            source_units=source_units,
            llm_trace_ids=_unique(llm_trace_ids),
            degraded_reasons=sorted(_unique(degraded_reasons)),
        )

    async def index_structured_facts_for_search(
        self,
        *,
        workspace_id: int,
        fact_ids: list[str] | None = None,
    ) -> SourceUnitRebuildResult:
        """Embed the structured business facts (catalog / KB / rules / voice) into
        the searchable index so semantic — including cross-lingual — retrieval can
        surface them. Onboarding embeds raw SOURCE units and the miner embeds
        conversation pairs, but the extracted structured facts the agent grounds on
        were never indexed, so an Uzbek query against an English catalog matched
        nothing. Pass ``fact_ids`` to (re)index only the just-written facts; omit to
        (re)index every searchable structured fact for the workspace."""
        return await self.rebuild_contextual_source_units(
            SourceUnitRebuildRequest(
                workspace_id=workspace_id,
                fact_types=list(SEARCHABLE_STRUCTURED_FACT_TYPES),
                candidate_fact_ids=list(fact_ids or []),
                embed_source_units=True,
            )
        )

    async def _contextualized_source_unit_text(
        self,
        *,
        workspace_id: int,
        fact: BusinessBrainFact,
        source_ref: str,
        source_text: str,
    ) -> tuple[str, str | None, str | None]:
        gateway = self._gateway or LLMGateway(repository=self._repository)
        prompt_id = "business_brain.source_unit_contextualization"
        prompt_version = "1.0.0"
        result = await gateway.generate(
            LLMGatewayRequest(
                route_key="structured_fast",
                workflow_name="business_brain.source_unit_contextualization",
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                input_payload={
                    "fact_type": fact.fact_type,
                    "entity_ref": fact.entity_ref,
                    "fact_value": fact.value,
                    "source_ref": source_ref,
                    "source_text": source_text[:6000],
                },
                output_schema_name="SourceUnitContextualizationOutput",
                workspace_id=workspace_id,
                correlation_id=f"source-unit-context:{workspace_id}:{fact.fact_id}:{source_ref}",
                source_refs=[source_ref, f"fact:{fact.fact_id}"],
                budget={"max_output_chars": 1200},
                timeout_ms=10_000,
                fallback_policy=["use_deterministic_context"],
            ),
            output_model=SourceUnitContextualizationOutput,
        )
        if result.status != "ok" or result.parsed_output is None:
            return source_text, f"contextualization:{result.status}", result.trace_id
        context = " ".join(str(result.parsed_output.get("context") or "").split())
        if not context:
            return source_text, "contextualization:empty", result.trace_id
        return _source_text_with_llm_context(
            context=context,
            source_text=source_text,
        ), None, result.trace_id

    async def retrieve_contextual(
        self,
        request: ContextualRetrievalRequest,
    ) -> ContextualRetrievalResult:
        query_text = (request.query_text or "").strip()
        query_terms = _lexical_terms(query_text)
        facts = await self._memory_facts(
            workspace_id=request.workspace_id,
            fact_types=tuple(request.requested_fact_types),
            include_inactive=True,
        )
        active_facts, rejected = _filter_retrieval_facts(facts, request)
        score_trace, semantic_fact_ids, degraded_reasons = await self._semantic_score_trace(
            request,
            has_query=bool(query_terms),
        )
        if query_terms:
            lexical_rejected, lexical_scores = await self._lexical_scores_for_query(
                active_facts,
                query_terms=query_terms,
                minimum_score=request.minimum_lexical_score,
            )
            rejected.extend(
                fact_id for fact_id in lexical_rejected if fact_id not in semantic_fact_ids
            )
            for fact_id, score in lexical_scores.items():
                score_trace.setdefault(fact_id, {})["lexical"] = round(score, 6)
            selected = _rank_scored_facts(
                active_facts,
                score_trace=score_trace,
                requested_fact_types=request.requested_fact_types,
                limit=request.limit,
            )
        elif score_trace:
            selected = _rank_scored_facts(
                active_facts,
                score_trace=score_trace,
                requested_fact_types=request.requested_fact_types,
                limit=request.limit,
            )
        else:
            selected = _sort_facts(
                active_facts,
                requested_fact_types=request.requested_fact_types,
            )[: request.limit]
        selected_ids = [fact.fact_id for fact in selected]
        candidates, source_unit_degraded, has_source_units = await self._build_candidates(
            request=request,
            selected=selected,
            score_trace=score_trace,
        )
        degraded_reasons.extend(source_unit_degraded)
        channels = _retrieval_channels(
            has_query=bool(query_terms),
            has_semantic=bool(semantic_fact_ids),
            has_source_units=has_source_units,
        )
        missing = [
            slot
            for slot in request.requested_slots
            if slot not in {candidate.fact_type for candidate in candidates}
        ]
        trace = ContextualRetrievalTrace(
            selected_fact_ids=selected_ids,
            rejected_fact_ids=sorted(_unique(rejected)),
            retrieval_channels=channels,
            degraded_reasons=sorted(_unique(degraded_reasons)),
            query_text=query_text or None,
            candidate_scores=score_trace,
            rerank_state="not_requested" if not request.enable_rerank else "degraded",
        )
        return ContextualRetrievalResult(
            workspace_id=request.workspace_id,
            candidates=candidates,
            missing_evidence=missing,
            degraded_reasons=trace.degraded_reasons,
            trace=trace,
        )

    async def _semantic_score_trace(
        self,
        request: ContextualRetrievalRequest,
        *,
        has_query: bool,
    ) -> tuple[dict[str, dict[str, float]], set[str], list[str]]:
        if not request.enable_semantic:
            return {}, set(), []
        if not request.query_embedding:
            return ({}, set(), ["semantic_query_embedding_missing"]) if has_query else ({}, set(), [])
        if len(request.query_embedding) != 3072:
            return {}, set(), ["semantic_query_dimension_mismatch"]
        try:
            semantic_results = await self._repository.search_index_records_vector(
                workspace_id=request.workspace_id,
                query_embedding=list(request.query_embedding),
                fact_types=tuple(request.requested_fact_types),
                statuses=ACTIVE_STATUSES,
                limit=request.limit * 4,
            )
        except Exception:
            return {}, set(), ["semantic_retrieval_unavailable"]

        score_trace: dict[str, dict[str, float]] = {}
        semantic_fact_ids: set[str] = set()
        for record, score in semantic_results:
            if score <= 0:
                continue
            semantic_fact_ids.add(record.fact_id)
            score_trace.setdefault(record.fact_id, {})["semantic"] = round(score, 6)
        return score_trace, semantic_fact_ids, []

    async def _build_candidates(
        self,
        *,
        request: ContextualRetrievalRequest,
        selected: list[BusinessBrainFact],
        score_trace: dict[str, dict[str, float]],
    ) -> tuple[list[ContextualRetrievalCandidate], list[str], bool]:
        candidates: list[ContextualRetrievalCandidate] = []
        degraded_reasons: list[str] = []
        has_source_units = False
        records_by_fact = (
            await self._repository.list_index_records_for_facts(
                workspace_id=request.workspace_id,
                fact_ids=tuple(fact.fact_id for fact in selected),
            )
            if request.include_source_units
            else {}
        )
        for fact in selected:
            records = records_by_fact.get(fact.fact_id, ())
            has_source_units = has_source_units or bool(records)
            degraded_reasons.extend(
                record.degraded_reason for record in records if record.degraded_reason
            )
            candidates.append(
                _contextual_candidate(
                    fact,
                    records=records,
                    scores=score_trace.get(fact.fact_id, {}),
                )
            )
        return candidates, degraded_reasons, has_source_units

    async def _lexical_scores_for_query(
        self,
        facts: list[BusinessBrainFact],
        *,
        query_terms: tuple[str, ...],
        minimum_score: float,
    ) -> tuple[list[str], dict[str, float]]:
        rejected: list[str] = []
        scores: dict[str, float] = {}
        if not facts:
            return rejected, scores
        records_by_fact = await self._repository.list_index_records_for_facts(
            workspace_id=facts[0].workspace_id,
            fact_ids=tuple(fact.fact_id for fact in facts),
        )
        for fact in facts:
            records = records_by_fact.get(fact.fact_id, ())
            score = _lexical_score(query_terms, _candidate_contextual_text(fact, records))
            if score <= 0 or score < minimum_score:
                rejected.append(fact.fact_id)
                continue
            scores[fact.fact_id] = score
        return rejected, scores

    async def build_agent_grounding(
        self,
        request: AgentGroundingRequest,
    ) -> AgentGroundingBundle:
        retrieval = await self.retrieve_contextual(
            ContextualRetrievalRequest(
                workspace_id=request.workspace_id,
                requested_fact_types=list(request.requested_fact_types),
                entity_refs=list(request.entity_refs),
                requested_slots=list(request.requested_slots),
                query_text=request.query_text,
                query_modalities=list(request.query_modalities),
                query_embedding=request.query_embedding,
                minimum_lexical_score=request.minimum_lexical_score,
                enable_semantic=request.enable_semantic,
                enable_rerank=request.enable_rerank,
                include_proposed=request.include_proposed,
                include_source_units=True,
            )
        )
        families: dict[str, list[dict[str, Any]]] = {}
        for candidate in retrieval.candidates:
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
            missing_evidence=list(retrieval.missing_evidence),
            unavailable_families=unavailable,
            degraded_reasons=list(retrieval.degraded_reasons),
            trace=retrieval.trace,
        )

    async def export_learning_lab(self, *, workspace_id: int) -> LearningLabExport:
        facts = await self._memory_facts(
            workspace_id=workspace_id,
            fact_types=TRAINING_FACT_TYPES,
            include_inactive=True,
        )
        training: list[dict[str, Any]] = []
        excluded: list[str] = []
        for fact in _sort_facts(list(facts), requested_fact_types=list(TRAINING_FACT_TYPES)):
            if _is_active_training_fact(fact) and fact.value.get("quality_label") in GOOD_QUALITY:
                training.append(_fact_payload(fact))
            else:
                excluded.append(fact.fact_id)
        return LearningLabExport(
            workspace_id=workspace_id,
            training_candidates=training,
            eval_candidates=list(training),
            excluded_fact_ids=excluded,
        )

    async def _memory_facts(
        self,
        *,
        workspace_id: int,
        fact_types: tuple[str, ...] = (),
        include_inactive: bool = False,
    ) -> tuple[BusinessBrainFact, ...]:
        statuses = VISIBLE_STATUSES if include_inactive else ACTIVE_STATUSES
        if fact_types:
            facts_by_id: dict[str, BusinessBrainFact] = {}
            for fact_type in fact_types:
                for fact in await self._repository.list_facts(
                    workspace_id=workspace_id,
                    fact_type=fact_type,
                    statuses=statuses,
                    limit=250,
                ):
                    facts_by_id.setdefault(fact.fact_id, fact)
            return tuple(
                _sort_facts(
                    list(facts_by_id.values()),
                    requested_fact_types=list(fact_types),
                )
            )
        facts = await self._repository.list_facts(
            workspace_id=workspace_id,
            statuses=statuses,
            limit=250,
        )
        return facts


def _filter_retrieval_facts(
    facts: tuple[BusinessBrainFact, ...],
    request: ContextualRetrievalRequest,
) -> tuple[list[BusinessBrainFact], list[str]]:
    retrievable: list[BusinessBrainFact] = []
    rejected: list[str] = []
    for fact in facts:
        if request.entity_refs and fact.entity_ref not in request.entity_refs:
            continue
        if request.candidate_fact_ids and fact.fact_id not in request.candidate_fact_ids:
            continue
        if _is_retrievable_fact(fact, include_proposed=request.include_proposed):
            retrievable.append(fact)
        else:
            rejected.append(fact.fact_id)
    return retrievable, rejected


def _retrieval_channels(
    *,
    has_query: bool,
    has_semantic: bool,
    has_source_units: bool,
) -> list[str]:
    channels = ["structured"]
    if has_query:
        channels.append("lexical")
    if has_semantic:
        channels.append("semantic")
    if has_source_units:
        channels.append("index")
    return channels


def _contextual_candidate(
    fact: BusinessBrainFact,
    *,
    records: tuple[BusinessBrainIndexRecordContract, ...],
    scores: dict[str, float],
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
        freshness=_freshness(fact.valid_from),
        contextual_text=_candidate_contextual_text(fact, records),
        retrieval_scores=dict(scores),
        source_units=list(records),
    )


def _rank_scored_facts(
    facts: list[BusinessBrainFact],
    *,
    score_trace: dict[str, dict[str, float]],
    requested_fact_types: list[str],
    limit: int,
) -> list[BusinessBrainFact]:
    order = {fact_type: index for index, fact_type in enumerate(requested_fact_types)}
    fallback = len(order)
    ranked = [
        (fact, sum(score_trace.get(fact.fact_id, {}).values()))
        for fact in facts
        if score_trace.get(fact.fact_id)
    ]
    ranked.sort(
        key=lambda item: (
            -item[1],
            order.get(item[0].fact_type, fallback),
            item[0].fact_id,
        )
    )
    return [fact for fact, _score in ranked[:limit]]


def _candidate_contextual_text(
    fact: BusinessBrainFact,
    records: tuple[BusinessBrainIndexRecordContract, ...],
) -> str:
    record_texts = [
        str(record.source_text).strip()
        for record in records
        if str(record.source_text or "").strip()
    ]
    if record_texts:
        return "\n\n".join(record_texts)
    return _fact_contextual_text(fact)


def _fact_contextual_text(
    fact: BusinessBrainFact,
    *,
    source_ref: str | None = None,
) -> str:
    value = dict(fact.value)
    if fact.fact_type == "conversation_pair_fact":
        parts = [
            f"Fact type: {fact.fact_type}",
            f"Entity: {fact.entity_ref}",
            f"Customer: {value.get('customer_turn') or ''}",
            f"Seller: {value.get('seller_turn') or ''}",
        ]
        context_before = value.get("context_before")
        if isinstance(context_before, list) and context_before:
            parts.append("Context before: " + " | ".join(str(item) for item in context_before[:4]))
        media_semantics = value.get("media_semantics")
        if isinstance(media_semantics, dict) and media_semantics:
            parts.append(
                "Media semantics: "
                + json.dumps(media_semantics, ensure_ascii=False, sort_keys=True)
            )
        for key in (
            "intent",
            "contact_type",
            "context_prefix",
            "previous_turns",
            "outcome",
            "quality_label",
        ):
            if value.get(key):
                parts.append(f"{key}: {value[key]}")
    else:
        parts = [
            f"Fact type: {fact.fact_type}",
            f"Entity: {fact.entity_ref}",
            json.dumps(value, ensure_ascii=False, sort_keys=True),
        ]
    if source_ref:
        parts.append(f"Source: {source_ref}")
    if fact.source_refs:
        parts.append("Source refs: " + " | ".join(fact.source_refs[:8]))
    return "\n".join(part for part in parts if part.strip())


def _contextualized_fact_source_text(
    fact: BusinessBrainFact,
    *,
    source_ref: str,
) -> str:
    return "\n".join(
        [
            "Contextual source unit",
            f"Fact type: {fact.fact_type}",
            f"Entity ref: {fact.entity_ref}",
            f"Fact ref: {fact.fact_id}",
            f"Source ref: {source_ref}",
            "Evidence text:",
            _fact_contextual_text(fact, source_ref=source_ref),
        ]
    )


def _source_text_with_llm_context(
    *,
    context: str,
    source_text: str,
) -> str:
    return "\n".join(
        [
            "LLM contextualized source unit",
            "LLM retrieval context:",
            context,
            "Original contextual source unit:",
            source_text,
        ]
    )


def _lexical_terms(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    terms: list[str] = []
    seen: set[str] = set()
    for part in normalized.split():
        if len(part) < 2 or part in seen:
            continue
        seen.add(part)
        terms.append(part)
    return tuple(terms)


def _lexical_score(query_terms: tuple[str, ...], text: str) -> float:
    if not query_terms or not text:
        return 0.0
    text_terms = set(_lexical_terms(text))
    if not text_terms:
        return 0.0
    matched = sum(1 for term in query_terms if term in text_terms)
    if matched == 0:
        return 0.0
    return matched / len(query_terms)


def _next_seller_turn(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    for turn in turns:
        if turn.get("sender_type") == "seller":
            return turn
    return None


def _is_active_training_fact(fact: BusinessBrainFact) -> bool:
    return _is_retrievable_fact(fact, include_proposed=False)


def _is_retrievable_fact(
    fact: BusinessBrainFact,
    *,
    include_proposed: bool,
) -> bool:
    allowed_statuses = set(ACTIVE_STATUSES)
    if include_proposed:
        allowed_statuses.add("proposed")
    if fact.status not in allowed_statuses:
        return False
    if fact.value.get("quality_label") in EXCLUDED_QUALITY:
        return False
    return fact.confidence >= 0.4


def _sort_facts(
    facts: list[BusinessBrainFact],
    *,
    requested_fact_types: list[str],
) -> list[BusinessBrainFact]:
    order = {fact_type: index for index, fact_type in enumerate(requested_fact_types)}
    fallback = len(order)
    return sorted(
        facts,
        key=lambda fact: (order.get(fact.fact_type, fallback), fact.fact_id),
    )


def _fact_payload(fact: BusinessBrainFact) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "fact_type": fact.fact_type,
        "entity_ref": fact.entity_ref,
        "value": dict(fact.value),
        "source_refs": list(fact.source_refs),
        "confidence": fact.confidence,
        "risk_tier": fact.risk_tier,
        "freshness": _freshness(fact.valid_from),
    }


def _freshness(valid_from: datetime) -> dict[str, object]:
    age_seconds = max(0, int((datetime.now(UTC) - valid_from).total_seconds()))
    return {
        "state": "fresh" if age_seconds < 30 * 24 * 60 * 60 else "stale",
        "age_seconds": age_seconds,
    }


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
