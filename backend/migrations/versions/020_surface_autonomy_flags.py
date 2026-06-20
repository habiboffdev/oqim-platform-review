"""Add workspace-level per-surface autonomy rollback flags.

Revision ID: 020_surface_autonomy_flags
Revises: 019_ai_reply_feedback_labels
Create Date: 2026-02-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020_surface_autonomy_flags"
down_revision: Union[str, None] = "019_ai_reply_feedback_labels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("surface_autonomy_modes", sa.JSON(), nullable=True))
    op.add_column("workspaces", sa.Column("rollback_reason", sa.Text(), nullable=True))
    op.add_column("workspaces", sa.Column("rollback_triggered_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE workspaces
        SET surface_autonomy_modes = '{"dm":"auto","comment":"auto","outreach":"auto"}'
        WHERE surface_autonomy_modes IS NULL
        """
    )
    op.alter_column("workspaces", "surface_autonomy_modes", nullable=False)


def downgrade() -> None:
    op.drop_column("workspaces", "rollback_triggered_at")
    op.drop_column("workspaces", "rollback_reason")
    op.drop_column("workspaces", "surface_autonomy_modes")
