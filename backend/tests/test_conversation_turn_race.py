"""Active-turn insert race: concurrent inbound must append to the winner, not crash."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.conversation_turn_session import ConversationTurnSession
from app.models.message import Message, SenderType
from app.modules.conversation_turns.service import ConversationTurnSessionService

pytestmark = pytest.mark.asyncio


async def _msg(db_session, conversation, text: str, tg_id: int) -> Message:
    message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content=text,
        telegram_message_id=tg_id,
        external_message_id=str(tg_id),
    )
    db_session.add(message)
    await db_session.flush()
    return message


async def test_duplicate_active_turn_insert_recovers_to_winner(
    db_session, workspace, conversation, customer, agent, monkeypatch
):
    service = ConversationTurnSessionService(db_session)
    msg1 = await _msg(db_session, conversation, "Assalomu alaykum", 9001)
    winner = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=msg1,
        agent_id=agent.id,
    )

    # Simulate the race: the second message's pre-check does not see the
    # winner row yet, so the service tries to INSERT a duplicate active turn.
    real_load = ConversationTurnSessionService._load_active
    calls = {"n": 0}

    async def racy_load(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # racer's stale view
        return await real_load(self, **kwargs)

    monkeypatch.setattr(ConversationTurnSessionService, "_load_active", racy_load)

    msg2 = await _msg(db_session, conversation, "Yaxshimisiz", 9002)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=msg2,
        agent_id=agent.id,
    )

    # No crash, and the message landed on the winner turn.
    assert turn.id == winner.id
    assert int(turn.latest_customer_message_id) == msg2.id


async def test_new_message_appends_to_continued_turn_instead_of_duplicating(
    db_session, workspace, conversation, customer, agent
):
    """2026-06-11 crash-loop root cause: a turn parked in 'continued' was
    invisible to _load_active, so the next customer message INSERTed a second
    active turn for the same conversation; leasing the pair then violated
    uq_conversation_turn_sessions_active out of run_once forever."""
    service = ConversationTurnSessionService(db_session)
    msg1 = await _msg(db_session, conversation, "Sizda qanday kurslar bor", 9101)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=msg1,
        agent_id=agent.id,
    )
    # Park the turn exactly as a mid-run customer message does: the run
    # finalizes having observed an older revision -> 'continued'.
    await service.finalize_run_observation(
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:continued-hole",
        observed_revision=0,
    )
    await db_session.refresh(turn)
    assert turn.state == "continued"

    msg2 = await _msg(db_session, conversation, "Narxi qancha", 9102)
    appended = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=msg2,
        agent_id=agent.id,
    )

    # Same row reused — never a duplicate.
    assert appended.id == turn.id
    rows = (
        await db_session.execute(
            select(ConversationTurnSession).where(
                ConversationTurnSession.conversation_id == conversation.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_append_to_continued_turn_keeps_burst_window_and_stays_leasable(
    db_session, workspace, conversation, customer, agent
):
    """The successor dispatch must carry the UNANSWERED earlier message too:
    first_customer_message_id stays at the original bubble, the revision bumps,
    and the turn is leasable once the coalescing window passes."""
    from datetime import UTC, datetime, timedelta

    service = ConversationTurnSessionService(db_session)
    msg1 = await _msg(db_session, conversation, "salom", 9201)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=msg1,
        agent_id=agent.id,
    )
    await service.finalize_run_observation(
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:continued-burst",
        observed_revision=0,
    )
    msg2 = await _msg(db_session, conversation, "kurs narxi qancha", 9202)
    appended = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=msg2,
        agent_id=agent.id,
    )

    assert appended.id == turn.id
    assert int(appended.first_customer_message_id) == msg1.id
    assert int(appended.latest_customer_message_id) == msg2.id
    assert int(appended.turn_revision) == 2
    assert appended.state == "continued"
    assert appended.stale_reason == "turn_revision_not_observed"

    # leasable once the burst window has elapsed
    appended.latest_customer_message_at = datetime.now(UTC) - timedelta(seconds=10)
    await db_session.flush()
    leases = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert [lease.turn_session_id for lease in leases] == [turn.id]


async def test_db_rejects_second_nonterminal_turn_for_same_conversation(
    db_session, workspace, conversation, customer, agent
):
    """The invariant is DB-enforced: at most ONE non-terminal turn per
    (workspace, conversation, agent) — 'continued' included. No application
    writer (now or future) can recreate the duplicate-active-turn class."""
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    from app.db.base import utc_now

    service = ConversationTurnSessionService(db_session)
    msg1 = await _msg(db_session, conversation, "salom", 9301)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=msg1,
        agent_id=agent.id,
    )
    turn.state = "continued"
    await db_session.flush()

    duplicate = ConversationTurnSession(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        channel="telegram_dm",
        state="open",
        turn_key=f"conversation:{conversation.id}:agent:{agent.id}:dup",
        turn_revision=1,
        first_customer_message_id=msg1.id,
        latest_customer_message_id=msg1.id,
        latest_customer_message_at=utc_now(),
    )
    with pytest.raises(SAIntegrityError):
        async with db_session.begin_nested():
            db_session.add(duplicate)
            await db_session.flush()
