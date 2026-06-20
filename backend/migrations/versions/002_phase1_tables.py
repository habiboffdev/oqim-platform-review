"""Phase 1 tables: products, product_images, product_variants, ai_replies, trust_configs

Revision ID: 002_phase1
Revises: 001_initial
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_phase1"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Products
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "seller_id",
            sa.Integer(),
            sa.ForeignKey("sellers.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("cost_price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(10), server_default="UZS"),
        sa.Column("stock_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("confirmed_by_seller", sa.Boolean(), server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # Product Images
    op.create_table(
        "product_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id"),
            nullable=False,
        ),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("ai_description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
    )

    # Product Variants
    op.create_table(
        "product_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id"),
            nullable=False,
        ),
        sa.Column("attribute_name", sa.String(100), nullable=False),
        sa.Column("attribute_value", sa.String(255), nullable=False),
        sa.Column("price_override", sa.Float(), nullable=True),
        sa.Column("stock_count", sa.Integer(), nullable=True),
    )

    # AI Replies
    op.create_table(
        "ai_replies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column(
            "trigger_message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id"),
            nullable=True,
        ),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft"),
        sa.Column("draft_content", sa.Text(), nullable=False),
        sa.Column("final_content", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(100), nullable=False),
        sa.Column("response_time_ms", sa.Integer(), nullable=False),
        sa.Column("scheduled_send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actually_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    # Trust Configs
    op.create_table(
        "trust_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "seller_id",
            sa.Integer(),
            sa.ForeignKey("sellers.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("mode", sa.String(20), server_default="draft"),
        sa.Column("auto_send_threshold", sa.Float(), server_default="0.85"),
        sa.Column("escalation_topics", sa.JSON(), nullable=True),
        sa.Column("working_hours_start", sa.String(10), server_default="09:00"),
        sa.Column("working_hours_end", sa.String(10), server_default="18:00"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("trust_configs")
    op.drop_table("ai_replies")
    op.drop_table("product_variants")
    op.drop_table("product_images")
    op.drop_table("products")
