from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.models.workspace import Workspace


class ActionRuntime(Base):
    __tablename__ = "action_runtime"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "message_id",
            "action",
            name="uq_action_runtime_message_action",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    degraded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    workspace: Mapped[Workspace] = relationship()
    conversation: Mapped[Conversation] = relationship()
    message: Mapped[Message] = relationship()
