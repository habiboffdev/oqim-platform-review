"""create onboarding runtime

Revision ID: 8c4d2f19b7a6
Revises: 6ac1d91f2b40
Create Date: 2026-05-02
"""
import sqlalchemy as sa
from alembic import op

revision = "8c4d2f19b7a6"
down_revision = "6ac1d91f2b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onboarding_runtime",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("progress_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_onboarding_runtime_workspace"),
    )
    op.create_index("ix_onboarding_runtime_workspace_id", "onboarding_runtime", ["workspace_id"])
    op.create_index("ix_onboarding_runtime_state", "onboarding_runtime", ["state"])
    op.create_index("ix_onboarding_runtime_phase", "onboarding_runtime", ["phase"])
    op.create_index("ix_onboarding_runtime_leased_until", "onboarding_runtime", ["leased_until"])
    op.create_index("ix_onboarding_runtime_next_attempt_at", "onboarding_runtime", ["next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_onboarding_runtime_next_attempt_at", table_name="onboarding_runtime")
    op.drop_index("ix_onboarding_runtime_leased_until", table_name="onboarding_runtime")
    op.drop_index("ix_onboarding_runtime_phase", table_name="onboarding_runtime")
    op.drop_index("ix_onboarding_runtime_state", table_name="onboarding_runtime")
    op.drop_index("ix_onboarding_runtime_workspace_id", table_name="onboarding_runtime")
    op.drop_table("onboarding_runtime")
