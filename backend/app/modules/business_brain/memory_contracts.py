from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.business_brain.contracts import (
    BusinessBrainIndexRecordContract,
    BusinessBrainWriteResult,
)
from app.modules.commercial_spine.contracts import (
    RiskTier,
)


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryFactWriteInput(MemoryModel):
    schema_version: Literal["memory_fact_write_input.v1"] = "memory_fact_write_input.v1"
    workspace_id: int = Field(gt=0)
    fact_id: str = Field(min_length=1)
    fact_type: str = Field(min_length=1)
    entity_ref: str = Field(min_length=1)
    value: dict[str, Any]
    source_refs: list[str] = Field(min_length=1)
    source: str = "manual"
    status: str = "active"
    approval_state: str = "confirmed"
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    risk_tier: RiskTier = "low"
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    supersedes_fact_id: str | None = Field(default=None, min_length=1)
    actor_ref: str | None = Field(default=None, min_length=1)


class RuleCompilationRequest(MemoryModel):
    schema_version: Literal["rule_compilation_request.v1"] = (
        "rule_compilation_request.v1"
    )
    workspace_id: int = Field(gt=0)
    rule_fact_id: str = Field(min_length=1)
    conversation_id: int = Field(gt=0)
    customer_id: int = Field(gt=0)
    correlation_id: str = Field(min_length=1)


class VoiceProjectionRequest(MemoryModel):
    schema_version: Literal["voice_projection_request.v1"] = (
        "voice_projection_request.v1"
    )
    workspace_id: int = Field(gt=0)
    entity_ref: str = "seller_voice"


class ConversationPairMiningInput(MemoryModel):
    schema_version: Literal["conversation_pair_mining_input.v1"] = (
        "conversation_pair_mining_input.v1"
    )
    workspace_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    source_refs: list[str] = Field(min_length=1)
    turns: list[dict[str, Any]] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    # When set, mine ONLY the pair completed by this seller turn's message_ref,
    # instead of re-mining every historical pair in the conversation. Scopes the
    # per-message projection to the new turn so a seller reply does not re-write +
    # re-embed the whole conversation (O(n) work + connection pinning) every time.
    # None preserves whole-conversation mining (bulk replay / backfill callers).
    trigger_message_ref: str | None = Field(default=None, min_length=1)


class ConversationPairMiningResult(MemoryModel):
    schema_version: Literal["conversation_pair_mining_result.v1"] = (
        "conversation_pair_mining_result.v1"
    )
    pairs: list[BusinessBrainWriteResult] = Field(default_factory=list)


class CorrectionEpisodeInput(MemoryModel):
    schema_version: Literal["correction_episode_input.v1"] = (
        "correction_episode_input.v1"
    )
    workspace_id: int = Field(gt=0)
    episode_ref: str = Field(min_length=1)
    situation: dict[str, Any]
    candidate_output: str = Field(min_length=1)
    human_feedback: str = Field(min_length=1)
    final_output: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    quality_label: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class SourceUnitRebuildRequest(MemoryModel):
    schema_version: Literal["source_unit_rebuild_request.v1"] = (
        "source_unit_rebuild_request.v1"
    )
    workspace_id: int = Field(gt=0)
    fact_types: list[str] = Field(default_factory=list)
    candidate_fact_ids: list[str] = Field(default_factory=list)
    degraded_units: dict[str, str] = Field(default_factory=dict)
    embed_source_units: bool = False
    contextualize_source_units: bool = False


class SourceUnitRebuildResult(MemoryModel):
    schema_version: Literal["source_unit_rebuild_result.v1"] = (
        "source_unit_rebuild_result.v1"
    )
    source_units: list[BusinessBrainIndexRecordContract] = Field(default_factory=list)
    llm_trace_ids: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)


class SourceUnitContextualizationOutput(MemoryModel):
    schema_version: Literal["source_unit_contextualization_output.v1"] = (
        "source_unit_contextualization_output.v1"
    )
    context: str = Field(default="", max_length=1200)


