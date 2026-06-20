from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class KnowledgeCollectionRecord(Base):
    __tablename__ = "knowledge_collections"
    __table_args__ = (
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "collection_id",
            name="uq_knowledge_collections_owner_collection",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class KnowledgeSourceRecord(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "source_id",
            name="uq_knowledge_sources_owner_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_kind: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    external_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    acl_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    freshness: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    ingestion_status: Mapped[str] = mapped_column(
        String(32), default="ready", nullable=False, index=True
    )
    raw_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class KnowledgeItemRecord(Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "item_id",
            name="uq_knowledge_items_owner_item",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    collection_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    authority_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_by_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class KnowledgeChunkRecord(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "chunk_id",
            name="uq_knowledge_chunks_owner_chunk",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(255), nullable=False)
    item_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    contextual_prefix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    citation: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_state: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        server_default="pending",
        nullable=False,
        index=True,
    )
    embedding_degraded_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(3072), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class KnowledgeCandidateRecord(Base):
    __tablename__ = "knowledge_candidates"
    __table_args__ = (
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "candidate_id",
            name="uq_knowledge_candidates_owner_candidate",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    proposed_kind: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    proposed_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    agent_control_action_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
