"""create phase1 commercial spine

Revision ID: b7e9c1d4a2f0
Revises: a4e7c9d2b601
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b7e9c1d4a2f0"
down_revision = "a4e7c9d2b601"
branch_labels = None
depends_on = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "commercial_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", _jsonb(), server_default="{}", nullable=False),
        sa.Column("raw_event", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "event_id", name="uq_commercial_events_workspace_event"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_commercial_events_workspace_idempotency"),
    )
    for column in ("workspace_id", "source_type", "source_ref", "actor_type", "correlation_id"):
        op.create_index(f"ix_commercial_events_{column}", "commercial_events", [column])

    op.create_table(
        "business_brain_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fact_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("fact_type", sa.String(length=120), nullable=False),
        sa.Column("entity_ref", sa.String(length=255), nullable=False),
        sa.Column("value", _jsonb(), server_default="{}", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("risk_tier", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("supersedes_fact_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("raw_fact", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "fact_id", name="uq_business_brain_facts_workspace_fact"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_business_brain_facts_workspace_idempotency"),
    )
    for column in ("workspace_id", "fact_type", "entity_ref", "status", "risk_tier"):
        op.create_index(f"ix_business_brain_facts_{column}", "business_brain_facts", [column])

    op.create_table(
        "business_brain_updates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("update_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("target_ref", sa.String(length=255), nullable=False),
        sa.Column("proposed_value", _jsonb(), server_default="{}", nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("approval_state", sa.String(length=32), nullable=False),
        sa.Column("risk_tier", sa.String(length=32), nullable=False),
        sa.Column("evidence_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_update", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "update_id", name="uq_business_brain_updates_workspace_update"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_business_brain_updates_workspace_idempotency"),
    )
    for column in ("workspace_id", "target_ref", "source", "approval_state", "risk_tier"):
        op.create_index(f"ix_business_brain_updates_{column}", "business_brain_updates", [column])

    op.create_table(
        "business_brain_projections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("projection_ref", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("projection_type", sa.String(length=80), nullable=False),
        sa.Column("entity_ref", sa.String(length=255), nullable=False),
        sa.Column("state", _jsonb(), server_default="{}", nullable=False),
        sa.Column("source_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("degraded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("degraded_reasons", _jsonb(), server_default="[]", nullable=False),
        sa.Column("raw_projection", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "projection_ref", name="uq_business_brain_projections_workspace_ref"),
    )
    for column in ("workspace_id", "projection_type", "entity_ref"):
        op.create_index(f"ix_business_brain_projections_{column}", "business_brain_projections", [column])

    op.create_table(
        "llm_gateway_traces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column("route_key", sa.String(length=120), nullable=False),
        sa.Column("workflow_name", sa.String(length=120), nullable=False),
        sa.Column("prompt_id", sa.String(length=255), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("source_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_used", sa.String(length=255), nullable=True),
        sa.Column("token_usage", _jsonb(), server_default="{}", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_estimate", sa.Float(), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("validation_errors", _jsonb(), server_default="[]", nullable=False),
        sa.Column("raw_output_ref", sa.Text(), nullable=True),
        sa.Column("raw_request", _jsonb(), server_default="{}", nullable=False),
        sa.Column("raw_response", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "trace_id", name="uq_llm_gateway_traces_workspace_trace"),
    )
    for column in ("workspace_id", "correlation_id", "route_key", "workflow_name", "prompt_id", "status"):
        op.create_index(f"ix_llm_gateway_traces_{column}", "llm_gateway_traces", [column])

    op.add_column(
        "commercial_action_proposals",
        sa.Column("lifecycle_state", sa.String(length=32), server_default="proposed", nullable=False),
    )
    op.add_column("commercial_action_proposals", sa.Column("correlation_id", sa.String(length=255), nullable=True))
    op.add_column("commercial_action_proposals", sa.Column("trace_id", sa.String(length=255), nullable=True))
    op.create_index("ix_commercial_action_proposals_lifecycle_state", "commercial_action_proposals", ["lifecycle_state"])
    op.create_index("ix_commercial_action_proposals_correlation_id", "commercial_action_proposals", ["correlation_id"])
    op.create_index("ix_commercial_action_proposals_trace_id", "commercial_action_proposals", ["trace_id"])

    op.add_column("commercial_decision_traces", sa.Column("correlation_id", sa.String(length=255), nullable=True))
    op.add_column("commercial_decision_traces", sa.Column("llm_trace_ids", _jsonb(), server_default="[]", nullable=False))
    op.create_index("ix_commercial_decision_traces_correlation_id", "commercial_decision_traces", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_commercial_decision_traces_correlation_id", table_name="commercial_decision_traces")
    op.drop_column("commercial_decision_traces", "llm_trace_ids")
    op.drop_column("commercial_decision_traces", "correlation_id")

    op.drop_index("ix_commercial_action_proposals_trace_id", table_name="commercial_action_proposals")
    op.drop_index("ix_commercial_action_proposals_correlation_id", table_name="commercial_action_proposals")
    op.drop_index("ix_commercial_action_proposals_lifecycle_state", table_name="commercial_action_proposals")
    op.drop_column("commercial_action_proposals", "trace_id")
    op.drop_column("commercial_action_proposals", "correlation_id")
    op.drop_column("commercial_action_proposals", "lifecycle_state")

    for column in ("status", "prompt_id", "workflow_name", "route_key", "correlation_id", "workspace_id"):
        op.drop_index(f"ix_llm_gateway_traces_{column}", table_name="llm_gateway_traces")
    op.drop_table("llm_gateway_traces")

    for column in ("entity_ref", "projection_type", "workspace_id"):
        op.drop_index(f"ix_business_brain_projections_{column}", table_name="business_brain_projections")
    op.drop_table("business_brain_projections")

    for column in ("risk_tier", "approval_state", "source", "target_ref", "workspace_id"):
        op.drop_index(f"ix_business_brain_updates_{column}", table_name="business_brain_updates")
    op.drop_table("business_brain_updates")

    for column in ("risk_tier", "status", "entity_ref", "fact_type", "workspace_id"):
        op.drop_index(f"ix_business_brain_facts_{column}", table_name="business_brain_facts")
    op.drop_table("business_brain_facts")

    for column in ("correlation_id", "actor_type", "source_ref", "source_type", "workspace_id"):
        op.drop_index(f"ix_commercial_events_{column}", table_name="commercial_events")
    op.drop_table("commercial_events")
