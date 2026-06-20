from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.conversation import (
    ConversationHydrationProjectionSchema,
    ConversationTailProjectionSchema,
)
from app.schemas.delivery import DeliveryRuntimeProjection


class MessageTextEntity(BaseModel):
    type: str
    offset: int
    length: int
    document_id: str | None = None


class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    channel: str = "dm"
    sender_type: str
    content: str
    media_type: str | None = None
    media_url: str | None = None
    media_preview_url: str | None = None
    media_full_url: str | None = None
    media_runtime: dict | None = None
    delivery_runtime: DeliveryRuntimeProjection | None = None
    telegram_message_id: int | None = None
    is_read: bool
    created_at: datetime

    # Rich message fields
    reply_to_msg_id: int | None = None
    forward_from_name: str | None = None
    forward_date: datetime | None = None
    edited_at: datetime | None = None
    is_deleted: bool = False
    media_metadata: dict | None = None
    text_entities: list[MessageTextEntity] | None = None
    reactions: list[dict] | None = None
    external_message_id: str | None = None
    external_author_id: str | None = None
    external_parent_id: str | None = None
    client_message_uuid: str | None = None
    delivery_state: str = "confirmed"
    conversation_seq: int | None = None
    grouped_id: int | None = None
    telegram_timestamp: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class HistoryGapResponse(BaseModel):
    reason: str
    before_external_message_id: str | None = None
    after_external_message_id: str | None = None


class PaginatedMessagesResponse(BaseModel):
    items: list[MessageResponse]
    has_older: bool
    latest_conversation_seq: int | None = None
    latest_conversation_revision: int | None = None
    history_gap: HistoryGapResponse | None = None
    tail: ConversationTailProjectionSchema | None = None
    hydration: ConversationHydrationProjectionSchema | None = None
