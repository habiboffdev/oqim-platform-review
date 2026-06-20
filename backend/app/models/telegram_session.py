from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id"), unique=True, nullable=False
    )
    session_data: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str | None] = mapped_column(String(16), nullable=True)
    client_profile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
