from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RetrievalCoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalContextRequest(RetrievalCoreModel):
    schema_version: Literal["retrieval_context_request.v1"] = (
        "retrieval_context_request.v1"
    )
    workspace_id: int = Field(gt=0)
    requested_fact_types: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    candidate_fact_ids: list[str] = Field(default_factory=list)
    requested_slots: list[str] = Field(default_factory=list)
    search_probes: list[str] = Field(default_factory=list, max_length=5)
    search_fact_types: list[str] = Field(default_factory=list, max_length=12)
    search_modalities: list[Literal["text", "image", "audio", "video", "pdf", "file"]] = (
        Field(default_factory=list, max_length=6)
    )
    query_text: str | None = None
    query_modalities: list[Literal["text", "image", "audio", "video", "pdf", "file"]] = (
        Field(default_factory=list)
    )
    query_embedding: list[float] | None = None
    minimum_lexical_score: float = Field(default=0.0, ge=0.0, le=1.0)
    enable_semantic: bool = True
    enable_query_rewrite: bool = False
    enable_agentic_search: bool = False
    enable_rerank: bool = False
    include_proposed: bool = False
    include_source_units: bool = True
    limit: int = Field(default=50, ge=1, le=250)


class RetrievalAgentGroundingRequest(RetrievalCoreModel):
    schema_version: Literal["retrieval_agent_grounding_request.v1"] = (
        "retrieval_agent_grounding_request.v1"
    )
    workspace_id: int = Field(gt=0)
    agent_kind: Literal[
        "seller_agent",
        "bi_agent",
        "promoter_agent",
        "support_agent",
        "catalog_update_agent",
        "follow_up_agent",
        "custom_agent",
        "setup_agent",
    ]
    requested_fact_types: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    requested_slots: list[str] = Field(default_factory=list)
    search_probes: list[str] = Field(default_factory=list, max_length=5)
    search_fact_types: list[str] = Field(default_factory=list, max_length=12)
    search_modalities: list[Literal["text", "image", "audio", "video", "pdf", "file"]] = (
        Field(default_factory=list, max_length=6)
    )
    query_text: str | None = None
    query_modalities: list[Literal["text", "image", "audio", "video", "pdf", "file"]] = (
        Field(default_factory=list)
    )
    query_embedding: list[float] | None = None
    minimum_lexical_score: float = Field(default=0.0, ge=0.0, le=1.0)
    enable_semantic: bool = True
    enable_contextual_rank: bool = True
    enable_query_rewrite: bool = False
    enable_agentic_search: bool = False
    enable_rerank: bool = False
    include_proposed: bool = False


class RetrievalQueryRewriteOutput(RetrievalCoreModel):
    schema_version: Literal["retrieval_query_rewrite_output.v1"] = (
        "retrieval_query_rewrite_output.v1"
    )
    rewrites: list[str] = Field(default_factory=list, max_length=5)


class RetrievalAgenticSearchOutput(RetrievalCoreModel):
    schema_version: Literal["retrieval_agentic_search_output.v1"] = (
        "retrieval_agentic_search_output.v1"
    )
    queries: list[str] = Field(default_factory=list, max_length=5)
    fact_types: list[str] = Field(default_factory=list, max_length=12)
    query_modalities: list[Literal["text", "image", "audio", "video", "pdf", "file"]] = (
        Field(default_factory=list, max_length=6)
    )
