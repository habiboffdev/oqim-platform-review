"""widen message client message uuid

Revision ID: c5d8e9a1f2b3
Revises: 7e4a91c2d8b0
Create Date: 2026-05-03
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c5d8e9a1f2b3"
down_revision = "7e4a91c2d8b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "messages",
        "client_message_uuid",
        existing_type=sa.String(length=36),
        type_=sa.String(length=120),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "messages",
        "client_message_uuid",
        existing_type=sa.String(length=120),
        type_=sa.String(length=36),
        existing_nullable=True,
    )
