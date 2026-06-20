"""rebuild-p1: workspace_budgets table

Revision ID: 1981a8aa5adb
Revises: fc33a4b5c6d7
Create Date: 2026-05-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "1981a8aa5adb"
down_revision = "fc33a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_date", sa.Date(), nullable=False),
        sa.Column(
            "tokens_in_used",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tokens_out_used",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "daily_cap_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="10000000",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "period_date",
            name="uq_budget_per_day",
        ),
    )
    op.create_index(
        "ix_workspace_budgets_workspace_id",
        "workspace_budgets",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_budgets_workspace_id", table_name="workspace_budgets")
    op.drop_table("workspace_budgets")
