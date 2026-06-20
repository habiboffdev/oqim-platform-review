"""Per-workspace daily token budget. Enforced before every LLM call."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class WorkspaceBudget(Base):
    """Daily token consumption record for a workspace.

    One row per (workspace_id, period_date). The BudgetService upserts this
    atomically before every LLM call and compares tokens_in_used +
    tokens_out_used against daily_cap_tokens. Exceeding the cap raises a
    BudgetExceededError which the action runtime surfaces to the owner.
    """

    __tablename__ = "workspace_budgets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "period_date", name="uq_budget_per_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period_date: Mapped[date] = mapped_column(Date, nullable=False)
    tokens_in_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_out_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    daily_cap_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=10_000_000
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
