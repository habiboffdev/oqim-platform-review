"""Initial tables: sellers, customers, conversations, messages

Revision ID: 001_initial
Revises: None
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sellers
    op.create_table(
        "sellers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone_number", sa.String(20), unique=True, nullable=False),
        sa.Column("business_name", sa.String(255), nullable=False),
        sa.Column("working_hours", sa.JSON(), nullable=True),
        sa.Column("subscription_tier", sa.String(20), server_default="free"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("telegram_connected", sa.Boolean(), server_default=sa.text("false")),
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

    # Customers
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "seller_id",
            sa.Integer(),
            sa.ForeignKey("sellers.id"),
            nullable=False,
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("phone_number", sa.String(20), nullable=True),
        sa.Column("language", sa.String(10), server_default="uz"),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("lifetime_value", sa.Float(), server_default="0.0"),
        sa.Column("notes", sa.Text(), nullable=True),
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

    # Conversations
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "seller_id",
            sa.Integer(),
            sa.ForeignKey("sellers.id"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id"),
            nullable=False,
        ),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("pipeline_stage", sa.String(20), server_default="new"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("needs_attention", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
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

    # Messages
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("sender_type", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(50), nullable=True),
        sa.Column("media_url", sa.String(500), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("customers")
    op.drop_table("sellers")
