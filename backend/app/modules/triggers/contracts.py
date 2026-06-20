from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EVENT_SOURCES = frozenset(
    {
        "channel_message_received",
        "conversation_state_changed",
        "customer_stage_changed",
        "source_added",
        "source_changed",
        "schedule",
        "owner_bi_command",
        "integration_webhook",
        "task_due",
        "catalog_conflict_detected",
        "instagram_comment_received",
    }
)

PermissionModeLiteral = Literal["ask_always", "auto_approve", "full_access"]


class TriggerKind(StrEnum):
    MESSAGE = "message"
    OWNER_COMMAND = "owner_command"
    SCHEDULE = "schedule"
    SOURCE_CHANGE = "source_change"
    WEBHOOK = "webhook"
    TASK_DUE = "task_due"
    SCAN = "scan"
    LEARNING = "learning"


class TriggerRunMode(StrEnum):
    REPLY = "reply"
    DRAFT = "draft"
    SILENT = "silent"
    OWNER_ONLY = "owner_only"
    BROADCAST = "broadcast"
    SCANNER = "scanner"


_PHASE3_EVENT_SOURCE = {
    TriggerKind.MESSAGE: "channel_message_received",
    TriggerKind.OWNER_COMMAND: "owner_bi_command",
    TriggerKind.SCHEDULE: "schedule",
    TriggerKind.SOURCE_CHANGE: "source_changed",
    TriggerKind.WEBHOOK: "integration_webhook",
    TriggerKind.TASK_DUE: "task_due",
    TriggerKind.SCAN: "owner_bi_command",
    TriggerKind.LEARNING: "source_changed",
}


class TriggerInput(BaseModel):
    """Owner-supplied trigger request.

    The frontend never sets `idempotency_key`; it is derived from
    (event_source, action_proposal_type, matching_scope) so two requests with
    the same shape collapse to one durable row.
    """

    model_config = ConfigDict(extra="forbid")

    owner_agent_id: int
    event_source: str = Field(min_length=3, max_length=64)
    action_proposal_type: str = Field(min_length=3, max_length=120)
    matching_scope: dict[str, Any] = Field(default_factory=dict)
    permission_mode: PermissionModeLiteral = "ask_always"
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)

    @field_validator("event_source")
    @classmethod
    def _known_event_source(cls, value: str) -> str:
        if value not in EVENT_SOURCES:
            raise ValueError(f"event_source must be one of {sorted(EVENT_SOURCES)}")
        return value

    def compute_idempotency_key(self) -> str:
        digest_input = json.dumps(
            {
                "owner_agent_id": self.owner_agent_id,
                "event_source": self.event_source,
                "action_proposal_type": self.action_proposal_type,
                "matching_scope": self.matching_scope,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:32]


class TriggerProposalInput(BaseModel):
    """Owner-visible trigger change request.

    Trigger changes start or stop agent automation, so the UI should create an
    Action Proposal and let Action Runtime apply it after approval.
    """

    model_config = ConfigDict(extra="forbid")

    operation: Literal["create", "deactivate"]
    trigger_id: int | None = Field(default=None, gt=0)
    event_source: str | None = Field(default=None, min_length=3, max_length=64)
    action_proposal_type: str | None = Field(default=None, min_length=3, max_length=120)
    matching_scope: dict[str, Any] = Field(default_factory=dict)
    permission_mode: PermissionModeLiteral = "ask_always"
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)
    correlation_id: str = Field(
        default="api:intelligence:agent_trigger",
        min_length=1,
        max_length=200,
    )

    @field_validator("event_source")
    @classmethod
    def _known_event_source(cls, value: str | None) -> str | None:
        if value is not None and value not in EVENT_SOURCES:
            raise ValueError(f"event_source must be one of {sorted(EVENT_SOURCES)}")
        return value

    @model_validator(mode="after")
    def _validate_operation_payload(self) -> TriggerProposalInput:
        if self.operation == "create":
            if self.event_source is None:
                raise ValueError("event_source is required for create")
            if self.action_proposal_type is None:
                raise ValueError("action_proposal_type is required for create")
        if self.operation == "deactivate" and self.trigger_id is None:
            raise ValueError("trigger_id is required for deactivate")
        return self

    def to_trigger_input(self, *, owner_agent_id: int) -> TriggerInput:
        return TriggerInput(
            owner_agent_id=owner_agent_id,
            event_source=str(self.event_source),
            action_proposal_type=str(self.action_proposal_type),
            matching_scope=dict(self.matching_scope),
            permission_mode=self.permission_mode,
            retry_policy=dict(self.retry_policy),
            notes=self.notes,
        )


class Phase3TriggerDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_agent_id: int
    kind: TriggerKind
    status: Literal["active", "paused", "archived"] = "active"
    run_mode: TriggerRunMode = TriggerRunMode.REPLY
    event_filters: dict[str, Any] = Field(default_factory=dict)
    idempotency_scope: dict[str, Any] = Field(default_factory=dict)
    lane: Literal["fast_interactive", "background"] = "fast_interactive"
    priority: int = Field(default=100, ge=0, le=1000)
    permission_mode: PermissionModeLiteral = "ask_always"

    def to_trigger_input(self) -> TriggerInput:
        matching_scope = dict(self.event_filters)
        matching_scope["phase3"] = {
            "kind": self.kind.value,
            "run_mode": self.run_mode.value,
            "lane": self.lane,
            "priority": self.priority,
            "idempotency_scope": self.idempotency_scope,
        }
        return TriggerInput(
            owner_agent_id=self.owner_agent_id,
            event_source=_PHASE3_EVENT_SOURCE[self.kind],
            action_proposal_type=f"hermes.{self.run_mode.value}",
            matching_scope=matching_scope,
            permission_mode=self.permission_mode,
        )


class TriggerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    owner_agent_id: int
    event_source: str
    matching_scope: dict[str, Any]
    permission_mode: str
    action_proposal_type: str
    idempotency_key: str
    retry_policy: dict[str, Any]
    last_run_status: str | None
    last_run_at: datetime | None
    run_count: int
    audit_metadata: dict[str, Any]
    notes: str
    active: bool
    created_at: datetime
    updated_at: datetime
