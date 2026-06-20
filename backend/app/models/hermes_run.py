"""Durable Hermes run records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class HermesRun(Base):
    """Durable audit record for a single Hermes runtime execution."""

    __tablename__ = "hermes_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_hermes_runs_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    tenant_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    agent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    agent_kind: Mapped[str] = mapped_column(String(80), nullable=False, default="agent")
    lane: Mapped[str] = mapped_column(String(40), nullable=False, default="fast_interactive", index=True)
    run_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="reply", index=True)
    trigger_type: Mapped[str] = mapped_column(String(80), nullable=False, default="manual", index=True)
    trigger_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    conversation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    runtime_profile_snapshot_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    runtime_profile_cache_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    engine_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    warnings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_errors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_action: Mapped[str | None] = mapped_column(String(120), nullable=True)
    output_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    input_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class HermesRunEvent(Base):
    """Ordered event log for a single Hermes run."""

    __tablename__ = "hermes_run_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "event_id", name="uq_hermes_run_events_workspace_event"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hermes_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("hermes_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="internal")
    owner_label: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    owner_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action_proposal_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
