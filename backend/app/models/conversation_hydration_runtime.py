from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.workspace import Workspace


class ConversationHydrationRuntime(Base):
    __tablename__ = "conversation_hydration_runtime"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "conversation_id",
            name="uq_conversation_hydration_runtime_workspace_conversation",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="idle", index=True)
    reason: Mapped[str] = mapped_column(String(80), nullable=False, default="chat_open")
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    persisted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    workspace: Mapped[Workspace] = relationship()
    conversation: Mapped[Conversation] = relationship()
