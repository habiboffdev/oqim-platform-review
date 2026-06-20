from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.commercial_spine.contracts import utc_now


class AgentRuntimeEventModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


AgentRunState = Literal[
    "queued",
    "running",
    "waiting_approval",
    "waiting_tool",
    "completed",
    "failed",
    "cancelled",
]
AgentRunVisibility = Literal["internal", "owner", "customer_action"]
AgentToolState = Literal["planned", "called", "succeeded", "failed", "blocked"]


class AgentRunInput(AgentRuntimeEventModel):
    schema_version: Literal["agent_run_input.v1"] = "agent_run_input.v1"
    run_id: str = Field(min_length=1)
    workspace_id: int = Field(gt=0)
    agent_id: int = Field(gt=0)
    agent_kind: str = Field(min_length=1)
    trigger_ref: str = Field(min_length=1)
    conversation_id: int = Field(default=0, ge=0)
    customer_id: int = Field(default=0, ge=0)
    state: AgentRunState = "queued"
    permission_mode: str = Field(default="ask_always", min_length=1)
    cache_key: str | None = Field(default=None, min_length=1)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)


class AgentRun(AgentRunInput):
    schema_version: Literal["agent_run.v1"] = "agent_run.v1"
    completed_at: datetime | None = None


class AgentRunEventInput(AgentRuntimeEventModel):
    schema_version: Literal["agent_run_event_input.v1"] = "agent_run_event_input.v1"
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    workspace_id: int = Field(gt=0)
    sequence: int | None = Field(default=None, gt=0)
    event_type: str = Field(min_length=1)
    visibility: AgentRunVisibility
    owner_label: str = Field(default="", max_length=240)
    owner_detail: str = Field(default="", max_length=1000)
    tool_name: str | None = Field(default=None, min_length=1)
    tool_state: AgentToolState | None = None
    action_proposal_id: str | None = Field(default=None, min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class AgentRunEvent(AgentRunEventInput):
    schema_version: Literal["agent_run_event.v1"] = "agent_run_event.v1"
    sequence: int = Field(gt=0)


class AgentRunTimeline(AgentRuntimeEventModel):
    schema_version: Literal["agent_run_timeline.v1"] = "agent_run_timeline.v1"
    workspace_id: int = Field(gt=0)
    run_id: str = Field(min_length=1)
    run: AgentRun | None = None
    events: list[AgentRunEvent] = Field(default_factory=list)


class AgentRunFeed(AgentRuntimeEventModel):
    schema_version: Literal["agent_run_feed.v1"] = "agent_run_feed.v1"
    workspace_id: int = Field(gt=0)
    timelines: list[AgentRunTimeline] = Field(default_factory=list)
