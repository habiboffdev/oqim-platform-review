"""Add rollout flags storage to draft runtime controls.

Revision ID: 2f7a6b1d0c9e
Revises: 9a2f4c7d1e88
Create Date: 2026-04-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2f7a6b1d0c9e"
down_revision: Union[str, None] = "9a2f4c7d1e88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "draft_workspace_runtime",
        sa.Column("rollout_flags", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("draft_workspace_runtime", "rollout_flags")
