from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.models.workspace import Workspace


class MediaRuntime(Base):
    __tablename__ = "media_runtime"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_media_runtime_message"),
        UniqueConstraint("workspace_id", "media_ref", name="uq_media_runtime_workspace_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), nullable=False, index=True
    )
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    media_type: Mapped[str] = mapped_column(String(50), nullable=False)
    media_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_state: Mapped[str] = mapped_column(String(32), nullable=False, default="metadata_only")
    semantic_state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    hydration_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    action_state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    ai_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    retry_after_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    commercial_semantics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    workspace: Mapped[Workspace] = relationship()
    conversation: Mapped[Conversation] = relationship()
    message: Mapped[Message] = relationship()
