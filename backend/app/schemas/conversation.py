from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.crm import CrmStageProjection


class ConversationCrmSnapshot(BaseModel):
    pipeline_stage: str
    lead_score: float | None = None
    last_intent: str | None = None
    products_interested: list[str] = Field(default_factory=list)
    urgency: bool | None = None
    needs_attention: bool = False
    media_ready: bool | None = None
    last_updated: datetime | None = None


class ConversationNextBestActionSchema(BaseModel):
    action: str
    ready: bool
    reason: str


class ConversationTailGapSchema(BaseModel):
    reason: str
    before_external_message_id: str | None = None
    after_external_message_id: str | None = None


class ConversationTailProjectionSchema(BaseModel):
    schema_version: str = "conversation_tail.v1"
    status: str
    source: str
    latest_message_text: str | None = None
    latest_message_at: datetime | None = None
    unread_count: int = 0
    unread_source: str
    latest_conversation_seq: int = 0
    latest_conversation_revision: int = 0
    gap: ConversationTailGapSchema | None = None


class ConversationHydrationProjectionSchema(BaseModel):
    schema_version: str = "conversation_hydration_runtime.v1"
    state: str
    reason: str = "chat_open"
    needed: bool = False
    can_retry: bool = False
    attempt_count: int = 0
    max_attempts: int = 3
    requested_count: int = 0
    persisted_count: int = 0
    duplicate_count: int = 0
    last_error: str | None = None
    next_attempt_at: datetime | None = None
    requested_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    updated_at: datetime | None = None


class ConversationResponse(BaseModel):
    id: int
    customer_id: int
    customer_name: str | None = None
    channel: str = "dm"
    telegram_chat_id: int | None = None
    external_chat_id: str | None = None
    external_thread_id: str | None = None
    pipeline_stage: str
    override_mode: str = "auto"
    summary: str | None
    needs_attention: bool
    read_outbox_max_id: int | None = None
    deal_value: float | None = None
    products_mentioned: list | None = None
    last_message_at: datetime | None
    unread_count: int = 0
    latest_conversation_seq: int | None = None
    latest_conversation_revision: int | None = None
    created_at: datetime
    latest_action: dict | None = None
    crm_snapshot: ConversationCrmSnapshot | None = None
    next_best_action: ConversationNextBestActionSchema | None = None
    last_message_text: str | None = None
    contact_type: str | None = None
    has_pending_reply: bool = False
    latest_reply_confidence: float | None = None
    tail: ConversationTailProjectionSchema | None = None
    crm_stage: CrmStageProjection | None = None
    hydration: ConversationHydrationProjectionSchema | None = None

    model_config = ConfigDict(from_attributes=True)


class PaginatedConversationsResponse(BaseModel):
    items: list[ConversationResponse]
    next_cursor: str | None = None


class LiveChatResponse(BaseModel):
    """A chat entry from the live dialog list, enriched with DB state."""
    telegram_chat_id: int | None = None
    telegram_user_id: int | None = None
    channel: str = "telegram_dm"
    display_name: str
    phone: str | None = None
    unread_count: int = 0
    last_message_text: str = ""
    last_message_date: str | None = None
    last_message_is_outgoing: bool = False
    read_outbox_max_id: int = 0
    # DB-enriched fields (per D-04)
    contact_type: str | None = None       # from Customer.contact_type
    has_ai: bool = False                   # computed from override_mode + classification
    has_pending_reply: bool = False        # reserved for future Agent Action API
    conversation_id: int | None = None     # DB conversation ID if exists
    customer_id: int | None = None         # DB customer ID if exists


class LiveChatsResponse(BaseModel):
    """Response for GET /api/conversations/live"""
    chats: list[LiveChatResponse]
    count: int


class ConversationUpdate(BaseModel):
    """Update fields for a conversation. Used by PATCH /conversations/{id}.

    override_mode includes "off" per D-14 -- seller can disable AI per conversation.
    """
    pipeline_stage: str | None = None
    override_mode: Literal["auto", "force_draft", "off"] | None = None
    needs_attention: bool | None = None
    deal_value: float | None = None
