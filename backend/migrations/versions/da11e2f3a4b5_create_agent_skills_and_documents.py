"""create agent_skills and agent_document_sections tables

Revision ID: da11e2f3a4b5
Revises: c9d0e1f2a3b4
Create Date: 2026-05-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "da11e2f3a4b5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("when_to_use", sa.Text(), nullable=False, server_default=""),
        sa.Column("when_not_to_use", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "input_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "output_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "examples",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
            "workspace_id", "slug", name="uq_agent_skills_workspace_slug"
        ),
    )
    op.create_index(
        "ix_agent_skills_workspace_id", "agent_skills", ["workspace_id"]
    )
    op.create_index(
        "ix_agent_skills_workspace_agent",
        "agent_skills",
        ["workspace_id", "agent_id"],
    )

    op.create_table(
        "agent_document_sections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("document_kind", sa.String(length=20), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("section_key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "source_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "generated_by",
            sa.String(length=40),
            nullable=False,
            server_default="system",
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
            "document_kind",
            "subject_type",
            "subject_id",
            "section_key",
            name="uq_agent_document_sections_subject_key",
        ),
    )
    op.create_index(
        "ix_agent_document_sections_workspace_id",
        "agent_document_sections",
        ["workspace_id"],
    )
    op.create_index(
        "ix_agent_document_sections_lookup",
        "agent_document_sections",
        ["workspace_id", "document_kind", "subject_type", "subject_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_document_sections_lookup", table_name="agent_document_sections"
    )
    op.drop_index(
        "ix_agent_document_sections_workspace_id",
        table_name="agent_document_sections",
    )
    op.drop_table("agent_document_sections")

    op.drop_index("ix_agent_skills_workspace_agent", table_name="agent_skills")
    op.drop_index("ix_agent_skills_workspace_id", table_name="agent_skills")
    op.drop_table("agent_skills")
