"""rename delivery runtime action correlation

Revision ID: a0b1c2d3e4f5
Revises: 9d0e1f2a3b45
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "a0b1c2d3e4f5"
down_revision = "9d0e1f2a3b45"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table_name)}


def _drop_fks_for_column(table_name: str, column_name: str) -> None:
    inspector = inspect(op.get_bind())
    for constraint in inspector.get_foreign_keys(table_name):
        if constraint.get("constrained_columns") == [column_name] and constraint.get("name"):
            op.drop_constraint(constraint["name"], table_name, type_="foreignkey")


def upgrade() -> None:
    if not _table_exists("delivery_runtime"):
        return

    columns = _column_names("delivery_runtime")
    indexes = _index_names("delivery_runtime")
    if "ai_reply_id" in columns:
        if "ix_delivery_runtime_ai_reply_id" in indexes:
            op.drop_index("ix_delivery_runtime_ai_reply_id", table_name="delivery_runtime")
        _drop_fks_for_column("delivery_runtime", "ai_reply_id")
        op.alter_column(
            "delivery_runtime",
            "ai_reply_id",
            new_column_name="action_record_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        indexes = _index_names("delivery_runtime")

    columns = _column_names("delivery_runtime")
    if "action_record_id" in columns and "ix_delivery_runtime_action_record_id" not in indexes:
        op.create_index(
            "ix_delivery_runtime_action_record_id",
            "delivery_runtime",
            ["action_record_id"],
        )


def downgrade() -> None:
    if not _table_exists("delivery_runtime"):
        return

    columns = _column_names("delivery_runtime")
    indexes = _index_names("delivery_runtime")
    if "action_record_id" in columns:
        if "ix_delivery_runtime_action_record_id" in indexes:
            op.drop_index("ix_delivery_runtime_action_record_id", table_name="delivery_runtime")
        op.alter_column(
            "delivery_runtime",
            "action_record_id",
            new_column_name="ai_reply_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        indexes = _index_names("delivery_runtime")

    columns = _column_names("delivery_runtime")
    if "ai_reply_id" in columns and "ix_delivery_runtime_ai_reply_id" not in indexes:
        op.create_index("ix_delivery_runtime_ai_reply_id", "delivery_runtime", ["ai_reply_id"])
