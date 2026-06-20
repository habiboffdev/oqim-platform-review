"""Extend ai_training_data with label + action linkage.

Revision ID: 016_training_vault_labels
Revises: 015_operator_events_and_channel_contracts
Create Date: 2026-02-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016_training_vault_labels"
down_revision: Union[str, None] = "015_operator_events_and_channel_contracts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_training_data", sa.Column("action_id", sa.Integer(), nullable=True))
    op.add_column(
        "ai_training_data",
        sa.Column("label", sa.String(length=32), server_default="approved", nullable=False),
    )
    op.add_column(
        "ai_training_data",
        sa.Column("pii_redacted", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("ai_training_data", sa.Column("context_snapshot_ref", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_ai_training_data_action_id_operator_actions",
        "ai_training_data",
        "operator_actions",
        ["action_id"],
        ["id"],
    )
    op.create_index("ix_ai_training_data_action_id", "ai_training_data", ["action_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_training_data_action_id", table_name="ai_training_data")
    op.drop_constraint("fk_ai_training_data_action_id_operator_actions", "ai_training_data", type_="foreignkey")
    op.drop_column("ai_training_data", "context_snapshot_ref")
    op.drop_column("ai_training_data", "pii_redacted")
    op.drop_column("ai_training_data", "label")
    op.drop_column("ai_training_data", "action_id")
