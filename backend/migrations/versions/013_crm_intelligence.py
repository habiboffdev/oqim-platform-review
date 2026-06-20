"""Add CRM intelligence tables: pipeline_stages, message_insights,
customer_journey_events, crm_state on conversations, ai_training_data.

Revision ID: 013_crm_intelligence
Revises: 012_read_receipts
Create Date: 2026-02-22 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "013_crm_intelligence"
down_revision: Union[str, None] = "012_read_receipts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Custom pipeline stages per workspace
    op.create_table(
        "pipeline_stages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), default="#6B7280", nullable=False, server_default="#6B7280"),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_terminal", sa.Boolean(), default=False, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_pipeline_stages_workspace_slug", "pipeline_stages", ["workspace_id", "slug"])
    op.create_index("ix_pipeline_stages_workspace_id", "pipeline_stages", ["workspace_id"])

    # 2. Per-message CRM extraction (written by save_crm_intelligence tool)
    op.create_table(
        "message_insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("products_mentioned", JSONB(), server_default="[]"),
        sa.Column("budget_signal", sa.BigInteger(), nullable=True),
        sa.Column("delivery_required", sa.Boolean(), nullable=True),
        sa.Column("objections", sa.ARRAY(sa.Text()), server_default="{}"),
        sa.Column("contact_info", JSONB(), nullable=True),
        sa.Column("lead_score", sa.Float(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("urgency", sa.Boolean(), server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_message_insights_conversation_id", "message_insights", ["conversation_id"])
    op.create_index("ix_message_insights_workspace_id", "message_insights", ["workspace_id"])

    # 3. Customer journey timeline events
    op.create_table(
        "customer_journey_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_data", JSONB(), server_default="{}"),
        sa.Column("triggered_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_journey_events_workspace_customer", "customer_journey_events", ["workspace_id", "customer_id", "created_at"])
    op.create_index("ix_journey_events_conversation_id", "customer_journey_events", ["conversation_id"])

    # 4. CRM snapshot on conversation for fast Kanban display
    op.add_column("conversations", sa.Column("crm_state", JSONB(), nullable=True))

    # 5. Training data pairs for future fine-tuning
    op.create_table(
        "ai_training_data",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("completion", sa.Text(), nullable=False),
        sa.Column("signal", sa.Float(), server_default="1.0"),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_training_data_workspace_id", "ai_training_data", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("ai_training_data")
    op.drop_column("conversations", "crm_state")
    op.drop_table("customer_journey_events")
    op.drop_table("message_insights")
    op.drop_table("pipeline_stages")
