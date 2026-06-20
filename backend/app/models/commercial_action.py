from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class CommercialActionProposalRecord(Base):
    __tablename__ = "commercial_action_proposals"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "proposal_id",
            name="uq_commercial_action_proposals_workspace_proposal",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_commercial_action_proposals_workspace_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="proposed", nullable=False, index=True)
    execution_mode: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    executor_runtime: Mapped[str | None] = mapped_column(String(120), nullable=True)
    priority: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    raw_proposal: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class CommercialActionExecutionRecord(Base):
    __tablename__ = "commercial_action_executions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "execution_id",
            name="uq_commercial_action_executions_workspace_execution",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_commercial_action_executions_workspace_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    proposal_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    delivery_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    raw_result: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class CommercialDecisionTraceRecord(Base):
    __tablename__ = "commercial_decision_traces"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "trace_id",
            name="uq_commercial_decision_traces_workspace_trace",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    accepted_signal_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    rejected_signal_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    changed_fact_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    changed_projection_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    emitted_proposal_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    llm_trace_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    degraded_reasons: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    raw_trace: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
