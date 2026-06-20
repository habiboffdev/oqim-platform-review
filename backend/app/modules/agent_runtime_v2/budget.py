"""Per-workspace token budget. Checked before LLM call, recorded after.

V1: daily cap, in-process check. Later phases: hourly + per-agent caps,
alerts at 80%/95%, webhook on breach.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.workspace_budget import WorkspaceBudget


class BudgetExceededError(RuntimeError):
    """Raised when a workspace would exceed its daily token cap."""


class BudgetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self, *, workspace_id: int, period_date: date | None = None
    ) -> WorkspaceBudget:
        period_date = period_date or date.today()
        stmt = select(WorkspaceBudget).where(
            WorkspaceBudget.workspace_id == workspace_id,
            WorkspaceBudget.period_date == period_date,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
        now = utc_now()
        row = WorkspaceBudget(
            workspace_id=workspace_id,
            period_date=period_date,
            tokens_in_used=0,
            tokens_out_used=0,
            daily_cap_tokens=10_000_000,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def is_exhausted(
        self, *, workspace_id: int, period_date: date | None = None
    ) -> bool:
        """Read-only: True if the workspace has already reached its daily cap.

        Unlike get_or_create/check_and_reserve this NEVER writes (no row insert,
        no reservation), so it is safe to call as a pre-flight gate on a
        read-only or not-yet-committed session. A missing row means the budget
        was never touched today -> not exhausted.
        """
        period_date = period_date or date.today()
        stmt = select(WorkspaceBudget).where(
            WorkspaceBudget.workspace_id == workspace_id,
            WorkspaceBudget.period_date == period_date,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        return (row.tokens_in_used + row.tokens_out_used) >= row.daily_cap_tokens

    async def set_daily_cap(self, *, workspace_id: int, cap: int) -> None:
        row = await self.get_or_create(workspace_id=workspace_id)
        row.daily_cap_tokens = cap
        row.updated_at = utc_now()

    async def check_and_reserve(
        self, *, workspace_id: int, tokens_estimate: int
    ) -> None:
        row = await self.get_or_create(workspace_id=workspace_id)
        total = row.tokens_in_used + row.tokens_out_used + tokens_estimate
        if total > row.daily_cap_tokens:
            raise BudgetExceededError(
                f"workspace {workspace_id} daily cap "
                f"({row.daily_cap_tokens}) would be exceeded "
                f"(current {row.tokens_in_used + row.tokens_out_used}, "
                f"requested {tokens_estimate})"
            )
        # Reserve the estimated tokens so subsequent calls see the reservation.
        row.tokens_in_used += tokens_estimate
        row.updated_at = utc_now()

    async def record_usage(
        self, *, workspace_id: int, tokens_in: int, tokens_out: int
    ) -> None:
        row = await self.get_or_create(workspace_id=workspace_id)
        row.tokens_in_used += tokens_in
        row.tokens_out_used += tokens_out
        row.updated_at = utc_now()
