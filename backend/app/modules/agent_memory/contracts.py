from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentMemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


AuthorityState = Literal["approved", "candidate", "proposed", "degraded"]
WarningSeverity = Literal["info", "warning", "error"]
SearchStatus = Literal["ok", "empty", "degraded"]


class AuthorityWarning(AgentMemoryModel):
    schema_version: Literal["authority_warning.v1"] = "authority_warning.v1"
    code: str
    message: str = ""
    severity: WarningSeverity = "warning"
    target_ref: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthorityBundle(AgentMemoryModel):
    schema_version: Literal["authority_bundle.v1"] = "authority_bundle.v1"
    domain: str
    kind: str
    authority: AuthorityState = "approved"
    claim_scope: list[str] = Field(default_factory=list)
    text: str = ""
    object: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[AuthorityWarning] = Field(default_factory=list)


class SituationBundle(AgentMemoryModel):
    schema_version: Literal["situation_bundle.v1"] = "situation_bundle.v1"
    domain: str = "conversation.situation"
    kind: str = "summary"
    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StyleBundle(AgentMemoryModel):
    schema_version: Literal["style_bundle.v1"] = "style_bundle.v1"
    domain: str = "style.voice"
    kind: str
    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillBundle(AgentMemoryModel):
    schema_version: Literal["skill_bundle.v1"] = "skill_bundle.v1"
    domain: str = "agent.skill"
    kind: str
    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionOptionBundle(AgentMemoryModel):
    schema_version: Literal["action_option_bundle.v1"] = "action_option_bundle.v1"
    kind: str
    title: str
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class AgentMemoryBundle(AgentMemoryModel):
    schema_version: Literal["agent_memory_bundle.v1"] = "agent_memory_bundle.v1"
    authority_lane: list[AuthorityBundle] = Field(default_factory=list)
    situation_lane: list[SituationBundle] = Field(default_factory=list)
    style_lane: list[StyleBundle] = Field(default_factory=list)
    skill_lane: list[SkillBundle] = Field(default_factory=list)
    action_lane: list[ActionOptionBundle] = Field(default_factory=list)
    warnings: list[AuthorityWarning] = Field(default_factory=list)
    evidence_budget: dict[str, Any] = Field(default_factory=dict)


class BrainMemorySearchRequest(AgentMemoryModel):
    schema_version: Literal["brain_memory_search_request.v1"] = (
        "brain_memory_search_request.v1"
    )
    workspace_id: int = Field(gt=0)
    agent_id: int | None = Field(default=None, gt=0)
    conversation_id: int | None = Field(default=None, gt=0)
    user_id: int | None = Field(default=None, gt=0)
    project_id: int | None = Field(default=None, gt=0)
    query: str
    domains: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    include_proposed: bool = False
    limit: int = Field(default=8, ge=1, le=50)


class BrainMemorySearchResult(AgentMemoryModel):
    schema_version: Literal["brain_memory_search_result.v1"] = (
        "brain_memory_search_result.v1"
    )
    status: SearchStatus = "ok"
    query: str
    authority_lane: list[AuthorityBundle] = Field(default_factory=list)
    situation_lane: list[SituationBundle] = Field(default_factory=list)
    style_lane: list[StyleBundle] = Field(default_factory=list)
    skill_lane: list[SkillBundle] = Field(default_factory=list)
    action_lane: list[ActionOptionBundle] = Field(default_factory=list)
    warnings: list[AuthorityWarning] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
