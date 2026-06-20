"""Add unique constraint on voice_profiles.workspace_id

Revision ID: e1f2a3b4c5d6
Revises: c4d5e6f7a8b9
Create Date: 2026-04-09
"""
from typing import Union

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Deduplicate: keep only the latest profile per workspace
    op.execute("""
        DELETE FROM voice_profiles
        WHERE id NOT IN (
            SELECT DISTINCT ON (workspace_id) id
            FROM voice_profiles
            ORDER BY workspace_id, generated_at DESC NULLS LAST
        )
    """)
    op.create_unique_constraint(
        "uq_voice_profiles_workspace_id", "voice_profiles", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_voice_profiles_workspace_id", "voice_profiles", type_="unique")
