from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CanonicalPipelineStage = Literal[
    "new",
    "qualified",
    "negotiation",
    "proposal",
    "payment",
    "delivery",
    "waiting",
    "won",
    "lost",
    "manual_review",
]


class CrmStageProjection(BaseModel):
    schema_version: Literal["crm_stage.v1"] = "crm_stage.v1"
    stage: CanonicalPipelineStage
    source: Literal["crm_state", "defaulted"]
    raw_stage: str | None = None
    normalized_from: str | None = None
    confidence: float | None = None
    last_intent: str | None = None
    products_interested: list[str] = Field(default_factory=list)
    urgency: bool | None = None
    needs_attention: bool = False
    last_updated: datetime | None = None
    field_provenance: dict[str, str] = Field(default_factory=dict)


class CrmPipelineCard(BaseModel):
    conversation_id: int
    customer_id: int
    customer_name: str | None = None
    channel: str
    stage: CrmStageProjection
    last_message_text: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0
    has_pending_reply: bool = False
    latest_reply_confidence: float | None = None
    contact_type: str | None = None
    needs_attention: bool = False
    deal_value: float | None = None


class CrmPipelineColumn(BaseModel):
    stage: CanonicalPipelineStage
    count: int
    cards: list[CrmPipelineCard] = Field(default_factory=list)


class CrmPipelineProjectionResponse(BaseModel):
    schema_version: Literal["crm_pipeline.v1"] = "crm_pipeline.v1"
    total: int
    stages: list[CrmPipelineColumn]
