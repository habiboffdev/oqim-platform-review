"""BindTokenService (#451): mint/revoke-prior, atomic lane-scoped redeem, unbind."""

import pytest
from sqlalchemy import select

from app.models.owner_bind_token import OwnerBindEvent, OwnerBindToken
from app.models.workspace import Workspace
from app.modules.telegram_control_bot.bind_token_service import BindTokenService

pytestmark = pytest.mark.asyncio


async def test_mint_revokes_prior_and_audits(db_session, workspace):
    svc = BindTokenService(db_session)
    first = await svc.mint(workspace_id=workspace.id)
    second = await svc.mint(workspace_id=workspace.id)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(OwnerBindToken).where(OwnerBindToken.workspace_id == workspace.id)
        )
    ).scalars().all()
    live = [r for r in rows if r.used_at is None]
    assert len(live) == 1 and live[0].token == second
    assert first != second and len(second) >= 32

    mints = (
        await db_session.execute(
            select(OwnerBindEvent).where(OwnerBindEvent.event_type == "mint")
        )
    ).scalars().all()
    assert len(mints) == 2


async def test_redeem_binds_once_then_rejects(db_session, workspace):
    svc = BindTokenService(db_session)
    token = await svc.mint(workspace_id=workspace.id)
    await db_session.flush()

    assert await svc.redeem(token=token, bound_workspace_id=workspace.id, chat_id=555) is True
    ws = await db_session.get(Workspace, workspace.id)
    assert ws.owner_control_chat_id == 555
    # the now-used token cannot bind again
    assert await svc.redeem(token=token, bound_workspace_id=workspace.id, chat_id=999) is False
    ws = await db_session.get(Workspace, workspace.id)
    assert ws.owner_control_chat_id == 555


async def test_redeem_rejects_wrong_lane(db_session, workspace, workspace_b):
    svc = BindTokenService(db_session)
    token = await svc.mint(workspace_id=workspace.id)
    await db_session.flush()
    # token scoped to `workspace`, presented on workspace_b's lane -> reject
    assert await svc.redeem(token=token, bound_workspace_id=workspace_b.id, chat_id=7) is False


async def test_redeem_none_lane_never_binds(db_session, workspace):
    svc = BindTokenService(db_session)
    token = await svc.mint(workspace_id=workspace.id)
    await db_session.flush()
    assert await svc.redeem(token=token, bound_workspace_id=None, chat_id=7) is False


async def test_unbind_clears_and_revokes(db_session, workspace):
    svc = BindTokenService(db_session)
    token = await svc.mint(workspace_id=workspace.id)
    await db_session.flush()
    await svc.redeem(token=token, bound_workspace_id=workspace.id, chat_id=555)
    await svc.unbind(workspace_id=workspace.id)
    ws = await db_session.get(Workspace, workspace.id)
    assert ws.owner_control_chat_id is None


async def test_unbind_expires_pending_owner_proposals(db_session, workspace):
    from app.models.commercial_action import CommercialActionProposalRecord

    db_session.add(
        CommercialActionProposalRecord(
            proposal_id="p1", workspace_id=workspace.id, conversation_id=0, customer_id=0,
            action_type="agent.update_owner_config", lifecycle_state="waiting_approval",
            execution_mode="suggest_only", risk_level="medium", requires_approval=True,
            priority="medium", confidence=1.0, reason_code="owner_config_edit",
            source_refs=[], payload={}, idempotency_key="p1",
        )
    )
    await db_session.flush()
    await BindTokenService(db_session).unbind(workspace_id=workspace.id)
    row = (
        await db_session.execute(select(CommercialActionProposalRecord))
    ).scalar_one()
    assert row.lifecycle_state == "expired"
