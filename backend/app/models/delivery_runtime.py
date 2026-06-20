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


class DeliveryRuntime(Base):
    __tablename__ = "delivery_runtime"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "client_idempotency_key",
            name="uq_delivery_runtime_workspace_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True, index=True)
    action_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    channel_conversation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="requested", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    sending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unknown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    workspace: Mapped[Workspace] = relationship()
    conversation: Mapped[Conversation] = relationship()
    message: Mapped[Message | None] = relationship()
