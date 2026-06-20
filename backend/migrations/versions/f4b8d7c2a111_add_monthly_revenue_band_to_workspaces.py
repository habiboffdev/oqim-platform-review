"""add monthly revenue band to workspaces

Revision ID: f4b8d7c2a111
Revises: bf5eb0e00390
Create Date: 2026-04-15 20:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4b8d7c2a111"
down_revision: Union[str, Sequence[str], None] = "bf5eb0e00390"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("monthly_revenue_band", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "monthly_revenue_band")
