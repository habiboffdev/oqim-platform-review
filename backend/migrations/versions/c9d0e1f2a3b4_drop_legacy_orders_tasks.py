"""drop legacy orders and tasks tables

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-05-15
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS order_items CASCADE")
    op.execute("DROP TABLE IF EXISTS orders CASCADE")
    op.execute("DROP TABLE IF EXISTS tasks CASCADE")


def downgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("priority", sa.String(length=10), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("task_kind", sa.String(length=32), nullable=True),
        sa.Column("source_evidence_ref", sa.Text(), nullable=True),
        sa.Column("auto_created", sa.Boolean(), nullable=True),
        sa.Column("resolution_reason", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("order_number", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("delivery_type", sa.String(length=20), nullable=True),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("payment_method", sa.String(length=20), nullable=True),
        sa.Column("payment_status", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_agent_id", sa.Integer(), nullable=True),
        sa.Column("created_via", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["created_by_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("catalog_product_ref", sa.String(length=255), nullable=True),
        sa.Column("product_name", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_price", sa.Numeric(12, 2), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
