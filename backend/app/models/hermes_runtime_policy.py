from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class HermesAutopilotCircuitBreaker(Base):
    __tablename__ = "hermes_autopilot_circuit_breakers"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id", name="uq_hermes_autopilot_breaker_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    reason: Mapped[str] = mapped_column(String(120), nullable=False, default="operator_disabled")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
