"""create event spine durable archive

Revision ID: c8f0d1e2a3b4
Revises: b7e9c1d4a2f0
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c8f0d1e2a3b4"
down_revision = "b7e9c1d4a2f0"
branch_labels = None
depends_on = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "event_spine_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.String(length=120), nullable=True),
        sa.Column("channel", sa.String(length=80), nullable=False),
        sa.Column("channel_account_id", sa.String(length=255), nullable=True),
        sa.Column("channel_conversation_id", sa.String(length=255), nullable=True),
        sa.Column("channel_message_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("causation_id", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", _jsonb(), server_default="{}", nullable=False),
        sa.Column("archive_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "event_id", name="uq_event_spine_events_workspace_event"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_event_spine_events_workspace_idempotency"),
    )
    op.create_index("ix_event_spine_events_workspace_id", "event_spine_events", ["workspace_id"])
    op.create_index("ix_event_spine_events_event_type", "event_spine_events", ["event_type"])
    op.create_index("ix_event_spine_events_channel", "event_spine_events", ["channel"])
    op.create_index("ix_event_spine_events_channel_conversation_id", "event_spine_events", ["channel_conversation_id"])
    op.create_index("ix_event_spine_events_correlation_id", "event_spine_events", ["correlation_id"])
    op.create_index(
        "ix_event_spine_events_conversation",
        "event_spine_events",
        ["workspace_id", "channel", "channel_conversation_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_spine_events_conversation", table_name="event_spine_events")
    op.drop_index("ix_event_spine_events_correlation_id", table_name="event_spine_events")
    op.drop_index("ix_event_spine_events_channel_conversation_id", table_name="event_spine_events")
    op.drop_index("ix_event_spine_events_channel", table_name="event_spine_events")
    op.drop_index("ix_event_spine_events_event_type", table_name="event_spine_events")
    op.drop_index("ix_event_spine_events_workspace_id", table_name="event_spine_events")
    op.drop_table("event_spine_events")
