from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class CatalogProductRecord(Base):
    __tablename__ = "catalog_products"
    __table_args__ = (
        UniqueConstraint("workspace_id", "product_ref", name="uq_catalog_products_workspace_product"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    product_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    authority_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_fact_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    freshness: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class CatalogVariantRecord(Base):
    __tablename__ = "catalog_variants"
    __table_args__ = (
        UniqueConstraint("workspace_id", "variant_ref", name="uq_catalog_variants_workspace_variant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    variant_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    authority_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_fact_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class CatalogOfferRecord(Base):
    __tablename__ = "catalog_offers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "offer_ref", name="uq_catalog_offers_workspace_offer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    offer_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    variant_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    price: Mapped[str | None] = mapped_column(String(120), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stock_state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    availability: Mapped[str | None] = mapped_column(String(120), nullable=True)
    authority_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_fact_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class CatalogMediaRecord(Base):
    __tablename__ = "catalog_media"
    __table_args__ = (
        UniqueConstraint("workspace_id", "media_ref", name="uq_catalog_media_workspace_media"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    media_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    media_kind: Mapped[str] = mapped_column(String(40), default="image", nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ocr_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visual_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    quality_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    crop_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    authority_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_fact_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class CatalogSourceFactRecord(Base):
    __tablename__ = "catalog_source_facts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_fact_id", name="uq_catalog_source_facts_workspace_fact"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_fact_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    fact_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    authority_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class CatalogConflictRecord(Base):
    __tablename__ = "catalog_conflicts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "conflict_ref", name="uq_catalog_conflicts_workspace_conflict"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    conflict_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    field: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_values: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class CatalogMissingFieldRecord(Base):
    __tablename__ = "catalog_missing_fields"
    __table_args__ = (
        UniqueConstraint("workspace_id", "product_ref", "field", name="uq_catalog_missing_workspace_product_field"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    product_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    field: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    authority_state: Mapped[str] = mapped_column(String(32), default="candidate", nullable=False, index=True)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
