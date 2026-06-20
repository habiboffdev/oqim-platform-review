from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.commercial_spine.contracts import CommercialActionProposal


class ActionRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ActionRuntimeState = Literal[
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

ExecutionStatus = Literal["executed", "blocked", "failed", "unsupported"]
EscalationDestination = Literal["in_app", "telegram_seller_bot"]


class ActionRuntimePolicyInput(ActionRuntimeModel):
    schema_version: Literal["action_runtime_policy_input.v1"] = (
        "action_runtime_policy_input.v1"
    )
    workspace_id: int = Field(gt=0)
    enabled: bool = False
    confidence_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    low_risk_allowlist: list[str] = Field(default_factory=list)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    escalation_destination: EscalationDestination = "in_app"
    source_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(default="action_runtime_policy", min_length=1)


class ActionRuntimePolicy(ActionRuntimeModel):
    schema_version: Literal["action_runtime_policy.v1"] = "action_runtime_policy.v1"
    workspace_id: int = Field(gt=0)
    enabled: bool
    confidence_threshold: float = Field(ge=0.0, le=1.0)
    low_risk_allowlist: list[str] = Field(default_factory=list)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    escalation_destination: EscalationDestination = "in_app"
    source_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(min_length=1)


class IntegrationCapabilityInput(ActionRuntimeModel):
    schema_version: Literal["integration_capability_input.v1"] = (
        "integration_capability_input.v1"
    )
    workspace_id: int = Field(gt=0)
    capability_ref: str = Field(min_length=1)
    integration_kind: str = Field(min_length=1)
    enabled: bool = True
    allowed_action_types: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(default="integration_capability", min_length=1)


class IntegrationCapability(ActionRuntimeModel):
    schema_version: Literal["integration_capability.v1"] = "integration_capability.v1"
    workspace_id: int = Field(gt=0)
    capability_ref: str = Field(min_length=1)
    integration_kind: str = Field(min_length=1)
    enabled: bool
    allowed_action_types: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(min_length=1)


class ActionRuntimeExecution(ActionRuntimeModel):
    schema_version: Literal["action_runtime_execution.v1"] = (
        "action_runtime_execution.v1"
    )
    execution_id: str = Field(min_length=1)
    workspace_id: int = Field(gt=0)
    # 0 means workspace-scoped/system action. Customer-facing executions keep
    # real positive conversation/customer ids.
    conversation_id: int = Field(ge=0)
    customer_id: int = Field(ge=0)
    proposal_id: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    status: ExecutionStatus
    reason_code: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    attempt: int = Field(default=1, gt=0)
    delivery_state: str | None = Field(default=None, min_length=1)
    external_message_id: str | None = Field(default=None, min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, min_length=1)


class ActionRuntimeDecision(ActionRuntimeModel):
    schema_version: Literal["action_runtime_decision.v1"] = (
        "action_runtime_decision.v1"
    )
    workspace_id: int = Field(gt=0)
    proposal_id: str = Field(min_length=1)
    state: ActionRuntimeState
    reason_code: str = Field(min_length=1)
    allowed_to_execute: bool = False
    notification_refs: list[str] = Field(default_factory=list)
    execution: ActionRuntimeExecution | None = None


class ActionRuntimeInbox(ActionRuntimeModel):
    schema_version: Literal["action_runtime_inbox.v1"] = "action_runtime_inbox.v1"
    workspace_id: int = Field(gt=0)
    items: list[CommercialActionProposal] = Field(default_factory=list)


OwnerTaskKind = Literal[
    "business",
    "meeting",
    "delivery",
    "stock",
    "call",
    "payment",
    "follow_up",
]
OwnerTaskState = Literal["proposed", "accepted", "blocked", "completed", "dismissed"]
OwnerTaskDueBucket = Literal["today", "overdue", "upcoming", "completed", "proposed"]


class OwnerTaskItem(ActionRuntimeModel):
    schema_version: Literal["owner_task_item.v1"] = "owner_task_item.v1"
    task_id: str = Field(min_length=1)
    workspace_id: int = Field(gt=0)
    proposal_id: str = Field(min_length=1)
    action_type: str = Field(min_length=1)
    kind: OwnerTaskKind
    state: OwnerTaskState
    due_bucket: OwnerTaskDueBucket
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    customer_label: str = Field(min_length=1)
    conversation_id: int = Field(ge=0)
    customer_id: int = Field(ge=0)
    due_at: str | None = None
    status_label: str = Field(min_length=1)
    source_label: str = Field(min_length=1)
    evidence_labels: list[str] = Field(default_factory=list)
    priority: str = Field(min_length=1)
    risk_level: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    can_accept: bool = False
    can_complete: bool = False
    can_snooze: bool = False
    can_message: bool = False
    proposal: CommercialActionProposal


class OwnerTaskProjection(ActionRuntimeModel):
    schema_version: Literal["owner_task_projection.v1"] = "owner_task_projection.v1"
    workspace_id: int = Field(gt=0)
    items: list[OwnerTaskItem] = Field(default_factory=list)
    proposed: list[OwnerTaskItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class ActionRuntimeApprovalInput(ActionRuntimeModel):
    schema_version: Literal["action_runtime_approval_input.v1"] = (
        "action_runtime_approval_input.v1"
    )
    workspace_id: int = Field(gt=0)
    proposal_id: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class ActionRuntimeRejectInput(ActionRuntimeModel):
    schema_version: Literal["action_runtime_reject_input.v1"] = (
        "action_runtime_reject_input.v1"
    )
    workspace_id: int = Field(gt=0)
    proposal_id: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class ActionRuntimeDraftEditInput(ActionRuntimeModel):
    schema_version: Literal["action_runtime_draft_edit_input.v1"] = (
        "action_runtime_draft_edit_input.v1"
    )
    workspace_id: int = Field(gt=0)
    proposal_id: str = Field(min_length=1)
    draft_text: str = Field(min_length=1, max_length=4000)
    actor_ref: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class ActionRuntimeRequeueInput(ActionRuntimeModel):
    schema_version: Literal["action_runtime_requeue_input.v1"] = (
        "action_runtime_requeue_input.v1"
    )
    workspace_id: int = Field(gt=0)
    proposal_id: str = Field(min_length=1)
    patch_payload: dict[str, Any] = Field(default_factory=dict)
    actor_ref: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
