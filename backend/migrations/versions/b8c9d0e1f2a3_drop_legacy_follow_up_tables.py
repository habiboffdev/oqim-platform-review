"""drop legacy follow-up obligation tables

Revision ID: b8c9d0e1f2a3
Revises: 4d1c2b3a9e87, a6b7c8d9e0f1
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b8c9d0e1f2a3"
down_revision: str | tuple[str, str] | None = ("4d1c2b3a9e87", "a6b7c8d9e0f1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_task_active_follow_up")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS fk_tasks_follow_up_obligation_id")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS follow_up_obligation_id")
    op.execute("DROP TABLE IF EXISTS follow_up_events CASCADE")
    op.execute("DROP TABLE IF EXISTS follow_up_obligations CASCADE")


def downgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "follow_up_obligations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("root_message_id", sa.Integer(), nullable=True),
        sa.Column("root_ai_reply_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("waiting_for", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("suggested_message", sa.Text(), nullable=True),
        sa.Column("response_expected", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_customer_message_id", sa.Integer(), nullable=True),
        sa.Column("last_seller_message_id", sa.Integer(), nullable=True),
        sa.Column("resolution_reason", sa.String(length=64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", jsonb, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["root_ai_reply_id"], ["ai_replies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["root_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_follow_up_obligations_due", "follow_up_obligations", ["workspace_id", "state", "due_at"])
    op.create_index("idx_follow_up_obligations_conversation", "follow_up_obligations", ["conversation_id", "state"])
    op.create_index("idx_follow_up_obligations_customer", "follow_up_obligations", ["customer_id", "state"])
    op.create_index(
        "uq_follow_up_active_kind",
        "follow_up_obligations",
        ["workspace_id", "conversation_id", "kind"],
        unique=True,
        postgresql_where=sa.text("state IN ('pending', 'due', 'snoozed')"),
    )
    op.create_table(
        "follow_up_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("obligation_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("ai_reply_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("payload", jsonb, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["ai_reply_id"], ["ai_replies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["obligation_id"], ["follow_up_obligations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_follow_up_events_conversation", "follow_up_events", ["conversation_id", "created_at"])
    op.create_index("idx_follow_up_events_obligation", "follow_up_events", ["obligation_id", "created_at"])
    op.add_column("tasks", sa.Column("follow_up_obligation_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_tasks_follow_up_obligation_id",
        "tasks",
        "follow_up_obligations",
        ["follow_up_obligation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_task_active_follow_up",
        "tasks",
        ["follow_up_obligation_id"],
        unique=True,
        postgresql_where=sa.text("follow_up_obligation_id IS NOT NULL AND status IN ('pending', 'in_progress')"),
    )
