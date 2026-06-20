"""Rename sellers to workspaces and add new columns.

Revision ID: 005_workspaces
Revises: 004_credits
Create Date: 2026-02-13 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "005_workspaces"
down_revision: Union[str, None] = "004_credits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Rename table
    op.rename_table("sellers", "workspaces")

    # 2. Rename column
    op.alter_column("workspaces", "business_name", new_column_name="name")

    # 3. Add new columns
    op.add_column("workspaces", sa.Column("type", sa.String(50), server_default="ecommerce", nullable=False))
    op.add_column("workspaces", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("workspaces", sa.Column(
        "pipeline_stages",
        sa.JSON(),
        server_default='["new","qualified","negotiation","won","lost"]',
        nullable=False,
    ))

    # 4. Rename FK columns in all dependent tables
    op.alter_column("customers", "seller_id", new_column_name="workspace_id")
    op.alter_column("conversations", "seller_id", new_column_name="workspace_id")
    op.alter_column("products", "seller_id", new_column_name="workspace_id")
    op.alter_column("business_knowledge", "seller_id", new_column_name="workspace_id")
    op.alter_column("trust_configs", "seller_id", new_column_name="workspace_id")

    # 5. Update FK constraints (drop old, add new)
    # customers
    op.drop_constraint("customers_seller_id_fkey", "customers", type_="foreignkey")
    op.create_foreign_key("customers_workspace_id_fkey", "customers", "workspaces", ["workspace_id"], ["id"])

    # conversations
    op.drop_constraint("conversations_seller_id_fkey", "conversations", type_="foreignkey")
    op.create_foreign_key("conversations_workspace_id_fkey", "conversations", "workspaces", ["workspace_id"], ["id"])

    # products
    op.drop_constraint("products_seller_id_fkey", "products", type_="foreignkey")
    op.create_foreign_key("products_workspace_id_fkey", "products", "workspaces", ["workspace_id"], ["id"])

    # business_knowledge
    op.drop_constraint("business_knowledge_seller_id_fkey", "business_knowledge", type_="foreignkey")
    op.create_foreign_key("business_knowledge_workspace_id_fkey", "business_knowledge", "workspaces", ["workspace_id"], ["id"])

    # trust_configs
    op.drop_constraint("trust_configs_seller_id_fkey", "trust_configs", type_="foreignkey")
    op.create_foreign_key("trust_configs_workspace_id_fkey", "trust_configs", "workspaces", ["workspace_id"], ["id"])

    # Update the unique constraint on trust_configs
    op.drop_constraint("trust_configs_seller_id_key", "trust_configs", type_="unique")
    op.create_unique_constraint("trust_configs_workspace_id_key", "trust_configs", ["workspace_id"])

    # Update index on business_knowledge
    op.drop_index("ix_business_knowledge_seller_active", "business_knowledge")
    op.create_index("ix_business_knowledge_workspace_active", "business_knowledge", ["workspace_id", "is_active"])


def downgrade() -> None:
    # Reverse everything
    op.drop_index("ix_business_knowledge_workspace_active", "business_knowledge")
    op.create_index("ix_business_knowledge_seller_active", "business_knowledge", ["seller_id", "is_active"])

    op.drop_constraint("trust_configs_workspace_id_key", "trust_configs", type_="unique")
    op.create_unique_constraint("trust_configs_seller_id_key", "trust_configs", ["seller_id"])

    op.drop_constraint("trust_configs_workspace_id_fkey", "trust_configs", type_="foreignkey")
    op.create_foreign_key("trust_configs_seller_id_fkey", "trust_configs", "sellers", ["seller_id"], ["id"])

    op.drop_constraint("business_knowledge_workspace_id_fkey", "business_knowledge", type_="foreignkey")
    op.create_foreign_key("business_knowledge_seller_id_fkey", "business_knowledge", "sellers", ["seller_id"], ["id"])

    op.drop_constraint("products_workspace_id_fkey", "products", type_="foreignkey")
    op.create_foreign_key("products_seller_id_fkey", "products", "sellers", ["seller_id"], ["id"])

    op.drop_constraint("conversations_workspace_id_fkey", "conversations", type_="foreignkey")
    op.create_foreign_key("conversations_seller_id_fkey", "conversations", "sellers", ["seller_id"], ["id"])

    op.drop_constraint("customers_workspace_id_fkey", "customers", type_="foreignkey")
    op.create_foreign_key("customers_seller_id_fkey", "customers", "sellers", ["seller_id"], ["id"])

    op.alter_column("trust_configs", "workspace_id", new_column_name="seller_id")
    op.alter_column("business_knowledge", "workspace_id", new_column_name="seller_id")
    op.alter_column("products", "workspace_id", new_column_name="seller_id")
    op.alter_column("conversations", "workspace_id", new_column_name="seller_id")
    op.alter_column("customers", "workspace_id", new_column_name="seller_id")

    op.drop_column("workspaces", "pipeline_stages")
    op.drop_column("workspaces", "description")
    op.drop_column("workspaces", "type")

    op.alter_column("workspaces", "name", new_column_name="business_name")
    op.rename_table("workspaces", "sellers")
