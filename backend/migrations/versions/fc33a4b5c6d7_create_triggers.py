"""create triggers table

Revision ID: fc33a4b5c6d7
Revises: eb22f3c4a5b6
Create Date: 2026-05-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "fc33a4b5c6d7"
down_revision = "eb22f3c4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "triggers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_source", sa.String(length=64), nullable=False),
        sa.Column(
            "matching_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "permission_mode",
            sa.String(length=32),
            nullable=False,
            server_default="ask_always",
        ),
        sa.Column("action_proposal_type", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column(
            "retry_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_run_status", sa.String(length=40), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "audit_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_triggers_workspace_idempotency",
        ),
    )
    op.create_index("ix_triggers_workspace_id", "triggers", ["workspace_id"])
    op.create_index("ix_triggers_owner_agent_id", "triggers", ["owner_agent_id"])
    op.create_index(
        "ix_triggers_workspace_event", "triggers", ["workspace_id", "event_source"]
    )
    op.create_index(
        "ix_triggers_owner_agent",
        "triggers",
        ["workspace_id", "owner_agent_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_triggers_owner_agent", table_name="triggers")
    op.drop_index("ix_triggers_workspace_event", table_name="triggers")
    op.drop_index("ix_triggers_owner_agent_id", table_name="triggers")
    op.drop_index("ix_triggers_workspace_id", table_name="triggers")
    op.drop_table("triggers")
