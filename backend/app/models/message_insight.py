from __future__ import annotations
from datetime import datetime
from sqlalchemy import ARRAY, BigInteger, Boolean, DateTime, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, utc_now


class MessageInsight(Base):
    __tablename__ = "message_insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    products_mentioned: Mapped[list] = mapped_column(JSONB, default=list)
    budget_signal: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivery_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    objections: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    contact_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    lead_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    urgency: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
