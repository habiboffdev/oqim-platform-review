"""owner_bind_tokens

Revision ID: 829e9c305925
Revises: ce079d08b457
Create Date: 2026-06-17 23:17:09.533330
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '829e9c305925'
down_revision: Union[str, None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "owner_bind_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bound_chat_id", sa.BigInteger(), nullable=True),
    )
    op.create_table(
        "owner_bind_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("lane_workspace_id", sa.BigInteger(), nullable=True),
        sa.Column("token_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # D6: a dedicated bot token belongs to exactly one workspace.
    op.create_index(
        "uq_workspaces_control_bot_token",
        "workspaces",
        ["control_bot_token"],
        unique=True,
        postgresql_where=sa.text("control_bot_token IS NOT NULL"),
    )
    # Cutover: NULL stale userbot auto-binds (owner_control_chat_id == telegram_user_id);
    # those point at the business account, not the human owner.
    op.execute(
        "UPDATE workspaces SET owner_control_chat_id = NULL "
        "WHERE owner_control_chat_id IS NOT NULL "
        "AND owner_control_chat_id = telegram_user_id"
    )


def downgrade() -> None:
    op.drop_index("uq_workspaces_control_bot_token", table_name="workspaces")
    op.drop_table("owner_bind_events")
    op.drop_table("owner_bind_tokens")
