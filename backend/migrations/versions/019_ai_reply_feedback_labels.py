"""Add AI reply feedback label fields for override taxonomy.

Revision ID: 019_ai_reply_feedback_labels
Revises: 018_conversation_override_mode
Create Date: 2026-02-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019_ai_reply_feedback_labels"
down_revision: Union[str, None] = "018_conversation_override_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_replies", sa.Column("override_reason", sa.String(length=32), nullable=True))
    op.add_column("ai_replies", sa.Column("override_note", sa.Text(), nullable=True))
    op.add_column("ai_replies", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_ai_replies_override_reason", "ai_replies", ["override_reason"])


def downgrade() -> None:
    op.drop_index("ix_ai_replies_override_reason", table_name="ai_replies")
    op.drop_column("ai_replies", "reviewed_at")
    op.drop_column("ai_replies", "override_note")
    op.drop_column("ai_replies", "override_reason")
