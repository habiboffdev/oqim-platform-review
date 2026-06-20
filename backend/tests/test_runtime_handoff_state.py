"""Handoff status truth: the runtime injects the REAL recorded handoff state
into conversation_state so the agent can never invent escalation progress
(spec 2026-06-11-honest-seller-loop)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db.base import utc_now
from app.modules.agent_runtime_v2.runtime_service import _open_handoff_state
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository

pytestmark = pytest.mark.asyncio


async def _seed_handoff(
    db_session,
    workspace,
    *,
    conversation_id=11,
    lifecycle_state="proposed",
    created_minutes_ago=5,
    kind="lead",
):
    repository = CommercialSpineRepository(db_session)
    proposal = CommercialActionProposal(
        proposal_id=f"owner_task:test-{kind}-{lifecycle_state}-{created_minutes_ago}",
        workspace_id=workspace.id,
        conversation_id=conversation_id,
        customer_id=1,
        action_type="create_business_task",
        lifecycle_state=lifecycle_state,
        execution_mode="suggest_only",
        risk_level="low",
        requires_approval=True,
        executor_runtime="owner_task",
        priority="high",
        confidence=1.0,
        reason_code="agent_created_owner_task",
        source_refs=[f"handoff:{kind}"],
        payload={"owner_task": {"title": "t", "detail": "d", "reason": "d"}},
        idempotency_key=f"test:{kind}:{lifecycle_state}:{created_minutes_ago}",
        correlation_id="test",
    )
    await repository.persist_action_proposal(proposal)
    # Backdate created_at directly for age/staleness assertions.
    from sqlalchemy import update

    from app.models.commercial_action import CommercialActionProposalRecord

    await db_session.execute(
        update(CommercialActionProposalRecord)
        .where(CommercialActionProposalRecord.proposal_id == proposal.proposal_id)
        .values(created_at=utc_now() - timedelta(minutes=created_minutes_ago))
    )
    await db_session.flush()


async def test_queued_handoff_is_reported_queued(db_session, workspace):
    await _seed_handoff(db_session, workspace, lifecycle_state="proposed")
    state = await _open_handoff_state(
        db_session, workspace_id=workspace.id, conversation_id=11
    )
    assert len(state) == 1
    assert state[0]["kind"] == "lead"
    assert state[0]["state"] == "queued"
    assert state[0]["age_minutes"] in (4, 5)
    assert state[0]["stale"] is False


async def test_acknowledged_and_stale_states(db_session, workspace):
    await _seed_handoff(
        db_session, workspace, lifecycle_state="approved", created_minutes_ago=10
    )
    await _seed_handoff(
        db_session,
        workspace,
        kind="complaint",
        lifecycle_state="proposed",
        created_minutes_ago=90,
    )
    state = await _open_handoff_state(
        db_session, workspace_id=workspace.id, conversation_id=11
    )
    by_kind = {item["kind"]: item for item in state}
    assert by_kind["lead"]["state"] == "acknowledged"
    assert by_kind["complaint"]["state"] == "queued"
    assert by_kind["complaint"]["stale"] is True


async def test_other_conversation_and_non_handoff_excluded(db_session, workspace):
    await _seed_handoff(db_session, workspace, conversation_id=99)
    state = await _open_handoff_state(
        db_session, workspace_id=workspace.id, conversation_id=11
    )
    assert state == []
