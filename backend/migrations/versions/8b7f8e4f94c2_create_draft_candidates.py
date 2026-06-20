"""create_draft_candidates

Revision ID: 8b7f8e4f94c2
Revises: c0fe20cf5a57
Create Date: 2026-04-04 11:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8b7f8e4f94c2"
down_revision: Union[str, None] = "c0fe20cf5a57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "draft_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="100"),
        sa.Column(
            "first_customer_message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id"),
            nullable=False,
        ),
        sa.Column(
            "latest_customer_message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id"),
            nullable=False,
        ),
        sa.Column(
            "latest_seller_message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id"),
            nullable=True,
        ),
        sa.Column(
            "last_ai_reply_id",
            sa.Integer(),
            sa.ForeignKey("ai_replies.id"),
            nullable=True,
        ),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="telegram_dm"),
        sa.Column("trigger_source", sa.String(length=24), nullable=False, server_default="customer_message"),
        sa.Column("open_reason", sa.String(length=32), nullable=True),
        sa.Column("suppressed_reason", sa.String(length=32), nullable=True),
        sa.Column("intent_hint", sa.String(length=32), nullable=True),
        sa.Column("urgency_hint", sa.String(length=16), nullable=True),
        sa.Column("language_hint", sa.String(length=16), nullable=True),
        sa.Column("contact_type_hint", sa.String(length=24), nullable=True),
        sa.Column("turn_message_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("has_media", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("latest_media_type", sa.String(length=50), nullable=True),
        sa.Column("first_customer_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_customer_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seller_replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index(
        "uq_draft_candidates_active_conversation",
        "draft_candidates",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('open', 'ready', 'leased', 'generating')"),
    )
    op.create_index(
        "ix_draft_candidates_ready_scan",
        "draft_candidates",
        ["state", "ready_at", "workspace_id", "priority", "id"],
        unique=False,
    )
    op.create_index(
        "ix_draft_candidates_workspace_state",
        "draft_candidates",
        ["workspace_id", "state", "ready_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_draft_candidates_lease_expiry",
        "draft_candidates",
        ["state", "lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("state IN ('leased', 'generating')"),
    )
    op.create_index(
        "ix_draft_candidates_latest_customer_message",
        "draft_candidates",
        ["latest_customer_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_draft_candidates_latest_customer_message", table_name="draft_candidates")
    op.drop_index("ix_draft_candidates_lease_expiry", table_name="draft_candidates")
    op.drop_index("ix_draft_candidates_workspace_state", table_name="draft_candidates")
    op.drop_index("ix_draft_candidates_ready_scan", table_name="draft_candidates")
    op.drop_index("uq_draft_candidates_active_conversation", table_name="draft_candidates")
    op.drop_table("draft_candidates")
