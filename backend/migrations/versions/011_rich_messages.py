"""Add rich message fields for enhanced chat UI.

Revision ID: 011_rich_messages
Revises: 010_broadcast_media
Create Date: 2026-02-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "011_rich_messages"
down_revision: Union[str, None] = "010_broadcast_media"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("reply_to_msg_id", sa.BigInteger(), nullable=True))
    op.add_column("messages", sa.Column("forward_from_name", sa.String(200), nullable=True))
    op.add_column("messages", sa.Column("forward_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("messages", sa.Column("media_metadata", sa.JSON(), nullable=True))
    op.add_column("messages", sa.Column("reactions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "reactions")
    op.drop_column("messages", "media_metadata")
    op.drop_column("messages", "is_deleted")
    op.drop_column("messages", "edited_at")
    op.drop_column("messages", "forward_date")
    op.drop_column("messages", "forward_from_name")
    op.drop_column("messages", "reply_to_msg_id")
