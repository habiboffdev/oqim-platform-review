"""v6_agent_type_undo_delay_contact_scope

Revision ID: 1bde71d7c2cb
Revises: 5b1140b35a01
Create Date: 2026-03-31 06:05:01.672713
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1bde71d7c2cb'
down_revision: Union[str, None] = '5b1140b35a01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("agent_type", sa.String(20), nullable=False, server_default="customer"))
    op.add_column("agents", sa.Column("undo_delay_seconds", sa.Integer(), nullable=False, server_default="15"))
    op.add_column("agents", sa.Column("contact_scope", sa.String(20), nullable=False, server_default="business"))


def downgrade() -> None:
    op.drop_column("agents", "contact_scope")
    op.drop_column("agents", "undo_delay_seconds")
    op.drop_column("agents", "agent_type")
