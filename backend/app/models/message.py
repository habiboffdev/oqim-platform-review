from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class SenderType(str, enum.Enum):
    SELLER = "seller"
    CUSTOMER = "customer"
    AI = "ai"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), default="dm", server_default="dm", nullable=False)
    sender_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    telegram_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Rich message fields
    reply_to_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    forward_from_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    forward_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    media_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    text_entities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reactions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_author_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_parent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_message_uuid: Mapped[str | None] = mapped_column(String(120), nullable=True)
    delivery_state: Mapped[str] = mapped_column(
        String(20),
        default="confirmed",
        server_default="confirmed",
        nullable=False,
    )
    conversation_seq: Mapped[int | None] = mapped_column(nullable=True)

    # Multimodal semantic extraction results
    transcription: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcription_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    media_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    grouped_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Relationships
    conversation: Mapped[Conversation] = relationship(back_populates="messages")
