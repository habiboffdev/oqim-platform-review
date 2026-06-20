"""create tool_grants table

Revision ID: eb22f3c4a5b6
Revises: da11e2f3a4b5
Create Date: 2026-05-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "eb22f3c4a5b6"
down_revision = "da11e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column(
            "granted_by", sa.String(length=64), nullable=False, server_default="owner"
        ),
        sa.Column("grant_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "audit_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "workspace_id",
            "agent_id",
            "scope",
            name="uq_tool_grants_workspace_agent_scope_active",
        ),
    )
    op.create_index("ix_tool_grants_workspace_id", "tool_grants", ["workspace_id"])
    op.create_index("ix_tool_grants_agent_id", "tool_grants", ["agent_id"])
    op.create_index(
        "ix_tool_grants_workspace_scope",
        "tool_grants",
        ["workspace_id", "scope"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_grants_workspace_scope", table_name="tool_grants")
    op.drop_index("ix_tool_grants_agent_id", table_name="tool_grants")
    op.drop_index("ix_tool_grants_workspace_id", table_name="tool_grants")
    op.drop_table("tool_grants")
