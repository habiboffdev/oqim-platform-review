from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.agent_control.contracts import AgentControlAction


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


KnowledgeOwnerType = Literal["user", "workspace"]
KnowledgeItemKind = Literal[
    "note",
    "script",
    "doc",
    "chat",
    "media",
    "catalog",
    "faq",
    "policy",
    "source",
]
KnowledgeSourceKind = Literal["paste", "upload", "telegram", "drive", "agent_note", "catalog"]
KnowledgeAuthorityState = Literal["source", "candidate", "approved", "rejected", "stale"]
KnowledgeVisibility = Literal["private", "workspace", "agent_scoped"]
KnowledgeCreatedBy = Literal["user", "agent", "connector", "system"]
KnowledgeCandidateStatus = Literal["pending", "approved", "rejected", "merged"]
KnowledgeChatSenderType = Literal["seller", "customer", "ai"]
KnowledgeQueryModality = Literal["text", "image", "audio", "video", "pdf", "file"]


class KnowledgeScope(KnowledgeModel):
    schema_version: Literal["knowledge_scope.v1"] = "knowledge_scope.v1"
    owner_type: KnowledgeOwnerType
    owner_id: str = Field(min_length=1)
    workspace_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def workspace_owner_requires_workspace_id(self) -> KnowledgeScope:
        if self.owner_type == "workspace" and self.workspace_id is None:
            raise ValueError("workspace knowledge requires workspace_id")
        return self


class KnowledgeCollection(KnowledgeModel):
    schema_version: Literal["knowledge_collection.v1"] = "knowledge_collection.v1"
    collection_id: str = Field(min_length=1)
    scope: KnowledgeScope
    title: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSource(KnowledgeModel):
    schema_version: Literal["knowledge_source.v1"] = "knowledge_source.v1"
    source_id: str = Field(min_length=1)
    scope: KnowledgeScope
    source_kind: KnowledgeSourceKind
    external_ref: str | None = None
    checksum: str = Field(min_length=1)
    acl_snapshot: dict[str, Any] = Field(default_factory=dict)
    freshness: dict[str, Any] = Field(default_factory=dict)
    ingestion_status: str = Field(default="ready", min_length=1)
    raw_content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeItem(KnowledgeModel):
    schema_version: Literal["knowledge_item.v1"] = "knowledge_item.v1"
    item_id: str = Field(min_length=1)
    scope: KnowledgeScope
    kind: KnowledgeItemKind
    title: str = Field(min_length=1)
    body_text: str = ""
    source_refs: list[str] = Field(default_factory=list)
    collection_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authority_state: KnowledgeAuthorityState
    visibility: KnowledgeVisibility
    created_by: KnowledgeCreatedBy
    created_by_ref: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeChunk(KnowledgeModel):
    schema_version: Literal["knowledge_chunk.v1"] = "knowledge_chunk.v1"
    chunk_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    scope: KnowledgeScope
    text: str = Field(min_length=1)
    contextual_prefix: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    citation: dict[str, Any] = Field(default_factory=dict)
    embedding_model: str | None = None
    embedding_state: str = "pending"
    embedding_degraded_reason: str | None = None


class KnowledgeCandidate(KnowledgeModel):
    schema_version: Literal["knowledge_candidate.v1"] = "knowledge_candidate.v1"
    candidate_id: str = Field(min_length=1)
    scope: KnowledgeScope
    source_id: str = Field(min_length=1)
    proposed_kind: str = Field(min_length=1)
    proposed_payload: dict[str, Any]
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    status: KnowledgeCandidateStatus = "pending"
    agent_control_action_id: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSaveInput(KnowledgeModel):
    schema_version: Literal["knowledge_save_input.v1"] = "knowledge_save_input.v1"
    scope: KnowledgeScope
    kind: KnowledgeItemKind
    title: str = Field(min_length=1)
    body_text: str = Field(min_length=1)
    collection_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_kind: KnowledgeSourceKind = "agent_note"
    external_ref: str | None = None
    authority_state: KnowledgeAuthorityState = "source"
    visibility: KnowledgeVisibility = "private"
    created_by: KnowledgeCreatedBy = "agent"
    created_by_ref: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class KnowledgeSearchRequest(KnowledgeModel):
    schema_version: Literal["knowledge_search_request.v1"] = "knowledge_search_request.v1"
    scope: KnowledgeScope
    query: str = Field(min_length=1)
    collection_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    authority_states: list[KnowledgeAuthorityState] = Field(default_factory=list)
    enable_semantic: bool = False
    query_embedding: list[float] | None = Field(default=None, exclude=True)
    limit: int = Field(default=10, ge=1, le=50)


