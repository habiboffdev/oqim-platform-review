"""Add operator actions/events tables and channel contract fields.

Revision ID: 015_operator_events_and_channel_contracts
Revises: 014_orders_and_crm_fields
Create Date: 2026-02-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "015_operator_events_and_channel_contracts"
down_revision: Union[str, None] = "014_orders_and_crm_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operator_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("channel", sa.String(length=20), server_default="dm", nullable=False),
        sa.Column("action_type", sa.String(length=50), nullable=False),
        sa.Column("risk_tier", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("execution_mode", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("reasoning_summary", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("policy_checks", JSONB(), nullable=True),
        sa.Column("evidence_refs", JSONB(), server_default="[]", nullable=False),
        sa.Column("extra_data", JSONB(), nullable=True),
        sa.Column("source_model", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="decided", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_operator_actions_workspace_id", "operator_actions", ["workspace_id"])
    op.create_index("ix_operator_actions_conversation_id", "operator_actions", ["conversation_id"])
    op.create_index("ix_operator_actions_created_at", "operator_actions", ["created_at"])

    op.create_table(
        "operator_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("action_id", sa.Integer(), sa.ForeignKey("operator_actions.id"), nullable=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("channel", sa.String(length=20), server_default="dm", nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_operator_events_event_id"),
    )
    op.create_index("ix_operator_events_workspace_id", "operator_events", ["workspace_id"])
    op.create_index("ix_operator_events_event_type", "operator_events", ["event_type"])
    op.create_index("ix_operator_events_occurred_at", "operator_events", ["occurred_at"])

    op.add_column("conversations", sa.Column("channel", sa.String(length=20), server_default="dm", nullable=False))
    op.add_column("conversations", sa.Column("external_chat_id", sa.String(length=255), nullable=True))
    op.add_column("conversations", sa.Column("external_thread_id", sa.String(length=255), nullable=True))

    op.add_column("messages", sa.Column("channel", sa.String(length=20), server_default="dm", nullable=False))
    op.add_column("messages", sa.Column("external_message_id", sa.String(length=255), nullable=True))
    op.add_column("messages", sa.Column("external_author_id", sa.String(length=255), nullable=True))
    op.add_column("messages", sa.Column("external_parent_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "external_parent_id")
    op.drop_column("messages", "external_author_id")
    op.drop_column("messages", "external_message_id")
    op.drop_column("messages", "channel")

    op.drop_column("conversations", "external_thread_id")
    op.drop_column("conversations", "external_chat_id")
    op.drop_column("conversations", "channel")

    op.drop_index("ix_operator_events_occurred_at", table_name="operator_events")
    op.drop_index("ix_operator_events_event_type", table_name="operator_events")
    op.drop_index("ix_operator_events_workspace_id", table_name="operator_events")
    op.drop_table("operator_events")

    op.drop_index("ix_operator_actions_created_at", table_name="operator_actions")
    op.drop_index("ix_operator_actions_conversation_id", table_name="operator_actions")
    op.drop_index("ix_operator_actions_workspace_id", table_name="operator_actions")
    op.drop_table("operator_actions")
