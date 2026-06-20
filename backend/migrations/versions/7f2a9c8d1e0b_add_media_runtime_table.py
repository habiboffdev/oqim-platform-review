"""add media runtime table

Revision ID: 7f2a9c8d1e0b
Revises: 6d9f4e2a1b3c
Create Date: 2026-04-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f2a9c8d1e0b"
down_revision: Union[str, None] = "6d9f4e2a1b3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_runtime",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("media_type", sa.String(length=50), nullable=False),
        sa.Column("media_ref", sa.String(length=255), nullable=False),
        sa.Column("asset_state", sa.String(length=32), nullable=False),
        sa.Column("semantic_state", sa.String(length=32), nullable=False),
        sa.Column("hydration_status", sa.String(length=32), nullable=False),
        sa.Column("action_state", sa.String(length=32), nullable=False),
        sa.Column("ai_relevant", sa.Boolean(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("retry_after_seconds", sa.Float(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_media_runtime_message"),
        sa.UniqueConstraint("workspace_id", "media_ref", name="uq_media_runtime_workspace_ref"),
    )
    op.create_index("ix_media_runtime_workspace_id", "media_runtime", ["workspace_id"])
    op.create_index("ix_media_runtime_conversation_id", "media_runtime", ["conversation_id"])
    op.create_index("ix_media_runtime_action_state", "media_runtime", ["action_state"])
    op.create_index("ix_media_runtime_ai_relevant", "media_runtime", ["ai_relevant"])
    op.create_index("ix_media_runtime_next_attempt_at", "media_runtime", ["next_attempt_at"])
    op.create_index("ix_media_runtime_leased_until", "media_runtime", ["leased_until"])


def downgrade() -> None:
    op.drop_index("ix_media_runtime_leased_until", table_name="media_runtime")
    op.drop_index("ix_media_runtime_next_attempt_at", table_name="media_runtime")
    op.drop_index("ix_media_runtime_ai_relevant", table_name="media_runtime")
    op.drop_index("ix_media_runtime_action_state", table_name="media_runtime")
    op.drop_index("ix_media_runtime_conversation_id", table_name="media_runtime")
    op.drop_index("ix_media_runtime_workspace_id", table_name="media_runtime")
    op.drop_table("media_runtime")
