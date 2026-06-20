from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.commercial_spine.contracts import (
    ActorType,
    ApprovalState,
    BusinessBrainFact,
    BusinessBrainUpdate,
    FactStatus,
    RiskTier,
    UpdateSource,
)

NonEmptyString = Annotated[str, Field(min_length=1)]


class BusinessBrainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusinessBrainFactUpdateInput(BusinessBrainModel):
    schema_version: Literal["business_brain_fact_update_input.v1"] = (
        "business_brain_fact_update_input.v1"
    )
    update_id: NonEmptyString
    fact_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    fact_type: NonEmptyString
    entity_ref: NonEmptyString
    value: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    status: FactStatus
    risk_tier: RiskTier
    source: UpdateSource
    approval_state: ApprovalState
    source_refs: list[NonEmptyString] = Field(min_length=1)
    idempotency_key: NonEmptyString
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    supersedes_fact_id: NonEmptyString | None = None
    applied_at: datetime | None = None
    actor_type: ActorType | None = None
    actor_ref: NonEmptyString | None = None
    correlation_id: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_shared_write_contract(self) -> BusinessBrainFactUpdateInput:
        if self.approval_state == "confirmed" and self.status not in {
            "active",
            "confirmed",
            "historical",
            "superseded",
        }:
            raise ValueError("confirmed updates require an active or historical fact status")
        if (
            self.source == "ai_proposal"
            and self.approval_state == "confirmed"
            and self.applied_at is None
        ):
            raise ValueError("confirmed ai_proposal updates require applied_at")
        return self

    def to_fact(self) -> BusinessBrainFact:
        payload: dict[str, Any] = {
            "fact_id": self.fact_id,
            "workspace_id": self.workspace_id,
            "fact_type": self.fact_type,
            "entity_ref": self.entity_ref,
            "value": dict(self.value),
            "confidence": self.confidence,
            "status": self.status,
            "risk_tier": self.risk_tier,
            "source_refs": list(self.source_refs),
            "supersedes_fact_id": self.supersedes_fact_id,
            "idempotency_key": f"fact:{self.idempotency_key}",
        }
        if self.valid_from is not None:
            payload["valid_from"] = self.valid_from
        if self.valid_until is not None:
            payload["valid_until"] = self.valid_until
        return BusinessBrainFact.model_validate(payload)

    def to_update(self) -> BusinessBrainUpdate:
        return BusinessBrainUpdate(
            update_id=self.update_id,
            workspace_id=self.workspace_id,
            target_ref=f"fact:{self.fact_id}",
            proposed_value=dict(self.value),
            source=self.source,
            approval_state=self.approval_state,
            risk_tier=self.risk_tier,
            evidence_refs=list(self.source_refs),
            idempotency_key=f"update:{self.idempotency_key}",
            applied_at=self.applied_at,
            actor_type=self.actor_type,
            actor_ref=self.actor_ref,
            correlation_id=self.correlation_id,
        )


class BusinessBrainWriteResult(BusinessBrainModel):
    schema_version: Literal["business_brain_write_result.v1"] = (
        "business_brain_write_result.v1"
    )
    fact: BusinessBrainFact
    update: BusinessBrainUpdate
    fact_created: bool
    update_created: bool


class BusinessBrainIndexUnit(BusinessBrainModel):
    schema_version: Literal["business_brain_index_unit.v1"] = (
        "business_brain_index_unit.v1"
    )
    unit_ref: NonEmptyString
    source_refs: list[NonEmptyString] = Field(min_length=1)
    embedding_ref: NonEmptyString | None = None
    degraded_reason: NonEmptyString | None = None


class BusinessBrainIndexRecordContract(BusinessBrainModel):
    schema_version: Literal["business_brain_index_record.v1"] = (
        "business_brain_index_record.v1"
    )
    index_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    fact_id: NonEmptyString
    unit_ref: NonEmptyString
    state: Literal["pending", "ready", "degraded"]
    embedding_ref: NonEmptyString | None = None
    embedding_model: NonEmptyString | None = None
    embedding_state: Literal["pending", "ready", "degraded"] = "pending"
    embedding: list[float] | None = Field(default=None, exclude=True)
    source_text: str | None = None
    degraded_reason: NonEmptyString | None = None
    source_refs: list[NonEmptyString] = Field(min_length=1)
    idempotency_key: NonEmptyString


class BusinessBrainFactReadModel(BusinessBrainModel):
    schema_version: Literal["business_brain_fact_read_model.v1"] = (
        "business_brain_fact_read_model.v1"
    )
    fact_id: NonEmptyString
    workspace_id: int = Field(gt=0)
    fact_type: NonEmptyString
    entity_ref: NonEmptyString
    value: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    status: FactStatus
    risk_tier: RiskTier
    source_refs: list[NonEmptyString] = Field(default_factory=list)
    freshness: dict[str, Any]
    supersedes_fact_id: NonEmptyString | None = None
    valid_from: datetime
    valid_until: datetime | None = None


