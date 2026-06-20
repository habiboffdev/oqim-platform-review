"""Add read_outbox_max_id to conversations for read receipt tracking.

Revision ID: 012_read_receipts
Revises: 011_rich_messages
Create Date: 2026-02-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "012_read_receipts"
down_revision: Union[str, None] = "011_rich_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("read_outbox_max_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "read_outbox_max_id")
