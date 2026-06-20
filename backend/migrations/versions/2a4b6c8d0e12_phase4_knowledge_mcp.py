"""phase4: knowledge mcp storage

Revision ID: 2a4b6c8d0e12
Revises: 1d2e3f4a5b6c
Create Date: 2026-05-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "2a4b6c8d0e12"
down_revision = "1d2e3f4a5b6c"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def upgrade() -> None:
    op.create_table(
        "knowledge_collections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("collection_id", sa.String(length=255), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("owner_type", "owner_id", "collection_id", name="uq_knowledge_collections_owner_collection"),
    )
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("source_kind", sa.String(length=80), nullable=False),
        sa.Column("external_ref", sa.String(length=512), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("acl_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("freshness", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ingestion_status", sa.String(length=32), nullable=False, server_default="ready"),
        sa.Column("raw_content", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("owner_type", "owner_id", "source_id", name="uq_knowledge_sources_owner_source"),
    )
    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.String(length=255), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("collection_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("authority_state", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_by_ref", sa.String(length=255), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("owner_type", "owner_id", "item_id", name="uq_knowledge_items_owner_item"),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chunk_id", sa.String(length=255), nullable=False),
        sa.Column("item_id", sa.String(length=255), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("contextual_prefix", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("citation", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("owner_type", "owner_id", "chunk_id", name="uq_knowledge_chunks_owner_chunk"),
    )
    op.create_table(
        "knowledge_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.String(length=255), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("proposed_kind", sa.String(length=80), nullable=False),
        sa.Column("proposed_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("agent_control_action_id", sa.String(length=255), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("owner_type", "owner_id", "candidate_id", name="uq_knowledge_candidates_owner_candidate"),
    )
    for table in (
        "knowledge_collections",
        "knowledge_sources",
        "knowledge_items",
        "knowledge_chunks",
        "knowledge_candidates",
    ):
        op.create_index(f"ix_{table}_owner_type", table, ["owner_type"])
        op.create_index(f"ix_{table}_owner_id", table, ["owner_id"])
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
    op.create_index("ix_knowledge_items_kind", "knowledge_items", ["kind"])
    op.create_index("ix_knowledge_items_authority_state", "knowledge_items", ["authority_state"])
    op.create_index("ix_knowledge_candidates_status", "knowledge_candidates", ["status"])
    op.create_index("ix_knowledge_candidates_agent_control_action_id", "knowledge_candidates", ["agent_control_action_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_candidates_agent_control_action_id", table_name="knowledge_candidates")
    op.drop_index("ix_knowledge_candidates_status", table_name="knowledge_candidates")
    op.drop_index("ix_knowledge_items_authority_state", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_kind", table_name="knowledge_items")
    for table in (
        "knowledge_candidates",
        "knowledge_chunks",
        "knowledge_items",
        "knowledge_sources",
        "knowledge_collections",
    ):
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_index(f"ix_{table}_owner_id", table_name=table)
        op.drop_index(f"ix_{table}_owner_type", table_name=table)
    op.drop_table("knowledge_candidates")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_items")
    op.drop_table("knowledge_sources")
    op.drop_table("knowledge_collections")
