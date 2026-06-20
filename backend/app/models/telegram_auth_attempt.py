from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.workspace import Workspace


class TelegramAuthAttempt(Base):
    __tablename__ = "telegram_auth_attempts"
    __table_args__ = (
        UniqueConstraint("temp_session_id", name="uq_telegram_auth_attempt_temp_session"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id"), nullable=True, index=True
    )
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    temp_session_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    temp_session_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_code_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="requested", index=True)
    delivery_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    preferred_delivery_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    delivery_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delivery_degraded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_delivery_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_recovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_recovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_state: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    recovery_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_recovery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_step: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    workspace: Mapped[Workspace | None] = relationship()
