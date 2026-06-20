"""create commerce catalog core

Revision ID: 5f0a1b2c3d4e
Revises: 4c6d8e0f1a23
Create Date: 2026-06-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "5f0a1b2c3d4e"
down_revision = "4c6d8e0f1a23"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def _json_array() -> sa.Column:
    return sa.Column(
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )


def _json_object() -> sa.Column:
    return sa.Column(
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )


def upgrade() -> None:
    op.create_table(
        "catalog_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("product_ref", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("authority_state", sa.String(length=32), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_fact_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("freshness", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "product_ref", name="uq_catalog_products_workspace_product"),
    )
    op.create_table(
        "catalog_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("variant_ref", sa.String(length=255), nullable=False),
        sa.Column("product_ref", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("authority_state", sa.String(length=32), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_fact_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "variant_ref", name="uq_catalog_variants_workspace_variant"),
    )
    op.create_table(
        "catalog_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("offer_ref", sa.String(length=255), nullable=False),
        sa.Column("product_ref", sa.String(length=255), nullable=False),
        sa.Column("variant_ref", sa.String(length=255), nullable=True),
        sa.Column("price", sa.String(length=120), nullable=True),
        sa.Column("currency", sa.String(length=32), nullable=True),
        sa.Column("stock_state", sa.String(length=120), nullable=True),
        sa.Column("availability", sa.String(length=120), nullable=True),
        sa.Column("authority_state", sa.String(length=32), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_fact_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "offer_ref", name="uq_catalog_offers_workspace_offer"),
    )
    op.create_table(
        "catalog_media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("media_ref", sa.String(length=255), nullable=False),
        sa.Column("product_ref", sa.String(length=255), nullable=False),
        sa.Column("media_kind", sa.String(length=40), nullable=False, server_default="image"),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=False, server_default=""),
        sa.Column("ocr_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("visual_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("quality_state", sa.String(length=80), nullable=True),
        sa.Column("crop_state", sa.String(length=80), nullable=True),
        sa.Column("authority_state", sa.String(length=32), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_fact_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "media_ref", name="uq_catalog_media_workspace_media"),
    )
    op.create_table(
        "catalog_source_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("source_fact_id", sa.String(length=255), nullable=False),
        sa.Column("product_ref", sa.String(length=255), nullable=True),
        sa.Column("fact_type", sa.String(length=120), nullable=False),
        sa.Column("authority_state", sa.String(length=32), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "source_fact_id", name="uq_catalog_source_facts_workspace_fact"),
    )
    op.create_table(
        "catalog_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("conflict_ref", sa.String(length=255), nullable=False),
        sa.Column("product_ref", sa.String(length=255), nullable=False),
        sa.Column("field", sa.String(length=120), nullable=False),
        sa.Column("candidate_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "conflict_ref", name="uq_catalog_conflicts_workspace_conflict"),
    )
    op.create_table(
        "catalog_missing_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("product_ref", sa.String(length=255), nullable=False),
        sa.Column("field", sa.String(length=120), nullable=False),
        sa.Column("authority_state", sa.String(length=32), nullable=False, server_default="candidate"),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "product_ref", "field", name="uq_catalog_missing_workspace_product_field"),
    )
    for table in (
        "catalog_products",
        "catalog_variants",
        "catalog_offers",
        "catalog_media",
        "catalog_source_facts",
        "catalog_conflicts",
        "catalog_missing_fields",
    ):
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
    for table, columns in {
        "catalog_products": ["product_ref", "authority_state"],
        "catalog_variants": ["variant_ref", "product_ref", "authority_state"],
        "catalog_offers": ["offer_ref", "product_ref", "variant_ref", "authority_state"],
        "catalog_media": ["media_ref", "product_ref", "authority_state"],
        "catalog_source_facts": ["source_fact_id", "product_ref", "fact_type", "authority_state"],
        "catalog_conflicts": ["conflict_ref", "product_ref", "status"],
        "catalog_missing_fields": ["product_ref", "field", "authority_state"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table, columns in {
        "catalog_missing_fields": ["product_ref", "field", "authority_state"],
        "catalog_conflicts": ["conflict_ref", "product_ref", "status"],
        "catalog_source_facts": ["source_fact_id", "product_ref", "fact_type", "authority_state"],
        "catalog_media": ["media_ref", "product_ref", "authority_state"],
        "catalog_offers": ["offer_ref", "product_ref", "variant_ref", "authority_state"],
        "catalog_variants": ["variant_ref", "product_ref", "authority_state"],
        "catalog_products": ["product_ref", "authority_state"],
    }.items():
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
    for table in (
        "catalog_missing_fields",
        "catalog_conflicts",
        "catalog_source_facts",
        "catalog_media",
        "catalog_offers",
        "catalog_variants",
        "catalog_products",
    ):
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_table(table)
