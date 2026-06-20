"""create draft workspace runtime

Revision ID: 9d72b9e1c4ab
Revises: 8b7f8e4f94c2
Create Date: 2026-04-04 18:10:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9d72b9e1c4ab"
down_revision: str | None = "8b7f8e4f94c2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "draft_workspace_runtime",
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("max_inflight_drafts", sa.SmallInteger(), server_default="2", nullable=False),
        sa.Column("max_ready_claims_per_tick", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_reason", sa.String(length=64), nullable=True),
        sa.Column("last_candidate_selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_candidate_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("draft_workspace_runtime")