class BusinessBrainFactDetail(BusinessBrainModel):
    schema_version: Literal["business_brain_fact_detail.v1"] = (
        "business_brain_fact_detail.v1"
    )
    fact: BusinessBrainFactReadModel
    updates: list[BusinessBrainUpdate] = Field(default_factory=list)
    index_state: Literal["ready", "degraded", "pending", "unavailable"]
    extraction_state: Literal["available", "degraded", "unavailable"]
    index_records: list[BusinessBrainIndexRecordContract] = Field(default_factory=list)


BrainObjectDomain = Literal[
    "catalog",
    "knowledge",
    "rules",
    "voice",
    "examples",
    "issues",
    "sources",
]
BrainObjectState = Literal["ready", "needs_review", "conflict", "degraded", "archived"]
BrainObjectEvidenceKind = Literal[
    "telegram",
    "file",
    "website",
    "manual",
    "conversation",
    "integration",
    "source",
]
BrainObjectSourceLifecycle = Literal[
    "live",
    "snapshot",
    "expired",
    "archived",
    "conflicting",
    "failed",
    "retrying",
]


class BrainObjectEvidence(BusinessBrainModel):
    schema_version: Literal["brain_object_evidence.v1"] = "brain_object_evidence.v1"
    label: NonEmptyString
    kind: BrainObjectEvidenceKind
    freshness_label: NonEmptyString
    detail: NonEmptyString | None = None
    unit_label: NonEmptyString | None = None
    source_ref: NonEmptyString | None = None


class BrainObjectItem(BusinessBrainModel):
    schema_version: Literal["brain_object_item.v1"] = "brain_object_item.v1"
    object_id: NonEmptyString
    domain: BrainObjectDomain
    title: NonEmptyString
    summary: NonEmptyString
    status: BrainObjectState
    status_label: NonEmptyString
    confidence: float = Field(ge=0.0, le=1.0)
    risk_tier: RiskTier
    source_lifecycle: BrainObjectSourceLifecycle
    evidence: list[BrainObjectEvidence] = Field(default_factory=list)
    evidence_count: int = Field(ge=0)
    updated_at: datetime
    can_edit: bool = True
    can_archive: bool = True
    needs_review: bool = False
    fact_ids: list[NonEmptyString] = Field(default_factory=list)
    proposal_refs: list[NonEmptyString] = Field(default_factory=list)


class BrainObjectProjection(BusinessBrainModel):
    schema_version: Literal["brain_object_projection.v1"] = "brain_object_projection.v1"
    workspace_id: int = Field(gt=0)
    objects: list[BrainObjectItem] = Field(default_factory=list)
    counts: dict[BrainObjectDomain, int] = Field(default_factory=dict)
    issues_count: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    review_count: int = Field(ge=0)


SourceIntakeLifecycle = Literal[
    "live",
    "snapshot",
    "learning",
    "needs_review",
    "retrying",
    "failed",
    "conflicting",
    "archived",
]
SourceIntakePurpose = Literal["brain_data", "agent_data"]


class SourceIntakeItem(BusinessBrainModel):
    schema_version: Literal["source_intake_item.v1"] = "source_intake_item.v1"
    source_ref: NonEmptyString
    title: NonEmptyString
    kind: NonEmptyString
    kind_label: NonEmptyString
    purpose: SourceIntakePurpose
    purpose_label: NonEmptyString
    lifecycle: SourceIntakeLifecycle
    status_label: NonEmptyString
    summary: NonEmptyString
    preview: NonEmptyString
    learned_object_count: int = Field(ge=0)
    learned_object_labels: list[NonEmptyString] = Field(default_factory=list)
    source_unit_count: int = Field(ge=0)
    media_count: int = Field(ge=0)
    issue_label: NonEmptyString | None = None
    retryable: bool = False
    can_retry: bool = False
    can_archive: bool = True
    can_pause: bool = False
    can_resume: bool = False
    fact_id: NonEmptyString | None = None
    updated_at: datetime


class SourceIntakeProjection(BusinessBrainModel):
    schema_version: Literal["source_intake_projection.v1"] = "source_intake_projection.v1"
    workspace_id: int = Field(gt=0)
    sources: list[SourceIntakeItem] = Field(default_factory=list)
    counts: dict[SourceIntakeLifecycle, int] = Field(default_factory=dict)
    kind_counts: dict[str, int] = Field(default_factory=dict)
    ready_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    live_count: int = Field(ge=0)
