"""Create tasks table.

Revision ID: 009_tasks
Revises: 008_broadcasts
Create Date: 2026-02-13 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "009_tasks"
down_revision: Union[str, None] = "008_broadcasts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("priority", sa.String(10), server_default="medium", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tasks")
