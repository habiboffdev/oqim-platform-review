"""Add orders + order_items tables, extend customers and conversations.

Revision ID: 014_orders_and_crm_fields
Revises: 013_crm_intelligence
Create Date: 2026-02-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "014_orders_and_crm_fields"
down_revision: Union[str, None] = "013_crm_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Orders table ---
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("order_number", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("currency", sa.String(10), server_default="UZS", nullable=False),
        sa.Column("delivery_type", sa.String(20), nullable=True),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("payment_method", sa.String(20), nullable=True),
        sa.Column("payment_status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("created_via", sa.String(20), server_default="manual", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_orders_workspace_id", "orders", ["workspace_id"])
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_index("ix_orders_conversation_id", "orders", ["conversation_id"])
    op.create_index("ix_orders_status", "orders", ["status"])

    # --- Order items table ---
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("catalog_item_id", sa.Integer(), sa.ForeignKey("catalog_items.id"), nullable=True),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("total_price", sa.Numeric(12, 2), server_default="0", nullable=False),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])

    # --- Extend customers ---
    op.add_column("customers", sa.Column("ai_brief", sa.Text(), nullable=True))
    op.add_column("customers", sa.Column("address", sa.Text(), nullable=True))

    # --- Extend conversations ---
    op.add_column("conversations", sa.Column("deal_value", sa.Numeric(12, 2), nullable=True))
    op.add_column("conversations", sa.Column("products_mentioned", JSONB(), server_default="[]", nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "products_mentioned")
    op.drop_column("conversations", "deal_value")
    op.drop_column("customers", "address")
    op.drop_column("customers", "ai_brief")
    op.drop_index("ix_order_items_order_id", "order_items")
    op.drop_table("order_items")
    op.drop_index("ix_orders_status", "orders")
    op.drop_index("ix_orders_conversation_id", "orders")
    op.drop_index("ix_orders_customer_id", "orders")
    op.drop_index("ix_orders_workspace_id", "orders")
    op.drop_table("orders")
