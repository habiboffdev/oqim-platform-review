"""amocrm slice A: crm_lead_links synced_value + pending_tasks

Revision ID: c3d4e5f6a7b8
Revises: c4e1a2b3d5f6
Create Date: 2026-06-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3d4e5f6a7b8"
down_revision = "c4e1a2b3d5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_lead_links",
        sa.Column("synced_value", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "crm_lead_links",
        sa.Column(
            "pending_tasks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.alter_column("crm_lead_links", "pending_tasks", server_default=None)


def downgrade() -> None:
    op.drop_column("crm_lead_links", "pending_tasks")
    op.drop_column("crm_lead_links", "synced_value")
