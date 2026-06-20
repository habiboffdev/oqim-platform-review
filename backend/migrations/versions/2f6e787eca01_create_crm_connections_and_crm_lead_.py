"""create crm_connections and crm_lead_links

The typed, provider-neutral CRM connection layer (credentials live here, not on
``workspaces`` — a second provider is approved scope and single-use refresh
tokens need their own row to lock without contending the hot workspaces row) and
the per-conversation desired-state lead links the sync plane reconciles.

Two partial unique indexes encode the connection invariants without blocking
audit history: exactly one ACTIVE connection per workspace, and the same
external CRM account can't be ACTIVELY bound to two workspaces — disconnected /
degraded rows are kept and never collide. One lead per conversation is a plain
unique constraint on ``(connection_id, conversation_id)``.

Revision ID: 2f6e787eca01
Revises: a7d3e9f0c1b2
Create Date: 2026-06-12 21:18:03.567180
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2f6e787eca01'
down_revision: str | None = 'a7d3e9f0c1b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crm_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("provider_account_ref", sa.String(255), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pipeline_config", postgresql.JSONB(), nullable=False),
        sa.Column("webhook_token", sa.String(64), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("webhook_token", name="uq_crm_connections_webhook_token"),
    )
    op.create_index(
        "ix_crm_connections_workspace_id", "crm_connections", ["workspace_id"]
    )
    op.create_index(
        "uq_crm_connections_workspace_active",
        "crm_connections",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_crm_connections_provider_account_active",
        "crm_connections",
        ["provider", "provider_account_ref"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "crm_lead_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("crm_connections.id"),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column(
            "desired_stage_role", sa.String(16), nullable=False, server_default="new"
        ),
        sa.Column(
            "stage_authority", sa.String(8), nullable=False, server_default="oqim"
        ),
        sa.Column("pending_notes", postgresql.JSONB(), nullable=False),
        sa.Column(
            "sync_state", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("provider_lead_id", sa.String(64), nullable=True),
        sa.Column("provider_contact_id", sa.String(64), nullable=True),
        sa.Column("synced_stage_role", sa.String(16), nullable=True),
        sa.Column("last_synced_stage_id", sa.String(64), nullable=True),
        sa.Column("last_observed_stage_id", sa.String(64), nullable=True),
        sa.Column("synced_phone", sa.String(32), nullable=True),
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
            "connection_id",
            "conversation_id",
            name="uq_crm_lead_links_connection_conversation",
        ),
    )
    op.create_index(
        "ix_crm_lead_links_workspace_id", "crm_lead_links", ["workspace_id"]
    )
    op.create_index(
        "ix_crm_lead_links_connection_id", "crm_lead_links", ["connection_id"]
    )
    op.create_index(
        "ix_crm_lead_links_scan",
        "crm_lead_links",
        ["sync_state", "next_attempt_at"],
    )
    op.create_index(
        "ix_crm_lead_links_provider_lead",
        "crm_lead_links",
        ["connection_id", "provider_lead_id"],
    )


def downgrade() -> None:
    op.drop_table("crm_lead_links")
    op.drop_table("crm_connections")
