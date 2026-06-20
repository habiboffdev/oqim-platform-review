"""Add task source traceability fields for promise-to-task flow.

Revision ID: 021_task_source_traceability
Revises: 020_surface_autonomy_flags
Create Date: 2026-02-26 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021_task_source_traceability"
down_revision: Union[str, None] = "020_surface_autonomy_flags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("source", sa.String(length=50), nullable=True))
    op.add_column("tasks", sa.Column("source_action_id", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("source_evidence_ref", sa.String(length=255), nullable=True))
    op.create_foreign_key(
        "fk_tasks_source_action_id_operator_actions",
        "tasks",
        "operator_actions",
        ["source_action_id"],
        ["id"],
    )
    op.create_index("ix_tasks_source_action_id", "tasks", ["source_action_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_source_action_id", table_name="tasks")
    op.drop_constraint(
        "fk_tasks_source_action_id_operator_actions",
        "tasks",
        type_="foreignkey",
    )
    op.drop_column("tasks", "source_evidence_ref")
    op.drop_column("tasks", "source_action_id")
    op.drop_column("tasks", "source")
