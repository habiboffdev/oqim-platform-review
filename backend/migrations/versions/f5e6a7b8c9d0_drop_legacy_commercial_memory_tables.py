"""drop legacy commercial memory tables

Revision ID: f5e6a7b8c9d0
Revises: f4d5e6a7b8c9
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f5e6a7b8c9d0"
down_revision = "f4d5e6a7b8c9"
branch_labels = None
depends_on = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS commercial_memory_projections CASCADE")
    op.execute("DROP TABLE IF EXISTS commercial_memory_facts CASCADE")
    op.execute("DROP TABLE IF EXISTS commercial_signals CASCADE")
    op.execute("DROP TABLE IF EXISTS commercial_input_events CASCADE")


def downgrade() -> None:
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "fact_id", name="uq_commercial_memory_facts_workspace_fact"),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_commercial_memory_facts_workspace_idempotency",
        ),
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "projection_ref",
            name="uq_commercial_memory_projections_workspace_ref",
        ),
    )
    op.create_index("ix_commercial_memory_projections_workspace_id", "commercial_memory_projections", ["workspace_id"])
    op.create_index(
        "ix_commercial_memory_projections_projection_type",
        "commercial_memory_projections",
        ["projection_type"],
    )
    op.create_index("ix_commercial_memory_projections_entity_type", "commercial_memory_projections", ["entity_type"])
    op.create_index("ix_commercial_memory_projections_entity_id", "commercial_memory_projections", ["entity_id"])
