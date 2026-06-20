"""add embedding_status to business_knowledge

Revision ID: 1f6b8a2d9c40
Revises: 8c4d2f19b7a6
Create Date: 2026-05-02
"""

import sqlalchemy as sa
from alembic import op

revision = "1f6b8a2d9c40"
down_revision = "8c4d2f19b7a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "business_knowledge",
        sa.Column("embedding_status", sa.String(length=20), server_default="pending", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("business_knowledge", "embedding_status")
