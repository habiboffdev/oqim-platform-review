"""add image_embedding and embedding_status to catalog_items

Revision ID: c7d8e9f0a1b2
Revises: b1c2d3e4f5a6
Create Date: 2026-03-25 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("catalog_items", sa.Column("image_embedding", Vector(768), nullable=True))
    op.add_column(
        "catalog_items",
        sa.Column("embedding_status", sa.String(20), server_default="pending", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("catalog_items", "embedding_status")
    op.drop_column("catalog_items", "image_embedding")
