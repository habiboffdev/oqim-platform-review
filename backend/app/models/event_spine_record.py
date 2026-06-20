from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class EventSpineRecord(Base):
    """Durable archive for canonical EventSpine events.

    Redis streams remain the live fan-out bus. This table is the replayable
    source that survives Redis loss, local restarts, and stream trimming.
    """

    __tablename__ = "event_spine_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "event_id",
            name="uq_event_spine_events_workspace_event",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_event_spine_events_workspace_idempotency",
        ),
        Index(
            "ix_event_spine_events_conversation",
            "workspace_id",
            "channel",
            "channel_conversation_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    stream_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    channel: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    channel_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_conversation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    channel_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    archive_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
