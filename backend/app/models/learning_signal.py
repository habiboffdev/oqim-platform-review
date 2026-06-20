from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class LearningSignal(Base):
    __tablename__ = "learning_signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False)  # voice_correction, classification_correction, kb_gap
    context: Mapped[str] = mapped_column(Text, nullable=False)
    correction: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list | None] = mapped_column(Vector(3072), nullable=True)
    indexing_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
