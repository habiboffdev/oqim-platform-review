"""add learning runtime state

Revision ID: 7e4a91c2d8b0
Revises: 1f6b8a2d9c40
Create Date: 2026-05-02
"""

import sqlalchemy as sa
from alembic import op

revision = "7e4a91c2d8b0"
down_revision = "1f6b8a2d9c40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learning_signals",
        sa.Column("indexing_status", sa.String(length=24), server_default="pending", nullable=False),
    )
    op.add_column(
        "draft_actions",
        sa.Column("learning_state", sa.String(length=24), server_default="not_applicable", nullable=False),
    )
    op.add_column("draft_actions", sa.Column("learning_signal_id", sa.Integer(), nullable=True))
    op.add_column("draft_actions", sa.Column("learning_error", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_draft_actions_learning_signal_id",
        "draft_actions",
        "learning_signals",
        ["learning_signal_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_draft_actions_learning_signal_id", "draft_actions", type_="foreignkey")
    op.drop_column("draft_actions", "learning_error")
    op.drop_column("draft_actions", "learning_signal_id")
    op.drop_column("draft_actions", "learning_state")
    op.drop_column("learning_signals", "indexing_status")
