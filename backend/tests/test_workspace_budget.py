from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace_budget import WorkspaceBudget
from app.modules.agent_runtime_v2.budget import (
    BudgetExceededError,
    BudgetService,
)


@pytest.mark.asyncio
async def test_budget_records_token_usage(db_session: AsyncSession, workspace) -> None:
    service = BudgetService(db_session)
    await service.record_usage(
        workspace_id=workspace.id,
        tokens_in=1000,
        tokens_out=500,
    )
    await db_session.flush()
    today = date.today()
    row = await service.get_or_create(workspace_id=workspace.id, period_date=today)
    assert row.tokens_in_used == 1000
    assert row.tokens_out_used == 500


@pytest.mark.asyncio
async def test_budget_raises_when_daily_cap_exceeded(
    db_session: AsyncSession, workspace
) -> None:
    service = BudgetService(db_session)
    await service.set_daily_cap(workspace_id=workspace.id, cap=500)
    await db_session.flush()
    await service.check_and_reserve(
        workspace_id=workspace.id, tokens_estimate=400
    )
    with pytest.raises(BudgetExceededError):
        await service.check_and_reserve(
            workspace_id=workspace.id, tokens_estimate=200
        )


@pytest.mark.asyncio
async def test_budget_isolation_per_workspace(
    db_session: AsyncSession, workspace, workspace_b
) -> None:
    service = BudgetService(db_session)
    await service.record_usage(workspace_id=workspace.id, tokens_in=999, tokens_out=0)
    await db_session.flush()
    other_row = await service.get_or_create(
        workspace_id=workspace_b.id, period_date=date.today()
    )
    assert other_row.tokens_in_used == 0


@pytest.mark.asyncio
async def test_is_exhausted_false_when_no_row(db_session: AsyncSession, workspace) -> None:
    # No budget row yet -> not exhausted, and the read must NOT create one.
    assert await BudgetService(db_session).is_exhausted(workspace_id=workspace.id) is False
    today = date.today()
    stmt = select(WorkspaceBudget).where(
        WorkspaceBudget.workspace_id == workspace.id,
        WorkspaceBudget.period_date == today,
    )
    assert (await db_session.execute(stmt)).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_is_exhausted_false_when_under_cap(db_session: AsyncSession, workspace) -> None:
    service = BudgetService(db_session)
    await service.set_daily_cap(workspace_id=workspace.id, cap=1000)
    await service.record_usage(workspace_id=workspace.id, tokens_in=400, tokens_out=400)
    await db_session.flush()
    assert await service.is_exhausted(workspace_id=workspace.id) is False


@pytest.mark.asyncio
async def test_is_exhausted_true_at_or_over_cap(db_session: AsyncSession, workspace) -> None:
    service = BudgetService(db_session)
    await service.set_daily_cap(workspace_id=workspace.id, cap=1000)
    await service.record_usage(workspace_id=workspace.id, tokens_in=600, tokens_out=400)
    await db_session.flush()
    assert await service.is_exhausted(workspace_id=workspace.id) is True
