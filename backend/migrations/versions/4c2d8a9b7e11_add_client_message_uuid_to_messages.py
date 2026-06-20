"""add client_message_uuid to messages

Revision ID: 4c2d8a9b7e11
Revises: 2f7a6b1d0c9e
Create Date: 2026-04-22 14:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4c2d8a9b7e11"
down_revision = "2f7a6b1d0c9e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("client_message_uuid", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "client_message_uuid")
