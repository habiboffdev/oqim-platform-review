from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

HealthSeverity = Literal["info", "warning", "critical"]
ReadinessStatus = Literal["not_provisioned", "degraded", "needs_review", "ready"]


class WorkspaceOSIssue(BaseModel):
    code: str = Field(min_length=1)
    severity: HealthSeverity
    target_kind: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    title_uz: str = Field(min_length=1)
    detail_uz: str = Field(min_length=1)
    action_label_uz: str | None = None


class WorkspaceOSReadiness(BaseModel):
    status: ReadinessStatus
    percent: int = Field(ge=0, le=100)
    issues: list[WorkspaceOSIssue] = Field(default_factory=list)


class WorkspaceOSAgentStatus(BaseModel):
    package_key: str
    expected: bool = True
    present: bool
    id: int | None = None
    name: str
    agent_type: str
    is_active: bool = False
    permission_mode: str = "ask_always"
    trust_mode: str = "disabled"
    skill_count: int = 0
    document_section_count: int = 0
    capability_count: int = 0
    tool_grant_count: int = 0
    active_tool_grant_count: int = 0
    trigger_count: int = 0
    active_trigger_count: int = 0
    missing_capability_scopes: list[str] = Field(default_factory=list)
    missing_tool_scopes: list[str] = Field(default_factory=list)
    missing_trigger_count: int = 0
    skill_names: list[str] = Field(default_factory=list)
    document_preview: list["WorkspaceOSDocumentSectionPreview"] = Field(default_factory=list)
    health: Literal["missing", "degraded", "ready"] = "missing"


class WorkspaceOSDocumentSectionPreview(BaseModel):
    section_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body_preview: str = ""
    generated_by: str = "system"
    source_evidence_count: int = 0


class WorkspaceOSDocumentStatus(BaseModel):
    business_section_count: int = 0
    agent_section_count: int = 0
    skill_section_count: int = 0
    owner_edited_section_count: int = 0
    missing_business_sections: list[str] = Field(default_factory=list)
    sections_preview: list[WorkspaceOSDocumentSectionPreview] = Field(default_factory=list)
    business_md_ready: bool = False


class WorkspaceOSSourceStatus(BaseModel):
    status: str = "idle"
    summary: dict[str, int] = Field(default_factory=dict)
    sources: list[dict[str, Any]] = Field(default_factory=list)


class WorkspaceOSActionStatus(BaseModel):
    needs_approval: int = 0
    scheduled: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0
    rejected: int = 0


class WorkspaceOSTaskStatus(BaseModel):
    proposed: int = 0
    active: int = 0
    done: int = 0
    failed: int = 0


class WorkspaceOSProjection(BaseModel):
    schema_version: Literal["workspace_os_projection.v1"] = "workspace_os_projection.v1"
    workspace_id: int
    workspace_name: str
    onboarding_completed: bool
    telegram_connected: bool
    generated_at: datetime
    readiness: WorkspaceOSReadiness
    agents: list[WorkspaceOSAgentStatus]
    documents: WorkspaceOSDocumentStatus
    sources: WorkspaceOSSourceStatus
    actions: WorkspaceOSActionStatus
    tasks: WorkspaceOSTaskStatus
