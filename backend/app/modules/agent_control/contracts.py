from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


AgentControlActionKind = Literal[
    "reply.send",
    "reply.edit",
    "knowledge.write",
    "knowledge.promote",
    "catalog.update",
    "rule.update",
    "automation.update",
    "integration.write",
    "broadcast.send",
]
AgentControlRiskLevel = Literal["low", "medium", "high", "critical"]
AgentControlPolicyDecision = Literal["execute", "approve", "draft", "deny"]
AgentControlStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "executed",
    "expired",
    "failed",
]


class AgentControlActionInput(AgentControlModel):
    schema_version: Literal["agent_control_action_input.v1"] = (
        "agent_control_action_input.v1"
    )
    workspace_id: int = Field(gt=0)
    user_id: str = Field(min_length=1)
    agent_id: int | None = Field(default=None, gt=0)
    hermes_run_id: str | None = Field(default=None, min_length=1)
    action_kind: AgentControlActionKind
    target_ref: str = Field(min_length=1)
    proposed_payload: dict[str, Any] = Field(default_factory=dict)
    risk_level: AgentControlRiskLevel = "low"
    evidence_refs: list[str] = Field(default_factory=list)
    approval_required: bool = False
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class AgentControlAction(AgentControlModel):
    schema_version: Literal["agent_control_action.v1"] = "agent_control_action.v1"
    action_id: str = Field(min_length=1)
    workspace_id: int = Field(gt=0)
    user_id: str = Field(min_length=1)
    agent_id: int | None = Field(default=None, gt=0)
    hermes_run_id: str | None = Field(default=None, min_length=1)
    action_kind: AgentControlActionKind
    target_ref: str = Field(min_length=1)
    proposed_payload: dict[str, Any] = Field(default_factory=dict)
    risk_level: AgentControlRiskLevel
    evidence_refs: list[str] = Field(min_length=1)
    policy_decision: AgentControlPolicyDecision
    status: AgentControlStatus
    proposal_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class AgentControlDecision(AgentControlModel):
    schema_version: Literal["agent_control_decision.v1"] = "agent_control_decision.v1"
    action_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    status: AgentControlStatus
    actor_ref: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)

