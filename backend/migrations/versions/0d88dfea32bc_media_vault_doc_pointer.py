"""media_vault_doc_pointer

Document-anchored media vault assets: an asset may carry NO cdn_url and instead
point at a Telegram message via (vault_peer, vault_message_id). So cdn_url becomes
nullable and a CHECK constraint requires at least one addressing mode.

Revision ID: 0d88dfea32bc
Revises: 829e9c305925
Create Date: 2026-06-18 05:20:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0d88dfea32bc'
down_revision: str | None = '829e9c305925'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "media_vault_records",
        "cdn_url",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_media_vault_url_or_pointer",
        "media_vault_records",
        "cdn_url IS NOT NULL OR vault_message_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_media_vault_url_or_pointer", "media_vault_records", type_="check"
    )
    op.alter_column(
        "media_vault_records",
        "cdn_url",
        existing_type=sa.Text(),
        nullable=False,
    )
