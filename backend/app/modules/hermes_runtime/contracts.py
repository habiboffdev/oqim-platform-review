from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.commercial_spine.contracts import utc_now


class HermesRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HermesRunLane(StrEnum):
    FAST_INTERACTIVE = "fast_interactive"
    BACKGROUND = "background"
    BROADCAST = "broadcast"
    DEEP_ANALYSIS = "deep_analysis"


class HermesRunMode(StrEnum):
    REPLY = "reply"
    PERSONAL = "personal"
    BROADCAST = "broadcast"
    SCAN = "scan"
    ENTERPRISE_QA = "enterprise_qa"
    LEARNING = "learning"


class HermesRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEDUPED = "deduped"
    SKIPPED = "skipped"


class HermesRunEventKind(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    STARTED = "started"
    LANE_WAITED = "lane_waited"
    CONTEXT_GATHERED = "context_gathered"
    LLM_CALLED = "llm_called"
    TOOL_CALLED = "tool_called"
    POLICY_CHECKED = "policy_checked"
    ACTION_PROPOSED = "action_proposed"
    ACTION_EXECUTED = "action_executed"
    COMPLETED = "completed"
    FAILED = "failed"
    DEDUPED = "deduped"
    SKIPPED = "skipped"


def build_hermes_run_idempotency_key(
    *,
    workspace_id: int,
    agent_id: int | None,
    trigger_type: str,
    trigger_id: str,
    run_mode: HermesRunMode | str,
) -> str:
    """Stable run key shared by trigger, retry, and dedupe paths."""

    mode_value = run_mode.value if isinstance(run_mode, HermesRunMode) else str(run_mode)
    agent_ref = str(agent_id) if agent_id is not None else "system"
    return f"hermes_run:{workspace_id}:{agent_ref}:{trigger_type}:{trigger_id}:{mode_value}"


class HermesRunInput(HermesRuntimeModel):
    schema_version: Literal["hermes_run_input.v1"] = "hermes_run_input.v1"
    run_id: str = Field(default_factory=lambda: f"hermes_run:{uuid4().hex}", min_length=1)
    workspace_id: int = Field(gt=0)
    tenant_id: int | None = Field(default=None, gt=0)
    agent_id: int | None = Field(default=None, gt=0)
    agent_kind: str = Field(default="agent", min_length=1)
    lane: HermesRunLane = HermesRunLane.FAST_INTERACTIVE
    run_mode: HermesRunMode = HermesRunMode.REPLY
    trigger_type: str = Field(default="manual", min_length=1, max_length=80)
    trigger_id: str = Field(min_length=1, max_length=255)
    event_id: str | None = Field(default=None, min_length=1, max_length=255)
    conversation_id: int | None = Field(default=None, gt=0)
    customer_id: int | None = Field(default=None, gt=0)
    runtime_profile_snapshot_id: str | None = Field(default=None, min_length=1, max_length=255)
    runtime_profile_cache_key: str | None = Field(default=None, min_length=1, max_length=255)
    engine_run_id: str | None = Field(default=None, min_length=1, max_length=255)
    correlation_id: str = Field(default_factory=lambda: f"corr:{uuid4().hex}", min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=512)
    state: HermesRunState = HermesRunState.QUEUED
    source_refs: list[str] = Field(default_factory=list)
    input_summary: str = Field(default="", max_length=2000)
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _default_tenant_and_idempotency(self) -> HermesRunInput:
        if self.tenant_id is None:
            self.tenant_id = self.workspace_id
        if self.idempotency_key is None:
            self.idempotency_key = build_hermes_run_idempotency_key(
                workspace_id=self.workspace_id,
                agent_id=self.agent_id,
                trigger_type=self.trigger_type,
                trigger_id=self.trigger_id,
                run_mode=self.run_mode,
            )
        return self


class HermesRunPatch(HermesRuntimeModel):
    schema_version: Literal["hermes_run_patch.v1"] = "hermes_run_patch.v1"
    state: HermesRunState | None = None
    engine_run_id: str | None = Field(default=None, min_length=1, max_length=255)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_latency_ms: int | None = Field(default=None, ge=0)
    llm_latency_ms: int | None = Field(default=None, ge=0)
    llm_calls: int | None = Field(default=None, ge=0)
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings_count: int | None = Field(default=None, ge=0)
    tool_errors_count: int | None = Field(default=None, ge=0)
    output_action: str | None = Field(default=None, min_length=1, max_length=120)
    output_ref: str | None = Field(default=None, min_length=1, max_length=255)
    error_code: str | None = Field(default=None, min_length=1, max_length=120)
    error_message: str | None = Field(default=None, min_length=1, max_length=2000)
    details: dict[str, Any] | None = None


class HermesRunSnapshot(HermesRuntimeModel):
    schema_version: Literal["hermes_run.v1"] = "hermes_run.v1"
    id: int | None = None
    run_id: str
    workspace_id: int
    tenant_id: int | None = None
    agent_id: int | None = None
    agent_kind: str
    lane: HermesRunLane
    run_mode: HermesRunMode
    trigger_type: str
    trigger_id: str
    event_id: str | None = None
    conversation_id: int | None = None
    customer_id: int | None = None
    runtime_profile_snapshot_id: str | None = None
    runtime_profile_cache_key: str | None = None
    engine_run_id: str | None = None
    correlation_id: str
    idempotency_key: str
    state: HermesRunState
    source_refs: list[str] = Field(default_factory=list)
    input_summary: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_latency_ms: int | None = None
    llm_latency_ms: int | None = None
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    total_tokens: int = 0
    confidence: float | None = None
    warnings_count: int = 0
    tool_errors_count: int = 0
    output_action: str | None = None
    output_ref: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    deduped: bool = False
    created_at: datetime
    updated_at: datetime


class HermesRunEventInput(HermesRuntimeModel):
    schema_version: Literal["hermes_run_event_input.v1"] = "hermes_run_event_input.v1"
    event_id: str | None = Field(default=None, min_length=1, max_length=255)
    run_id: str = Field(min_length=1)
    workspace_id: int = Field(gt=0)
    sequence: int | None = Field(default=None, gt=0)
    kind: HermesRunEventKind
    visibility: Literal["internal", "owner", "customer_action"] = "internal"
    owner_label: str = Field(default="", max_length=240)
    owner_detail: str = Field(default="", max_length=1000)
    tool_name: str | None = Field(default=None, min_length=1, max_length=120)
    tool_state: str | None = Field(default=None, min_length=1, max_length=80)
    action_proposal_id: str | None = Field(default=None, min_length=1, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = Field(default_factory=lambda: f"corr:{uuid4().hex}", min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=512)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _default_ids(self) -> HermesRunEventInput:
        if self.event_id is None:
            self.event_id = f"{self.run_id}:{self.kind}:{uuid4().hex}"
        if self.idempotency_key is None:
            self.idempotency_key = f"{self.run_id}:{self.kind}:{self.event_id}"
        return self


class HermesRunEventSnapshot(HermesRunEventInput):
    schema_version: Literal["hermes_run_event.v1"] = "hermes_run_event.v1"
    id: int | None = None
    hermes_run_id: int | None = None
    sequence: int = Field(gt=0)
