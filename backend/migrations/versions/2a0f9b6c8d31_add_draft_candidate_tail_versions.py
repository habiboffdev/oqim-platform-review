"""add draft candidate tail versions

Revision ID: 2a0f9b6c8d31
Revises: 1c8b4e7a2d90
Create Date: 2026-04-26 01:45:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "2a0f9b6c8d31"
down_revision = "1c8b4e7a2d90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "draft_candidates",
        sa.Column("tail_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "draft_candidates",
        sa.Column("generation_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "draft_candidates",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE draft_candidates AS dc
        SET tail_version = COALESCE(c.message_revision, c.message_sequence, 0)
        FROM conversations AS c
        WHERE dc.conversation_id = c.id
        """
    )
    op.create_index(
        "ix_draft_candidates_tail_version",
        "draft_candidates",
        ["conversation_id", "tail_version", "generation_version"],
    )
    op.create_index(
        "uq_draft_candidates_active_tail",
        "draft_candidates",
        ["conversation_id", "tail_version"],
        unique=True,
        postgresql_where=sa.text("state IN ('open', 'ready', 'leased', 'generating')"),
    )


def downgrade() -> None:
    op.drop_index("uq_draft_candidates_active_tail", table_name="draft_candidates")
    op.drop_index("ix_draft_candidates_tail_version", table_name="draft_candidates")
    op.drop_column("draft_candidates", "superseded_at")
    op.drop_column("draft_candidates", "generation_version")
    op.drop_column("draft_candidates", "tail_version")
