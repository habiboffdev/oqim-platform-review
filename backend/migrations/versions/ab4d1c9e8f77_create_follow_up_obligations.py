"""create follow up obligations and align tasks

Revision ID: ab4d1c9e8f77
Revises: 9d72b9e1c4ab
Create Date: 2026-04-05 14:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "ab4d1c9e8f77"
down_revision: str | None = "9d72b9e1c4ab"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "follow_up_obligations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("root_message_id", sa.Integer(), nullable=True),
        sa.Column("root_ai_reply_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("waiting_for", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=10), server_default="medium", nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("suggested_message", sa.Text(), nullable=True),
        sa.Column("response_expected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seller_message_id", sa.Integer(), nullable=True),
        sa.Column("last_customer_message_id", sa.Integer(), nullable=True),
        sa.Column("resolution_reason", sa.String(length=64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["last_customer_message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["last_seller_message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["root_ai_reply_id"], ["ai_replies.id"]),
        sa.ForeignKeyConstraint(["root_message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_follow_up_obligations_due",
        "follow_up_obligations",
        ["workspace_id", "state", "due_at"],
        unique=False,
    )
    op.create_index(
        "idx_follow_up_obligations_conversation",
        "follow_up_obligations",
        ["workspace_id", "conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_follow_up_obligations_customer",
        "follow_up_obligations",
        ["workspace_id", "customer_id", "created_at"],
        unique=False,
    )
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
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("obligation_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("ai_reply_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("actor_type", sa.String(length=24), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ai_reply_id"], ["ai_replies.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["obligation_id"], ["follow_up_obligations.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_follow_up_events_conversation",
        "follow_up_events",
        ["workspace_id", "conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_follow_up_events_obligation",
        "follow_up_events",
        ["obligation_id", "created_at"],
        unique=False,
    )

    op.add_column("tasks", sa.Column("follow_up_obligation_id", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("task_kind", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("source_evidence_ref", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("auto_created", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("tasks", sa.Column("resolution_reason", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        "fk_tasks_follow_up_obligation_id",
        "tasks",
        "follow_up_obligations",
        ["follow_up_obligation_id"],
        ["id"],
    )
    op.create_index(
        "uq_tasks_active_follow_up",
        "tasks",
        ["follow_up_obligation_id"],
        unique=True,
        postgresql_where=sa.text(
            "follow_up_obligation_id IS NOT NULL AND status IN ('pending', 'in_progress')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_tasks_active_follow_up", table_name="tasks")
    op.drop_constraint("fk_tasks_follow_up_obligation_id", "tasks", type_="foreignkey")
    op.drop_column("tasks", "resolution_reason")
    op.drop_column("tasks", "auto_created")
    op.drop_column("tasks", "source_evidence_ref")
    op.drop_column("tasks", "task_kind")
    op.drop_column("tasks", "follow_up_obligation_id")

    op.drop_index("idx_follow_up_events_obligation", table_name="follow_up_events")
    op.drop_index("idx_follow_up_events_conversation", table_name="follow_up_events")
    op.drop_table("follow_up_events")

    op.drop_index("uq_follow_up_active_kind", table_name="follow_up_obligations")
    op.drop_index("idx_follow_up_obligations_customer", table_name="follow_up_obligations")
    op.drop_index("idx_follow_up_obligations_conversation", table_name="follow_up_obligations")
    op.drop_index("idx_follow_up_obligations_due", table_name="follow_up_obligations")
    op.drop_table("follow_up_obligations")
