"""promoter outreach tables

Two new tables — ``outreach_campaigns`` (owner-approved drip plan with goal,
segment, base message, and cap overrides) and ``outreach_targets`` (per-contact
state machine: pending → sending → sent | failed | opted_out | converted) —
plus a ``customers.opted_out`` column so person-level suppression is durable
and workspace-scoped independent of which campaign or channel triggered it.

The unique constraints encode the two key invariants without extra application
logic: one target row per (campaign, phone) pair prevents duplicate sends to
the same number in the same campaign, and one row per idempotency_key gives the
drip worker a safe retry handle.

Revision ID: ce7a81de98e8
Revises: 2f6e787eca01
Create Date: 2026-06-13 00:38:14.340252
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ce7a81de98e8"
down_revision: str | None = "2f6e787eca01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # outreach_campaigns                                                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "outreach_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("crm_connections.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("goal", sa.String(64), nullable=False),
        sa.Column("segment_spec", postgresql.JSONB(), nullable=False),
        sa.Column("base_message", sa.Text(), nullable=False),
        sa.Column("caps", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_outreach_campaigns_workspace_id", "outreach_campaigns", ["workspace_id"]
    )

    # ------------------------------------------------------------------ #
    # outreach_targets                                                     #
    # ------------------------------------------------------------------ #
    op.create_table(
        "outreach_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("outreach_campaigns.id"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("provider_contact_id", sa.String(64), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("tier", sa.String(8), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "campaign_id", "phone", name="uq_outreach_targets_campaign_phone"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_outreach_targets_idem"),
    )
    op.create_index(
        "ix_outreach_targets_campaign_id", "outreach_targets", ["campaign_id"]
    )
    op.create_index(
        "ix_outreach_targets_workspace_id", "outreach_targets", ["workspace_id"]
    )
    op.create_index(
        "ix_outreach_targets_scan",
        "outreach_targets",
        ["state", "next_attempt_at"],
    )

    # ------------------------------------------------------------------ #
    # customers.opted_out                                                  #
    # ------------------------------------------------------------------ #
    op.add_column(
        "customers",
        sa.Column("opted_out", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("customers", "opted_out")
    op.drop_table("outreach_targets")
    op.drop_table("outreach_campaigns")
