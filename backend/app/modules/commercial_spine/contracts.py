from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmptyString = Annotated[str, Field(min_length=1)]

ActorType = Literal["customer", "seller", "owner", "system", "agent", "admin", "integration"]
FactStatus = Literal[
    "active",
    "confirmed",
    "proposed",
    "rejected",
    "superseded",
    "historical",
    "expired",
    "conflicted",
    "degraded",
]
UpdateSource = Literal["manual", "ai_proposal", "onboarding", "correction", "integration", "import", "replay"]
ApprovalState = Literal["proposed", "confirmed", "rejected", "blocked", "expired", "cancelled"]
RiskTier = Literal["low", "medium", "high", "critical"]
ProposalLifecycleState = Literal[
    "proposed",
    "waiting_approval",
    "approved",
    "executing",
    "executed",
    "rejected",
    "blocked",
    "failed",
    "expired",
    "cancelled",
]
ExecutionMode = Literal[
    "suggest_only",
    "draft_for_review",
    "ask_seller_confirmation",
    "auto_execute_if_policy_allows",
    "blocked_until_evidence",
]
Priority = Literal["low", "medium", "high", "urgent"]
GatewayStatus = Literal["ok", "schema_error", "provider_error", "timeout", "blocked", "degraded"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class SpineModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommercialEvent(SpineModel):
    schema_version: Literal["commercial_event.v1"] = "commercial_event.v1"
    event_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    source_type: NonEmptyString
    source_ref: NonEmptyString
    actor_type: ActorType
    correlation_id: NonEmptyString
    idempotency_key: NonEmptyString
    occurred_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class BusinessBrainFact(SpineModel):
    schema_version: Literal["business_brain_fact.v1"] = "business_brain_fact.v1"
    fact_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    fact_type: NonEmptyString
    entity_ref: NonEmptyString
    value: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    status: FactStatus
    risk_tier: RiskTier = "low"
    valid_from: datetime = Field(default_factory=utc_now)
    valid_until: datetime | None = None
    source_refs: list[NonEmptyString] = Field(min_length=1)
    supersedes_fact_id: NonEmptyString | None = None
    idempotency_key: NonEmptyString

    @model_validator(mode="after")
    def validate_temporal_range(self) -> BusinessBrainFact:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        if self.status == "superseded" and self.valid_until is None:
            raise ValueError("superseded facts require valid_until")
        return self


class BusinessBrainUpdate(SpineModel):
    schema_version: Literal["business_brain_update.v1"] = "business_brain_update.v1"
    update_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    target_ref: NonEmptyString
    proposed_value: dict[str, Any]
    source: UpdateSource
    approval_state: ApprovalState
    risk_tier: RiskTier
    evidence_refs: list[NonEmptyString] = Field(min_length=1)
    idempotency_key: NonEmptyString
    applied_at: datetime | None = None
    actor_type: ActorType | None = None
    actor_ref: NonEmptyString | None = None
    correlation_id: NonEmptyString | None = None

    @model_validator(mode="after")
    def confirmed_updates_need_applied_at_for_ai(self) -> BusinessBrainUpdate:
        if self.source == "ai_proposal" and self.approval_state == "confirmed" and self.applied_at is None:
            raise ValueError("confirmed ai_proposal updates require applied_at")
        return self


class BusinessBrainProjection(SpineModel):
    schema_version: Literal["business_brain_projection.v1"] = "business_brain_projection.v1"
    projection_ref: NonEmptyString
    workspace_id: int = Field(gt=0)
    projection_type: NonEmptyString
    entity_ref: NonEmptyString
    state: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[NonEmptyString] = Field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def degraded_projection_needs_reason(self) -> BusinessBrainProjection:
        if self.degraded and not self.degraded_reasons:
            raise ValueError("degraded projections require degraded_reasons")
        if not self.degraded and not self.source_refs:
            raise ValueError("non-degraded projections require source_refs")
        return self


class CommercialActionProposal(SpineModel):
    schema_version: Literal["commercial_action_proposal.v2"] = "commercial_action_proposal.v2"
    proposal_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    # 0 means workspace-scoped/system action. Customer-facing actions keep real
    # positive conversation/customer ids.
    conversation_id: int = Field(ge=0)
    customer_id: int = Field(ge=0)
    action_type: NonEmptyString
    lifecycle_state: ProposalLifecycleState
    execution_mode: ExecutionMode
    risk_level: RiskTier
    requires_approval: bool
    executor_runtime: NonEmptyString | None = None
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: NonEmptyString
    source_refs: list[NonEmptyString] = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: NonEmptyString
    correlation_id: NonEmptyString | None = None
    trace_id: NonEmptyString | None = None

    @model_validator(mode="after")
    def approval_matches_risk(self) -> CommercialActionProposal:
        if self.lifecycle_state == "waiting_approval" and not self.requires_approval:
            raise ValueError("waiting_approval proposals require approval")
        if self.risk_level in {"high", "critical"} and self.execution_mode == "auto_execute_if_policy_allows":
            raise ValueError("risky proposals cannot auto execute in Phase 1")
        return self


class CommercialDecisionTrace(SpineModel):
    schema_version: Literal["commercial_decision_trace.v2"] = "commercial_decision_trace.v2"
    trace_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    correlation_id: NonEmptyString
    conversation_id: int | None = Field(default=None, ge=0)
    customer_id: int | None = Field(default=None, ge=0)
    accepted_event_ids: list[NonEmptyString] = Field(default_factory=list)
    changed_fact_refs: list[NonEmptyString] = Field(default_factory=list)
    changed_projection_refs: list[NonEmptyString] = Field(default_factory=list)
    emitted_proposal_refs: list[NonEmptyString] = Field(default_factory=list)
    llm_trace_ids: list[NonEmptyString] = Field(default_factory=list)
    degraded_reasons: list[NonEmptyString] = Field(default_factory=list)


class LLMGatewayRequest(SpineModel):
    schema_version: Literal["llm_gateway_request.v1"] = "llm_gateway_request.v1"
    route_key: NonEmptyString
    workflow_name: NonEmptyString
    prompt_id: NonEmptyString
    prompt_version: NonEmptyString
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_schema_name: NonEmptyString
    workspace_id: int = Field(gt=0)
    correlation_id: NonEmptyString
    source_refs: list[NonEmptyString] = Field(default_factory=list)
    content_parts: list[dict[str, Any]] = Field(default_factory=list, exclude=True)
    budget: dict[str, Any] = Field(default_factory=dict)
    prompt_cache: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=30_000, gt=0)
    fallback_policy: list[NonEmptyString] = Field(default_factory=list)
    eval_sample_policy: dict[str, Any] = Field(default_factory=dict)


class LLMGatewayResult(SpineModel):
    schema_version: Literal["llm_gateway_result.v1"] = "llm_gateway_result.v1"
    status: GatewayStatus
    parsed_output: dict[str, Any] | None = None
    raw_output_ref: str | None = None
    model_used: str | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    cost_estimate: float | None = None
    trace_id: NonEmptyString
    validation_errors: list[str] = Field(default_factory=list)
    fallback_used: bool = False


class LLMGatewayTrace(SpineModel):
    schema_version: Literal["llm_gateway_trace.v1"] = "llm_gateway_trace.v1"
    trace_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    correlation_id: NonEmptyString
    route_key: NonEmptyString
    workflow_name: NonEmptyString
    prompt_id: NonEmptyString
    prompt_version: NonEmptyString
    source_refs: list[NonEmptyString] = Field(default_factory=list)
    status: GatewayStatus
    model_used: str | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    cost_estimate: float | None = None
    fallback_used: bool = False
    validation_errors: list[str] = Field(default_factory=list)
    raw_output_ref: str | None = None
    raw_request: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)
