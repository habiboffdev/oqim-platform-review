"""add commercial action executions

Revision ID: a4e7c9d2b601
Revises: f9a1b2c3d4e5
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a4e7c9d2b601"
down_revision = "f9a1b2c3d4e5"
branch_labels = None
depends_on = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "commercial_action_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.String(length=255), nullable=False),
        sa.Column("action_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("delivery_state", sa.String(length=32), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payload", _jsonb(), server_default="{}", nullable=False),
        sa.Column("raw_result", _jsonb(), server_default="{}", nullable=False),
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
            "execution_id",
            name="uq_commercial_action_executions_workspace_execution",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_commercial_action_executions_workspace_idempotency",
        ),
    )
    op.create_index(
        "ix_commercial_action_executions_workspace_id",
        "commercial_action_executions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_commercial_action_executions_conversation_id",
        "commercial_action_executions",
        ["conversation_id"],
    )
    op.create_index(
        "ix_commercial_action_executions_customer_id",
        "commercial_action_executions",
        ["customer_id"],
    )
    op.create_index(
        "ix_commercial_action_executions_proposal_id",
        "commercial_action_executions",
        ["proposal_id"],
    )
    op.create_index(
        "ix_commercial_action_executions_action_type",
        "commercial_action_executions",
        ["action_type"],
    )
    op.create_index(
        "ix_commercial_action_executions_status",
        "commercial_action_executions",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_commercial_action_executions_status",
        table_name="commercial_action_executions",
    )
    op.drop_index(
        "ix_commercial_action_executions_action_type",
        table_name="commercial_action_executions",
    )
    op.drop_index(
        "ix_commercial_action_executions_proposal_id",
        table_name="commercial_action_executions",
    )
    op.drop_index(
        "ix_commercial_action_executions_customer_id",
        table_name="commercial_action_executions",
    )
    op.drop_index(
        "ix_commercial_action_executions_conversation_id",
        table_name="commercial_action_executions",
    )
    op.drop_index(
        "ix_commercial_action_executions_workspace_id",
        table_name="commercial_action_executions",
    )
    op.drop_table("commercial_action_executions")
