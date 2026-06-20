"""add conversation revision cursor

Revision ID: 8f1a2b3c4d5e
Revises: 7b6c5d4e3f21
Create Date: 2026-04-22 18:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8f1a2b3c4d5e"
down_revision = "7b6c5d4e3f21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("message_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE conversations
        SET message_revision = message_sequence
        """
    )


def downgrade() -> None:
    op.drop_column("conversations", "message_revision")
