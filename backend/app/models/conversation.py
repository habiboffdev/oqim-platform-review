from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.workspace import Workspace
    from app.models.customer import Customer
    from app.models.message import Message


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "telegram_chat_id", name="uq_conversation_workspace_chat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), default="dm", server_default="dm", nullable=False)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    external_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pipeline_stage: Mapped[str] = mapped_column(
        String(20), default="new"
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    override_mode: Mapped[str] = mapped_column(
        String(20), default="auto", server_default="auto", nullable=False
    )
    needs_attention: Mapped[bool] = mapped_column(default=False)
    crm_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    read_outbox_max_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deal_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    products_mentioned: Mapped[list | None] = mapped_column(JSONB, default=list)
    message_sequence: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    message_revision: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Relationships
    workspace: Mapped[Workspace] = relationship(back_populates="conversations")
    customer: Mapped[Customer] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", order_by="Message.created_at"
    )
