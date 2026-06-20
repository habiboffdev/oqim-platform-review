"""drop legacy broadcasts table

Revision ID: f3c4d5e6a7b8
Revises: f2b3c4d5e6a7
Create Date: 2026-05-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "f3c4d5e6a7b8"
down_revision = "f2b3c4d5e6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS broadcasts CASCADE")


def downgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("media_url", sa.String(length=500), nullable=True),
        sa.Column("channel_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("engagement", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