class KnowledgeChatMemorySearchRequest(KnowledgeModel):
    schema_version: Literal["knowledge_chat_memory_search_request.v1"] = (
        "knowledge_chat_memory_search_request.v1"
    )
    workspace_id: int = Field(gt=0)
    query: str = Field(min_length=1)
    conversation_id: int | None = Field(default=None, gt=0)
    sender_types: list[KnowledgeChatSenderType] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=50)


class KnowledgeCatalogSearchRequest(KnowledgeModel):
    schema_version: Literal["knowledge_catalog_search_request.v1"] = (
        "knowledge_catalog_search_request.v1"
    )
    workspace_id: int = Field(gt=0)
    query: str = Field(min_length=1)
    query_modalities: list[KnowledgeQueryModality] = Field(default_factory=list)
    include_media: bool = True
    enable_semantic: bool = True
    enable_rerank: bool = True
    limit: int = Field(default=10, ge=1, le=50)


class KnowledgeMediaSearchRequest(KnowledgeModel):
    schema_version: Literal["knowledge_media_search_request.v1"] = (
        "knowledge_media_search_request.v1"
    )
    workspace_id: int = Field(gt=0)
    query: str = Field(min_length=1)
    query_modalities: list[KnowledgeQueryModality] = Field(default_factory=lambda: ["image"])
    enable_semantic: bool = True
    enable_rerank: bool = True
    limit: int = Field(default=10, ge=1, le=50)


class KnowledgeSearchHit(KnowledgeModel):
    schema_version: Literal["knowledge_search_hit.v1"] = "knowledge_search_hit.v1"
    item: KnowledgeItem
    score: float = Field(ge=0.0)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeSearchResult(KnowledgeModel):
    schema_version: Literal["knowledge_search_result.v1"] = "knowledge_search_result.v1"
    hits: list[KnowledgeSearchHit] = Field(default_factory=list)


class KnowledgeGetItemRequest(KnowledgeModel):
    schema_version: Literal["knowledge_get_item_request.v1"] = (
        "knowledge_get_item_request.v1"
    )
    scope: KnowledgeScope
    item_id: str = Field(min_length=1)


class KnowledgeItemDetail(KnowledgeModel):
    schema_version: Literal["knowledge_item_detail.v1"] = "knowledge_item_detail.v1"
    item: KnowledgeItem
    sources: list[KnowledgeSource] = Field(default_factory=list)
    chunks: list[KnowledgeChunk] = Field(default_factory=list)


class KnowledgeExplainSourcesRequest(KnowledgeModel):
    schema_version: Literal["knowledge_explain_sources_request.v1"] = (
        "knowledge_explain_sources_request.v1"
    )
    scope: KnowledgeScope
    item_id: str = Field(min_length=1)


class KnowledgeSourceExplanation(KnowledgeModel):
    schema_version: Literal["knowledge_source_explanation.v1"] = (
        "knowledge_source_explanation.v1"
    )
    item_id: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    sources: list[KnowledgeSource] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    chunks: list[KnowledgeChunk] = Field(default_factory=list)


class KnowledgeAttachToCollectionInput(KnowledgeModel):
    schema_version: Literal["knowledge_attach_to_collection_input.v1"] = (
        "knowledge_attach_to_collection_input.v1"
    )
    scope: KnowledgeScope
    item_id: str = Field(min_length=1)
    collection_ids: list[str] = Field(min_length=1)


class KnowledgeTagItemInput(KnowledgeModel):
    schema_version: Literal["knowledge_tag_item_input.v1"] = (
        "knowledge_tag_item_input.v1"
    )
    scope: KnowledgeScope
    item_id: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)


class KnowledgeCandidateInput(KnowledgeModel):
    schema_version: Literal["knowledge_candidate_input.v1"] = "knowledge_candidate_input.v1"
    scope: KnowledgeScope
    source_id: str = Field(min_length=1)
    proposed_kind: str = Field(min_length=1)
    proposed_payload: dict[str, Any]
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    created_by_ref: str = Field(min_length=1)
    hermes_run_id: str | None = Field(default=None, min_length=1)
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class KnowledgeCandidateProposal(KnowledgeModel):
    schema_version: Literal["knowledge_candidate_proposal.v1"] = (
        "knowledge_candidate_proposal.v1"
    )
    candidate: KnowledgeCandidate
    action: AgentControlAction
