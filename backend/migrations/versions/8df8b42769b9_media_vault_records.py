"""media_vault_records

Reusable per-workspace media assets — the owner's media vault (spike #439).
The owner curates an intro video / photo / document once and refers to it by a
stable handle; the seller sends it via talk.send_media. Telegram-cloud pointer
columns (vault_peer/vault_message_id/document_id/access_hash/file_reference) are
nullable until the sidecar vault.store/vault.send path lands.

Revision ID: 8df8b42769b9
Revises: f2a3b4c5d6e7
Create Date: 2026-06-17 05:20:49.616088
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8df8b42769b9'
down_revision: str | None = 'f2a3b4c5d6e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_vault_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("handle", sa.String(length=120), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("cdn_url", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("vault_peer", sa.String(length=255), nullable=True),
        sa.Column("vault_message_id", sa.BigInteger(), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("access_hash", sa.BigInteger(), nullable=True),
        sa.Column("file_reference", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "workspace_id", "handle", name="uq_media_vault_workspace_handle"
        ),
    )
    op.create_index(
        "ix_media_vault_records_workspace_id",
        "media_vault_records",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_media_vault_records_workspace_id", table_name="media_vault_records"
    )
    op.drop_table("media_vault_records")
