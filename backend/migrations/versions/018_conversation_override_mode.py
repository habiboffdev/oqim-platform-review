"""Add per-conversation override mode for force-draft control.

Revision ID: 018_conversation_override_mode
Revises: 017_metric_snapshots
Create Date: 2026-02-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018_conversation_override_mode"
down_revision: Union[str, None] = "017_metric_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "override_mode",
            sa.String(length=20),
            nullable=False,
            server_default="auto",
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "override_mode")
