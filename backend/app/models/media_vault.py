"""Reusable per-workspace media assets — the owner's media vault (spike #439).

The owner uploads an intro video / photo / document ONCE and refers to it by a
stable `handle`. The seller sends it via the existing talk.send_media path; the
outbound-media resolver treats a MediaVaultRecord as a third source after catalog
media and source-media facts.

For the spike the asset is addressed by `cdn_url` (re-fetched each send). The
Telegram-cloud "upload once, reuse" path (sidecar vault.store/vault.send) fills
the nullable pointer columns (vault_peer/vault_message_id/document_id/
access_hash/file_reference) so future sends reuse the cloud copy with zero byte
re-upload — id/access_hash are stable but file_reference expires and is re-fetched
just-in-time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class MediaVaultRecord(Base):
    """One reusable media asset, addressable by (workspace_id, handle)."""

    __tablename__ = "media_vault_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "handle", name="uq_media_vault_workspace_handle"),
        CheckConstraint(
            "cdn_url IS NOT NULL OR vault_message_id IS NOT NULL",
            name="ck_media_vault_url_or_pointer",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )

    handle: Mapped[str] = mapped_column(String(120), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False, default="photo")
    cdn_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Telegram-cloud pointer (populated only when the sidecar vault path lands).
    vault_peer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vault_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    document_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    access_hash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_reference: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[str] = mapped_column(String(64), default="owner", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
