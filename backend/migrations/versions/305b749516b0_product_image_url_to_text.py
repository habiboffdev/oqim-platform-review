"""product_image_url_to_text

Revision ID: 305b749516b0
Revises: 003_knowledge
Create Date: 2026-02-07 12:18:39.447149
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '305b749516b0'
down_revision: Union[str, None] = '003_knowledge'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('product_images', 'url',
               existing_type=sa.VARCHAR(length=500),
               type_=sa.Text(),
               existing_nullable=False)


def downgrade() -> None:
    op.alter_column('product_images', 'url',
               existing_type=sa.Text(),
               type_=sa.VARCHAR(length=500),
               existing_nullable=False)
