from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.workspace import Workspace
    from app.models.conversation import Conversation


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "telegram_id", name="uq_customer_workspace_telegram"),
        Index("uq_customer_workspace_external", "workspace_id", "external_id", "channel",
              unique=True, postgresql_where="external_id IS NOT NULL"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="telegram_dm", server_default="telegram_dm")
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Telegram @username (without @) — powers the owner-card t.me jump link;
    # tg://user?id mentions are stripped for users who never met the bot.
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contact_type: Mapped[str] = mapped_column(String(20), default="customer")  # customer, supplier, personal, work, group
    classification_confidence: Mapped[float | None] = mapped_column(nullable=True)
    classification_corrected: Mapped[bool] = mapped_column(default=False)
    language: Mapped[str] = mapped_column(String(10), default="uz")
    tags: Mapped[list | None] = mapped_column(JSON, default=list)
    lifetime_value: Mapped[float] = mapped_column(default=0.0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_muted: Mapped[bool] = mapped_column(default=False)
    opted_out: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Relationships
    workspace: Mapped[Workspace] = relationship(back_populates="customers")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="customer")