class ContextualRetrievalRequest(MemoryModel):
    schema_version: Literal["contextual_retrieval_request.v1"] = (
        "contextual_retrieval_request.v1"
    )
    workspace_id: int = Field(gt=0)
    requested_fact_types: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    candidate_fact_ids: list[str] = Field(default_factory=list)
    requested_slots: list[str] = Field(default_factory=list)
    query_text: str | None = None
    query_modalities: list[Literal["text", "image", "audio", "video", "pdf", "file"]] = (
        Field(default_factory=list)
    )
    query_embedding: list[float] | None = Field(default=None, exclude=True)
    minimum_lexical_score: float = Field(default=0.0, ge=0.0, le=1.0)
    enable_semantic: bool = False
    enable_rerank: bool = False
    include_proposed: bool = False
    include_source_units: bool = False
    limit: int = Field(default=50, ge=1, le=250)


class ContextualRetrievalCandidate(MemoryModel):
    schema_version: Literal["contextual_retrieval_candidate.v1"] = (
        "contextual_retrieval_candidate.v1"
    )
    fact_id: str
    fact_type: str
    entity_ref: str
    value: dict[str, Any]
    source_refs: list[str]
    confidence: float
    risk_tier: RiskTier
    status: str
    freshness: dict[str, Any]
    contextual_text: str | None = None
    retrieval_scores: dict[str, float] = Field(default_factory=dict)
    source_units: list[BusinessBrainIndexRecordContract] = Field(default_factory=list)


class ContextualRetrievalTrace(MemoryModel):
    schema_version: Literal["contextual_retrieval_trace.v1"] = (
        "contextual_retrieval_trace.v1"
    )
    selected_fact_ids: list[str] = Field(default_factory=list)
    rejected_fact_ids: list[str] = Field(default_factory=list)
    retrieval_channels: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    query_text: str | None = None
    query_rewrites: list[str] = Field(default_factory=list)
    agentic_queries: list[str] = Field(default_factory=list)
    agentic_fact_types: list[str] = Field(default_factory=list)
    agentic_modalities: list[str] = Field(default_factory=list)
    expanded_fact_types: list[str] = Field(default_factory=list)
    llm_trace_ids: list[str] = Field(default_factory=list)
    candidate_scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    rerank_state: Literal["not_requested", "requested", "degraded"] = "not_requested"


class ContextualRetrievalResult(MemoryModel):
    schema_version: Literal["contextual_retrieval_result.v1"] = (
        "contextual_retrieval_result.v1"
    )
    workspace_id: int = Field(gt=0)
    candidates: list[ContextualRetrievalCandidate] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    trace: ContextualRetrievalTrace


class AgentGroundingRequest(MemoryModel):
    schema_version: Literal["agent_grounding_request.v1"] = "agent_grounding_request.v1"
    workspace_id: int = Field(gt=0)
    agent_kind: Literal[
        "seller_agent",
        "bi_agent",
        "promoter_agent",
        "support_agent",
    ]
    requested_fact_types: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    requested_slots: list[str] = Field(default_factory=list)
    query_text: str | None = None
    query_modalities: list[Literal["text", "image", "audio", "video", "pdf", "file"]] = (
        Field(default_factory=list)
    )
    query_embedding: list[float] | None = Field(default=None, exclude=True)
    minimum_lexical_score: float = Field(default=0.0, ge=0.0, le=1.0)
    enable_semantic: bool = False
    enable_rerank: bool = False
    include_proposed: bool = False


class AgentGroundingBundle(MemoryModel):
    schema_version: Literal["agent_grounding_bundle.v1"] = "agent_grounding_bundle.v1"
    workspace_id: int = Field(gt=0)
    agent_kind: str
    families: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    missing_evidence: list[str] = Field(default_factory=list)
    unavailable_families: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    trace: ContextualRetrievalTrace


class LearningLabExport(MemoryModel):
    schema_version: Literal["learning_lab_export.v1"] = "learning_lab_export.v1"
    workspace_id: int = Field(gt=0)
    training_candidates: list[dict[str, Any]] = Field(default_factory=list)
    eval_candidates: list[dict[str, Any]] = Field(default_factory=list)
    excluded_fact_ids: list[str] = Field(default_factory=list)
