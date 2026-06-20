"""Rename products to catalog_items, add flexible fields, migrate variants.

Revision ID: 007_catalog_items
Revises: 006_agents
Create Date: 2026-02-13 12:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "007_catalog_items"
down_revision: Union[str, None] = "006_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Rename table
    op.rename_table("products", "catalog_items")

    # 2. Add new columns
    op.add_column("catalog_items", sa.Column("type", sa.String(50), server_default="product", nullable=False))
    op.add_column("catalog_items", sa.Column("attributes", sa.JSON(), server_default='{}', nullable=False))
    op.add_column("catalog_items", sa.Column("price_label", sa.String(100), nullable=True))

    # 3. Make price nullable (services may not have fixed prices)
    op.alter_column("catalog_items", "price", nullable=True)

    # 4. Rename confirmed_by_seller -> confirmed
    op.alter_column("catalog_items", "confirmed_by_seller", new_column_name="confirmed")

    # 5. Migrate variant data into attributes JSON
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE catalog_items ci
        SET attributes = jsonb_set(
            COALESCE(ci.attributes, '{}')::jsonb,
            '{variants}',
            COALESCE(sub.variant_data, '[]'::jsonb)
        )
        FROM (
            SELECT product_id, jsonb_agg(jsonb_build_object(
                'name', attribute_name,
                'value', attribute_value,
                'price_override', price_override,
                'stock_count', stock_count
            )) as variant_data
            FROM product_variants
            GROUP BY product_id
        ) sub
        WHERE ci.id = sub.product_id
    """))

    # 6. Also move stock_count into attributes for products that have it
    conn.execute(sa.text("""
        UPDATE catalog_items
        SET attributes = jsonb_set(COALESCE(attributes, '{}')::jsonb, '{stock_count}', to_jsonb(stock_count))
        WHERE stock_count IS NOT NULL
    """))

    # 7. Rename product_images -> catalog_item_images
    op.rename_table("product_images", "catalog_item_images")
    op.alter_column("catalog_item_images", "product_id", new_column_name="catalog_item_id")
    op.drop_constraint("product_images_product_id_fkey", "catalog_item_images", type_="foreignkey")
    op.create_foreign_key("catalog_item_images_catalog_item_id_fkey", "catalog_item_images", "catalog_items", ["catalog_item_id"], ["id"])

    # 8. Update FK constraint from workspaces
    op.drop_constraint("products_workspace_id_fkey", "catalog_items", type_="foreignkey")
    op.create_foreign_key("catalog_items_workspace_id_fkey", "catalog_items", "workspaces", ["workspace_id"], ["id"])

    # 9. Drop product_variants table
    op.drop_table("product_variants")

    # 10. Update embedding index
    op.drop_index("ix_products_embedding", "catalog_items")
    op.execute("CREATE INDEX ix_catalog_items_embedding ON catalog_items USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.drop_index("ix_catalog_items_embedding", "catalog_items")
    op.execute("CREATE INDEX ix_products_embedding ON products USING hnsw (embedding vector_cosine_ops)")

    op.create_table(
        "product_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("attribute_name", sa.String(100), nullable=False),
        sa.Column("attribute_value", sa.String(255), nullable=False),
        sa.Column("price_override", sa.Float(), nullable=True),
        sa.Column("stock_count", sa.Integer(), nullable=True),
    )

    op.drop_constraint("catalog_items_workspace_id_fkey", "catalog_items", type_="foreignkey")
    op.create_foreign_key("products_workspace_id_fkey", "catalog_items", "workspaces", ["workspace_id"], ["id"])

    op.drop_constraint("catalog_item_images_catalog_item_id_fkey", "catalog_item_images", type_="foreignkey")
    op.alter_column("catalog_item_images", "catalog_item_id", new_column_name="product_id")
    op.create_foreign_key("product_images_product_id_fkey", "catalog_item_images", "products", ["product_id"], ["id"])
    op.rename_table("catalog_item_images", "product_images")

    op.alter_column("catalog_items", "confirmed", new_column_name="confirmed_by_seller")
    op.alter_column("catalog_items", "price", nullable=False)
    op.drop_column("catalog_items", "price_label")
    op.drop_column("catalog_items", "attributes")
    op.drop_column("catalog_items", "type")
    op.rename_table("catalog_items", "products")
