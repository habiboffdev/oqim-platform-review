"""Add metric snapshots table for AIR7 and safety rollups.

Revision ID: 017_metric_snapshots
Revises: 016_training_vault_labels
Create Date: 2026-02-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "017_metric_snapshots"
down_revision: Union[str, None] = "016_training_vault_labels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("metric_name", sa.String(length=50), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("dimensions", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workspace_id", "metric_name", "metric_date", name="uq_metric_snapshots_workspace_metric_date"),
    )
    op.create_index("ix_metric_snapshots_workspace_id", "metric_snapshots", ["workspace_id"])
    op.create_index("ix_metric_snapshots_metric_date", "metric_snapshots", ["metric_date"])


def downgrade() -> None:
    op.drop_index("ix_metric_snapshots_metric_date", table_name="metric_snapshots")
    op.drop_index("ix_metric_snapshots_workspace_id", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")
