"""drop legacy catalog item tables

Revision ID: f4d5e6a7b8c9
Revises: f3c4d5e6a7b8
Create Date: 2026-05-09 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


revision: str = "f4d5e6a7b8c9"
down_revision: Union[str, None] = "f3c4d5e6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_items",
        sa.Column("catalog_product_ref", sa.String(length=255), nullable=True),
    )
    op.execute(
        """
        UPDATE order_items
        SET catalog_product_ref = 'legacy_catalog_item:' || catalog_item_id::text
        WHERE catalog_item_id IS NOT NULL
          AND catalog_product_ref IS NULL
        """
    )
    op.execute(
        "ALTER TABLE order_items DROP CONSTRAINT IF EXISTS order_items_catalog_item_id_fkey"
    )
    op.drop_column("order_items", "catalog_item_id")
    op.drop_table("catalog_item_images")
    op.drop_table("catalog_items")


def downgrade() -> None:
    op.create_table(
        "catalog_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("cost_price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("price_label", sa.String(length=100), nullable=True),
        sa.Column("stock_count", sa.Integer(), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=True),
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("variants", sa.JSON(), nullable=True),
        sa.Column("embedding", Vector(3072), nullable=True),
        sa.Column("image_embedding", Vector(3072), nullable=True),
        sa.Column(
            "embedding_status",
            sa.String(length=20),
            nullable=True,
            server_default="pending",
        ),
        sa.Column("source_post_id", sa.Integer(), nullable=True),
        sa.Column("source_channel_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "catalog_item_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("catalog_item_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=True),
        sa.Column("ai_description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(["catalog_item_id"], ["catalog_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "order_items",
        sa.Column("catalog_item_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE order_items
        SET catalog_item_id = split_part(catalog_product_ref, ':', 2)::integer
        WHERE catalog_product_ref LIKE 'legacy_catalog_item:%'
        """
    )
    op.create_foreign_key(
        "order_items_catalog_item_id_fkey",
        "order_items",
        "catalog_items",
        ["catalog_item_id"],
        ["id"],
    )
    op.drop_column("order_items", "catalog_product_ref")
