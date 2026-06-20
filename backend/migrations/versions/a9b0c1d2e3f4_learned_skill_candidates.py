"""create learned_skill_candidates table

Revision ID: a9b0c1d2e3f4
Revises: bf909ae29aa5
Create Date: 2026-05-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a9b0c1d2e3f4"
down_revision = "bf909ae29aa5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learned_skill_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False, server_default=""),
        sa.Column("action", sa.Text(), nullable=False, server_default=""),
        sa.Column("example_phrase", sa.Text(), nullable=False, server_default=""),
        sa.Column("dimension", sa.String(length=60), nullable=False, server_default="general"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_conv_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="learned"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_learned_skill_candidates_workspace_slug"),
    )
    op.create_index("ix_learned_skill_candidates_workspace_id", "learned_skill_candidates", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_learned_skill_candidates_workspace_id", table_name="learned_skill_candidates")
    op.drop_table("learned_skill_candidates")
