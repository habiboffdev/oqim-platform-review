from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class CommercialEventRecord(Base):
    __tablename__ = "commercial_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "event_id",
            name="uq_commercial_events_workspace_event",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_commercial_events_workspace_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    raw_event: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class BusinessBrainFactRecord(Base):
    __tablename__ = "business_brain_facts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "fact_id",
            name="uq_business_brain_facts_workspace_fact",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_business_brain_facts_workspace_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fact_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    entity_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    risk_tier: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    index_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="skipped", server_default="skipped"
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    supersedes_fact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_fact: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class BusinessBrainUpdateRecord(Base):
    __tablename__ = "business_brain_updates"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "update_id",
            name="uq_business_brain_updates_workspace_update",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_business_brain_updates_workspace_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    update_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    target_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    proposed_value: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    approval_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    risk_tier: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    evidence_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_update: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class BusinessBrainProjectionRecord(Base):
    __tablename__ = "business_brain_projections"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "projection_ref",
            name="uq_business_brain_projections_workspace_ref",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    projection_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    projection_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    state: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    degraded_reasons: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    raw_projection: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class BusinessBrainIndexRecord(Base):
    __tablename__ = "business_brain_index_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "index_id",
            name="uq_business_brain_index_records_workspace_index",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_business_brain_index_records_workspace_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    index_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fact_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    unit_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    embedding_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_state: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        server_default="pending",
        nullable=False,
        index=True,
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(3072), nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    degraded_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_index: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class LLMGatewayTraceRecord(Base):
    __tablename__ = "llm_gateway_traces"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "trace_id",
            name="uq_llm_gateway_traces_workspace_trace",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    route_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    workflow_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    prompt_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_usage: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation_errors: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    raw_output_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_request: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    raw_response: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
