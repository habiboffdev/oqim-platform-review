"""Create broadcasts table.

Revision ID: 008_broadcasts
Revises: 007_catalog
Create Date: 2026-02-13 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "008_broadcasts"
down_revision: Union[str, None] = "007_catalog_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("engagement", sa.JSON(), server_default='{}', nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("broadcasts")
