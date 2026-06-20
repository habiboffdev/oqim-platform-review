"""create commercial memory tables

Revision ID: e8b2c3d4f5a6
Revises: d6e7f8a9b0c1
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e8b2c3d4f5a6"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "commercial_input_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("source_message_ids", _jsonb(), server_default="[]", nullable=False),
        sa.Column("source_media_ids", _jsonb(), server_default="[]", nullable=False),
        sa.Column("payload", _jsonb(), server_default="{}", nullable=False),
        sa.Column("raw_event", _jsonb(), server_default="{}", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_commercial_input_events_workspace_idempotency",
        ),
    )
    op.create_index("ix_commercial_input_events_workspace_id", "commercial_input_events", ["workspace_id"])
    op.create_index("ix_commercial_input_events_conversation_id", "commercial_input_events", ["conversation_id"])
    op.create_index("ix_commercial_input_events_customer_id", "commercial_input_events", ["customer_id"])
    op.create_index("ix_commercial_input_events_trigger", "commercial_input_events", ["trigger"])
    op.create_index("ix_commercial_input_events_correlation_id", "commercial_input_events", ["correlation_id"])

    op.create_table(
        "commercial_signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("signal_type", sa.String(length=120), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("source_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("extracted_fields", _jsonb(), server_default="{}", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("trustedness", sa.String(length=32), nullable=False),
        sa.Column("requires_review", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=True),
        sa.Column("raw_signal", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "signal_id", name="uq_commercial_signals_workspace_signal"),
    )
    op.create_index("ix_commercial_signals_workspace_id", "commercial_signals", ["workspace_id"])
    op.create_index("ix_commercial_signals_conversation_id", "commercial_signals", ["conversation_id"])
    op.create_index("ix_commercial_signals_customer_id", "commercial_signals", ["customer_id"])
    op.create_index("ix_commercial_signals_signal_type", "commercial_signals", ["signal_type"])
    op.create_index("ix_commercial_signals_trustedness", "commercial_signals", ["trustedness"])

    op.create_table(
        "commercial_memory_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fact_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("fact_type", sa.String(length=120), nullable=False),
        sa.Column("value", _jsonb(), server_default="{}", nullable=False),
        sa.Column("source_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("trustedness", sa.String(length=32), nullable=False),
        sa.Column("lifecycle", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_fact_ids", _jsonb(), server_default="[]", nullable=False),
        sa.Column("raw_fact", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "fact_id", name="uq_commercial_memory_facts_workspace_fact"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_commercial_memory_facts_workspace_idempotency"),
    )
    op.create_index("ix_commercial_memory_facts_workspace_id", "commercial_memory_facts", ["workspace_id"])
    op.create_index("ix_commercial_memory_facts_entity_type", "commercial_memory_facts", ["entity_type"])
    op.create_index("ix_commercial_memory_facts_entity_id", "commercial_memory_facts", ["entity_id"])
    op.create_index("ix_commercial_memory_facts_fact_type", "commercial_memory_facts", ["fact_type"])
    op.create_index("ix_commercial_memory_facts_trustedness", "commercial_memory_facts", ["trustedness"])
    op.create_index("ix_commercial_memory_facts_lifecycle", "commercial_memory_facts", ["lifecycle"])

    op.create_table(
        "commercial_memory_projections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("projection_ref", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("projection_type", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("source_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("state", _jsonb(), server_default="{}", nullable=False),
        sa.Column("degraded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("degradation_reasons", _jsonb(), server_default="[]", nullable=False),
        sa.Column("raw_projection", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "projection_ref", name="uq_commercial_memory_projections_workspace_ref"),
    )
    op.create_index("ix_commercial_memory_projections_workspace_id", "commercial_memory_projections", ["workspace_id"])
    op.create_index("ix_commercial_memory_projections_projection_type", "commercial_memory_projections", ["projection_type"])
    op.create_index("ix_commercial_memory_projections_entity_type", "commercial_memory_projections", ["entity_type"])
    op.create_index("ix_commercial_memory_projections_entity_id", "commercial_memory_projections", ["entity_id"])

    op.create_table(
        "commercial_action_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=120), nullable=False),
        sa.Column("execution_mode", sa.String(length=80), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("executor_runtime", sa.String(length=120), nullable=True),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("source_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("payload", _jsonb(), server_default="{}", nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("raw_proposal", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "proposal_id", name="uq_commercial_action_proposals_workspace_proposal"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_commercial_action_proposals_workspace_idempotency"),
    )
    op.create_index("ix_commercial_action_proposals_workspace_id", "commercial_action_proposals", ["workspace_id"])
    op.create_index("ix_commercial_action_proposals_conversation_id", "commercial_action_proposals", ["conversation_id"])
    op.create_index("ix_commercial_action_proposals_customer_id", "commercial_action_proposals", ["customer_id"])
    op.create_index("ix_commercial_action_proposals_action_type", "commercial_action_proposals", ["action_type"])
    op.create_index("ix_commercial_action_proposals_execution_mode", "commercial_action_proposals", ["execution_mode"])
    op.create_index("ix_commercial_action_proposals_risk_level", "commercial_action_proposals", ["risk_level"])
    op.create_index("ix_commercial_action_proposals_priority", "commercial_action_proposals", ["priority"])

    op.create_table(
        "commercial_decision_traces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("accepted_signal_ids", _jsonb(), server_default="[]", nullable=False),
        sa.Column("rejected_signal_ids", _jsonb(), server_default="[]", nullable=False),
        sa.Column("changed_fact_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("changed_projection_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("emitted_proposal_refs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("degraded_reasons", _jsonb(), server_default="[]", nullable=False),
        sa.Column("raw_trace", _jsonb(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "trace_id", name="uq_commercial_decision_traces_workspace_trace"),
    )
    op.create_index("ix_commercial_decision_traces_workspace_id", "commercial_decision_traces", ["workspace_id"])
    op.create_index("ix_commercial_decision_traces_conversation_id", "commercial_decision_traces", ["conversation_id"])
    op.create_index("ix_commercial_decision_traces_customer_id", "commercial_decision_traces", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_commercial_decision_traces_customer_id", table_name="commercial_decision_traces")
    op.drop_index("ix_commercial_decision_traces_conversation_id", table_name="commercial_decision_traces")
    op.drop_index("ix_commercial_decision_traces_workspace_id", table_name="commercial_decision_traces")
    op.drop_table("commercial_decision_traces")

    op.drop_index("ix_commercial_action_proposals_priority", table_name="commercial_action_proposals")
    op.drop_index("ix_commercial_action_proposals_risk_level", table_name="commercial_action_proposals")
    op.drop_index("ix_commercial_action_proposals_execution_mode", table_name="commercial_action_proposals")
    op.drop_index("ix_commercial_action_proposals_action_type", table_name="commercial_action_proposals")
    op.drop_index("ix_commercial_action_proposals_customer_id", table_name="commercial_action_proposals")
    op.drop_index("ix_commercial_action_proposals_conversation_id", table_name="commercial_action_proposals")
    op.drop_index("ix_commercial_action_proposals_workspace_id", table_name="commercial_action_proposals")
    op.drop_table("commercial_action_proposals")

    op.drop_index("ix_commercial_memory_projections_entity_id", table_name="commercial_memory_projections")
    op.drop_index("ix_commercial_memory_projections_entity_type", table_name="commercial_memory_projections")
    op.drop_index("ix_commercial_memory_projections_projection_type", table_name="commercial_memory_projections")
    op.drop_index("ix_commercial_memory_projections_workspace_id", table_name="commercial_memory_projections")
    op.drop_table("commercial_memory_projections")

    op.drop_index("ix_commercial_memory_facts_lifecycle", table_name="commercial_memory_facts")
    op.drop_index("ix_commercial_memory_facts_trustedness", table_name="commercial_memory_facts")
    op.drop_index("ix_commercial_memory_facts_fact_type", table_name="commercial_memory_facts")
    op.drop_index("ix_commercial_memory_facts_entity_id", table_name="commercial_memory_facts")
    op.drop_index("ix_commercial_memory_facts_entity_type", table_name="commercial_memory_facts")
    op.drop_index("ix_commercial_memory_facts_workspace_id", table_name="commercial_memory_facts")
    op.drop_table("commercial_memory_facts")

    op.drop_index("ix_commercial_signals_trustedness", table_name="commercial_signals")
    op.drop_index("ix_commercial_signals_signal_type", table_name="commercial_signals")
    op.drop_index("ix_commercial_signals_customer_id", table_name="commercial_signals")
    op.drop_index("ix_commercial_signals_conversation_id", table_name="commercial_signals")
    op.drop_index("ix_commercial_signals_workspace_id", table_name="commercial_signals")
    op.drop_table("commercial_signals")

    op.drop_index("ix_commercial_input_events_correlation_id", table_name="commercial_input_events")
    op.drop_index("ix_commercial_input_events_trigger", table_name="commercial_input_events")
    op.drop_index("ix_commercial_input_events_customer_id", table_name="commercial_input_events")
    op.drop_index("ix_commercial_input_events_conversation_id", table_name="commercial_input_events")
    op.drop_index("ix_commercial_input_events_workspace_id", table_name="commercial_input_events")
    op.drop_table("commercial_input_events")
