from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent_documents.contracts import RenderedDocument
from app.modules.business_brain.memory_contracts import AgentGroundingBundle

AgentRuntimeKind = Literal[
    "seller_agent",
    "support_agent",
    "catalog_update_agent",
    "follow_up_agent",
    "bi_agent",
    "custom_agent",
    "promoter_agent",
    "setup_agent",
]


class AgentRuntimeContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentRuntimeContextRequest(AgentRuntimeContextModel):
    schema_version: Literal["agent_runtime_context_request.v1"] = (
        "agent_runtime_context_request.v1"
    )
    workspace_id: int = Field(gt=0)
    agent_id: int = Field(gt=0)
    conversation_id: int | None = Field(default=None, gt=0)
    agent_session_id: int | None = Field(default=None, gt=0)
    hermes_session_id: str | None = None
    query_text: str | None = None
    requested_fact_types: list[str] = Field(default_factory=list)
    requested_slots: list[str] = Field(default_factory=list)
    query_modalities: list[Literal["text", "image", "audio", "video", "pdf", "file"]] = (
        Field(default_factory=list)
    )
    recent_message_limit: int = Field(default=50, ge=0, le=50)
    include_grounding: bool = True
    include_agent_session_summary: bool = True
    transcript_event_limit: int = Field(default=20, ge=0, le=50)
    include_proposed_knowledge: bool = False
    enable_semantic: bool = True
    enable_contextual_rank: bool = True
    enable_query_rewrite: bool = False
    enable_agentic_search: bool = True
    enable_rerank: bool = False


class AgentRuntimeMessage(AgentRuntimeContextModel):
    id: int
    conversation_id: int
    sender_type: str
    content: str
    media_type: str | None = None
    media_description: str | None = None
    transcription: str | None = None
    created_at: datetime | None = None


class AgentRuntimePermissionContext(AgentRuntimeContextModel):
    internal_capabilities: list[str] = Field(default_factory=list)
    expected_external_scopes: list[str] = Field(default_factory=list)
    active_external_scopes: list[str] = Field(default_factory=list)
    missing_external_scopes: list[str] = Field(default_factory=list)
    permission_mode: str
    trust_mode: str


class AgentRuntimeCachePlan(AgentRuntimeContextModel):
    cache_scope: Literal["agent"]
    cache_key: str
    material_hash: str
    cacheable: bool = True
    invalidation_refs: list[str] = Field(default_factory=list)


class AgentRuntimeDocumentContext(AgentRuntimeContextModel):
    business_md: RenderedDocument
    agent_md: RenderedDocument
    skill_md: list[RenderedDocument] = Field(default_factory=list)


class AgentRuntimeContext(AgentRuntimeContextModel):
    schema_version: Literal["agent_runtime_context.v1"] = "agent_runtime_context.v1"
    workspace_id: int
    agent_id: int
    agent_name: str
    agent_kind: AgentRuntimeKind
    documents: AgentRuntimeDocumentContext
    permissions: AgentRuntimePermissionContext
    recent_messages: list[AgentRuntimeMessage] = Field(default_factory=list)
    session_summary: str = ""
    transcript_hits: list[str] = Field(default_factory=list)
    grounding: AgentGroundingBundle | None = None
    cache_plan: AgentRuntimeCachePlan
    prompt_sections: dict[str, Any] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)
    telemetry: dict[str, Any] = Field(default_factory=dict)
