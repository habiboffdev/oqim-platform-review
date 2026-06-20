from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.conversation import (
    ConversationNextBestActionSchema,
    ConversationTailProjectionSchema,
)
from app.schemas.crm import CrmStageProjection


class CustomerResponse(BaseModel):
    id: int
    display_name: str
    phone_number: str | None
    contact_type: str = "customer"
    classification_confidence: float | None = None
    classification_corrected: bool = False
    language: str
    tags: list[str] = []
    lifetime_value: float
    notes: str | None
    ai_brief: str | None = None
    address: str | None = None
    ai_muted: bool = False
    conversation_count: int = 0
    last_conversation_at: datetime | None = None
    stage: str | None = None
    crm_stage: CrmStageProjection | None = None
    latest_conversation_id: int | None = None
    latest_conversation_tail: ConversationTailProjectionSchema | None = None
    next_best_action: ConversationNextBestActionSchema | None = None
    needs_followup: bool = False
    has_pending_reply: bool = False
    latest_reply_confidence: float | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CustomerConversation(BaseModel):
    id: int
    pipeline_stage: str
    crm_stage: CrmStageProjection | None = None
    summary: str | None
    last_message_at: datetime | None
    agent_name: str | None = None
    avg_confidence: float | None = None


class CustomerDetail(CustomerResponse):
    conversations: list[CustomerConversation] = []
    ai_summary: str | None = None


class CustomerCreate(BaseModel):
    display_name: str
    phone_number: str | None = None
    language: str = "uz"
    tags: list[str] = []
    notes: str | None = None


class CustomerUpdate(BaseModel):
    display_name: str | None = None
    phone_number: str | None = None
    contact_type: str | None = None
    language: str | None = None
    tags: list[str] | None = None
    notes: str | None = None
    ai_brief: str | None = None
    address: str | None = None
    ai_muted: bool | None = None


class CustomerCrmStageSummary(BaseModel):
    stage: str
    count: int


class CustomerCrmListProjection(BaseModel):
    schema_version: Literal["customer_crm_list.v1"] = "customer_crm_list.v1"
    scope: Literal["page"] = "page"
    total: int
    stages: list[CustomerCrmStageSummary]
    needs_attention_count: int = 0
    pending_reply_count: int = 0


class CustomerListResponse(BaseModel):
    customers: list[CustomerResponse]
    total: int
    avg_ltv: float
    new_this_week: int
    crm_summary: CustomerCrmListProjection | None = None
