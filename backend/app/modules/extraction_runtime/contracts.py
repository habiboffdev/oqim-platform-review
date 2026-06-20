from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.commercial_spine.contracts import GatewayStatus, RiskTier

NonEmptyString = Annotated[str, Field(min_length=1)]

ExtractionSourceKind = Literal[
    "source_bundle",
    "chat_tail",
    "media",
    "file",
    "url",
    "telegram_channel",
    "seller_correction",
    "admin_replay",
]
ExtractionCandidateKind = Literal[
    "catalog_family",
    "kb_entry",
    "seller_rule",
    "voice_observation",
    "conversation_pair",
    "customer_state",
    "opportunity",
    "business_task",
    "sales_follow_up",
    "payment_state",
    "delivery_state",
    "refund_state",
    "identity_merge",
    "buyer_intent",
    "catalog_media_send",
    "marketplace_listing",
]
ExtractionOwner = Literal[
    "business_brain",
    "commerce_core",
    "action_runtime",
    "marketplace",
    "review_only",
]
ExtractionPartKind = Literal[
    "text",
    "chat_turn",
    "media_ref",
    "media_bytes",
    "file_ref",
    "file_bytes",
    "url",
    "source_fact_ref",
]
ExtractionPersistMode = Literal["none", "review_candidates", "proposed_writes"]
ExtractionOperation = Literal["create", "update", "merge", "link", "signal", "noop"]
EvidenceState = Literal["valid", "missing", "unsupported", "conflicted"]


class ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractionScope(ExtractionModel):
    workspace_id: int = Field(gt=0)
    conversation_id: int | None = None
    customer_id: int | None = None
    channel_ref: NonEmptyString | None = None
    marketplace_ref: NonEmptyString | None = None


class ExtractionPart(ExtractionModel):
    kind: ExtractionPartKind
    ref: NonEmptyString
    payload: dict[str, Any] = Field(default_factory=dict)
    bytes_base64: str | None = Field(default=None, exclude=True)


class ExtractionRequest(ExtractionModel):
    schema_version: Literal["universal_extraction_request.v1"] = (
        "universal_extraction_request.v1"
    )
    scope: ExtractionScope
    source_kind: ExtractionSourceKind
    source_ref: NonEmptyString
    parts: list[ExtractionPart] = Field(min_length=1)
    profile_refs: list[NonEmptyString] = Field(min_length=1)
    target_kinds: list[ExtractionCandidateKind] = Field(default_factory=list)
    correlation_id: NonEmptyString
    idempotency_key: NonEmptyString
    max_parallelism: int = Field(default=4, ge=1, le=16)
    max_evidence_units: int = Field(default=50, ge=1, le=500)
    persist_mode: ExtractionPersistMode = "review_candidates"
    resume_token: NonEmptyString | None = None

    @model_validator(mode="after")
    def enforce_evidence_unit_limit(self) -> ExtractionRequest:
        if len(self.parts) > self.max_evidence_units:
            raise ValueError("parts exceed max_evidence_units")
        return self

    def allowed_evidence_refs(self) -> tuple[str, ...]:
        return tuple(_unique([part.ref for part in self.parts]))


class ExtractionEvidenceSet(ExtractionModel):
    schema_version: Literal["extraction_evidence_set.v1"] = (
        "extraction_evidence_set.v1"
    )
    allowed_refs: tuple[NonEmptyString, ...] = Field(default_factory=tuple)

    @classmethod
    def from_refs(
        cls,
        refs: list[str] | tuple[str, ...],
    ) -> ExtractionEvidenceSet:
        return cls(
            allowed_refs=tuple(
                _unique([str(ref).strip() for ref in refs if str(ref).strip()])
            )
        )

    @classmethod
    def from_request(cls, request: ExtractionRequest) -> ExtractionEvidenceSet:
        return cls.from_refs(request.allowed_evidence_refs())

    def unsupported_refs(
        self,
        candidate_refs: list[str] | tuple[str, ...],
    ) -> list[str]:
        allowed = set(self.allowed_refs)
        return [str(ref) for ref in candidate_refs if str(ref) not in allowed]


class ExtractionCandidate(ExtractionModel):
    schema_version: Literal["extraction_candidate.v1"] = "extraction_candidate.v1"
    candidate_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    owner: ExtractionOwner
    profile_ref: NonEmptyString
    kind: ExtractionCandidateKind
    entity_ref: NonEmptyString
    operation: ExtractionOperation
    value: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    risk_tier: RiskTier
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)
    evidence_state: EvidenceState
    requires_review: bool
    reason_code: NonEmptyString
    degraded_reasons: list[NonEmptyString] = Field(default_factory=list)


class RejectedExtractionCandidate(ExtractionModel):
    schema_version: Literal["rejected_extraction_candidate.v1"] = (
        "rejected_extraction_candidate.v1"
    )
    candidate_id: NonEmptyString
    profile_ref: NonEmptyString
    kind: ExtractionCandidateKind
    owner: ExtractionOwner
    reason: NonEmptyString
    unsupported_refs: list[NonEmptyString] = Field(default_factory=list)
    validation_errors: list[NonEmptyString] = Field(default_factory=list)


class ExtractionResult(ExtractionModel):
    schema_version: Literal["universal_extraction_result.v1"] = (
        "universal_extraction_result.v1"
    )
    run_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    status: GatewayStatus
    source_ref: NonEmptyString
    profile_refs: list[NonEmptyString]
    accepted_candidates: list[ExtractionCandidate] = Field(default_factory=list)
    rejected_candidates: list[RejectedExtractionCandidate] = Field(default_factory=list)
    degraded_reasons: list[NonEmptyString] = Field(default_factory=list)
    evidence_summary: dict[str, int]
    source_refs: list[NonEmptyString]
    correlation_id: NonEmptyString
    idempotency_key: NonEmptyString
    resume_token: NonEmptyString | None = None


class ProposeCandidatesRequest(ExtractionModel):
    schema_version: Literal["propose_extraction_candidates_request.v1"] = (
        "propose_extraction_candidates_request.v1"
    )
    workspace_id: int = Field(gt=0)
    run_id: NonEmptyString
    candidate_ids: list[NonEmptyString] = Field(min_length=1)
    correlation_id: NonEmptyString
    idempotency_key: NonEmptyString


class ProposeCandidatesResult(ExtractionModel):
    schema_version: Literal["propose_extraction_candidates_result.v1"] = (
        "propose_extraction_candidates_result.v1"
    )
    workspace_id: int = Field(gt=0)
    run_id: NonEmptyString
    proposed_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    proposal_refs: list[NonEmptyString] = Field(default_factory=list)
    degraded_reasons: list[NonEmptyString] = Field(default_factory=list)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
