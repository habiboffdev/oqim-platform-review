"""add text entities to messages

Revision ID: 6d9f4e2a1b3c
Revises: 9c4b6a7d8e1f
Create Date: 2026-04-23 15:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6d9f4e2a1b3c"
down_revision = "9c4b6a7d8e1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("text_entities", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "text_entities")
