"""TurnLifecycle coordinator — the single owner of turn.state writes (#427 S8)."""
from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from app.modules.conversation_turns.lifecycle import TurnLifecycle
from app.modules.conversation_turns.turn_state import TurnState
from app.modules.hermes_runtime.contracts import HermesRunInput, HermesRunPatch
from app.modules.hermes_runtime.service import HermesRunService

pytestmark = pytest.mark.asyncio


async def _open_turn(db_session, workspace, conversation, customer, agent, *, state="open"):
    """Build a VALID turn via the real service (it sets the NOT-NULL-no-default
    columns turn_key / first_customer_message_id / latest_customer_message_* that a
    bare constructor would violate), then force the desired starting state."""
    from app.models.message import Message
    from app.modules.conversation_turns.service import ConversationTurnSessionService

    msg = Message(conversation_id=conversation.id, sender_type="customer", content="hi")
    db_session.add(msg)
    await db_session.flush()
    turn = await ConversationTurnSessionService(db_session).append_customer_message(
        workspace_id=workspace.id, conversation=conversation, customer=customer,
        message=msg, agent_id=agent.id,
    )
    if turn.state != state:
        turn.state = state
        await db_session.flush()
    return turn


async def test_transition_sets_state_and_flushes(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="starting")
    await TurnLifecycle(db_session).transition(turn, TurnState.RUNNING)
    assert turn.state == "running"
    assert turn.updated_at is not None
    assert turn.completed_at is None  # terminal-only write must NOT fire for a non-terminal to


async def test_transition_terminal_sets_completed_at(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="finalizing")
    assert turn.completed_at is None
    await TurnLifecycle(db_session).transition(turn, TurnState.COMPLETED)
    assert turn.state == "completed"
    assert turn.completed_at is not None


async def test_transition_sets_and_clears_stale_reason(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="continued")
    # explicit None clears it; omitting the kwarg leaves it untouched
    await TurnLifecycle(db_session).transition(turn, TurnState.STARTING, stale_reason=None)
    assert turn.stale_reason is None


async def test_transition_omitting_stale_reason_leaves_it_untouched(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="starting")
    turn.stale_reason = "preexisting"
    await db_session.flush()
    # omitting the kwarg (the _UNSET sentinel branch) must NOT clear stale_reason
    await TurnLifecycle(db_session).transition(turn, TurnState.RUNNING)
    assert turn.stale_reason == "preexisting"


async def test_guard_logs_and_allows_unexpected_edge(
    db_session, workspace, conversation, customer, agent, caplog
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="completed")
    with caplog.at_level(logging.WARNING, logger="oqim_business.turn.lifecycle"):
        await TurnLifecycle(db_session).transition(turn, TurnState.RUNNING)  # completed->running: illegal
    assert turn.state == "running"  # applied anyway (log-and-allow)
    assert any("unexpected" in r.getMessage().lower() for r in caplog.records)


async def test_guard_allowed_edge_logs_nothing(
    db_session, workspace, conversation, customer, agent, caplog
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="starting")
    with caplog.at_level(logging.WARNING, logger="oqim_business.turn.lifecycle"):
        await TurnLifecycle(db_session).transition(turn, TurnState.RUNNING)
    assert not any("unexpected" in r.getMessage().lower() for r in caplog.records)


async def test_lease_clears_stale_reason(db_session, workspace, conversation, customer, agent):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="continued")
    turn.stale_reason = "turn_revision_not_observed"
    await db_session.flush()
    await TurnLifecycle(db_session).lease(turn)
    assert turn.state == "starting"
    assert turn.stale_reason is None


async def test_reopen_for_successor_clears_run_ids_and_sets_reason(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="finalizing")
    turn.active_hermes_run_id = "r1"
    turn.active_engine_run_id = "e1"
    await db_session.flush()
    await TurnLifecycle(db_session).reopen_for_successor(turn)
    assert turn.state == "continued"
    assert turn.active_hermes_run_id is None and turn.active_engine_run_id is None
    assert turn.stale_reason == "customer_message_after_finalization"


async def test_release_failed_quarantines_on_third_strike(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="starting")
    turn.failed_dispatch_count = 2
    await db_session.flush()
    await TurnLifecycle(db_session).release_failed(turn, reason="dispatch_failed")
    assert turn.state == "quarantined"
    assert turn.completed_at is not None
    assert turn.failed_dispatch_count == 3
    assert turn.stale_reason == "dispatch_poisoned"


async def test_release_failed_reopens_below_threshold(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="starting")
    turn.failed_dispatch_count = 0
    await db_session.flush()
    await TurnLifecycle(db_session).release_failed(turn, reason="dispatch_failed")
    assert turn.state == "open"
    assert turn.failed_dispatch_count == 1


async def test_complete_for_agent_message_clears_run_ids_and_completes(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="running")
    turn.active_hermes_run_id = "r1"
    turn.active_engine_run_id = "e1"
    await db_session.flush()
    await TurnLifecycle(db_session).complete_for_agent_message(turn, reason="agent_message_sent")
    assert turn.state == "completed" and turn.completed_at is not None
    assert turn.active_hermes_run_id is None and turn.active_engine_run_id is None
    assert turn.stale_reason == "agent_message_sent"


async def test_complete_for_trigger_clears_run_ids_and_completes(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="open")
    turn.active_hermes_run_id = "r1"
    turn.active_engine_run_id = "e1"
    await db_session.flush()
    await TurnLifecycle(db_session).complete_for_trigger(turn, reason="trigger_superseded")
    assert turn.state == "completed" and turn.completed_at is not None
    assert turn.active_hermes_run_id is None and turn.active_engine_run_id is None
    assert turn.stale_reason == "trigger_superseded"


async def test_complete_pre_dispatch_completes_without_touching_run_ids(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="starting")
    await TurnLifecycle(db_session).complete_pre_dispatch(turn, reason="pre_dispatch_failed")
    assert turn.state == "completed" and turn.completed_at is not None
    assert turn.stale_reason == "pre_dispatch_failed"


async def test_reclaim_lease_reopens_with_lease_expired_reason(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="starting")
    await TurnLifecycle(db_session).reclaim_lease(turn)
    assert turn.state == "open"
    assert turn.stale_reason == "turn_lease_expired"


async def test_complete_for_agent_message_deferred_flush_completes_two_turns(
    db_session, workspace, conversation, customer, agent
):
    from datetime import UTC, datetime

    from app.models.conversation import Conversation

    turn1 = await _open_turn(db_session, workspace, conversation, customer, agent, state="open")
    conv2 = Conversation(
        workspace_id=workspace.id, customer_id=customer.id,
        telegram_chat_id=987654321, pipeline_stage="new",
        last_message_at=datetime.now(UTC),
    )
    db_session.add(conv2)
    await db_session.flush()
    turn2 = await _open_turn(db_session, workspace, conv2, customer, agent, state="open")

    lc = TurnLifecycle(db_session)
    for t in (turn1, turn2):
        await lc.complete_for_agent_message(t, reason="agent_message_sent", flush=False)
    await db_session.flush()  # single trailing flush, like the real bulk loop

    assert turn1.state == "completed" and turn2.state == "completed"
    assert turn1.completed_at is not None and turn2.completed_at is not None


async def _run(db_session, workspace, agent):
    svc = HermesRunService(db_session)
    run = await svc.start_or_dedupe(HermesRunInput(
        workspace_id=workspace.id, agent_id=agent.id, trigger_id="t", event_id="e",
        idempotency_key=f"lc-{uuid4().hex}", correlation_id="c"))
    return svc, run


async def test_begin_run_marks_run_running_and_writes_engine_id(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="starting")
    svc, run = await _run(db_session, workspace, agent)
    # Pre-set a real engine id on the run. mark_running passes engine_run_id=None, which
    # patch() drops via exclude_none, so the pre-set value SURVIVES into the snapshot —
    # this makes the engine-id assertion meaningful (not snap==snap tautology).
    await svc.patch(run.run_id, HermesRunPatch(engine_run_id="eng-xyz"))
    snap = await TurnLifecycle(db_session).begin_run(turn, run.run_id, agent_id=agent.id)
    assert turn.state == "running"
    assert turn.agent_id == agent.id
    assert turn.started_at is not None
    assert turn.active_hermes_run_id == run.run_id
    assert turn.active_engine_run_id == snap.engine_run_id == "eng-xyz"


async def test_complete_finalized_completes_run_then_turn(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="finalizing")
    turn.finalized_revision = 1
    await db_session.flush()
    _svc, run = await _run(db_session, workspace, agent)
    ok = await TurnLifecycle(db_session).complete_finalized(
        turn, run_id=run.run_id, run_patch=HermesRunPatch(completed_at=None), finalized_revision=1)
    assert ok is True
    assert turn.state == "completed" and turn.completed_at is not None


async def test_complete_finalized_returns_false_when_superseded(
    db_session, workspace, conversation, customer, agent
):
    # A newer customer message bumped turn_revision past finalized_revision: the
    # crash-loop guard must REFUSE to complete (and leave the turn non-completed).
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="finalizing")
    turn.finalized_revision = 1
    turn.turn_revision = 2
    await db_session.flush()
    _svc, run = await _run(db_session, workspace, agent)
    ok = await TurnLifecycle(db_session).complete_finalized(
        turn, run_id=run.run_id, run_patch=HermesRunPatch(completed_at=None), finalized_revision=1)
    assert ok is False
    assert turn.state == "finalizing"


async def test_complete_finalized_returns_false_when_not_finalizing(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="running")
    turn.finalized_revision = 1
    await db_session.flush()
    _svc, run = await _run(db_session, workspace, agent)
    ok = await TurnLifecycle(db_session).complete_finalized(
        turn, run_id=run.run_id, run_patch=HermesRunPatch(completed_at=None), finalized_revision=1)
    assert ok is False
    assert turn.state == "running"


async def test_complete_finalized_returns_false_when_no_revision(
    db_session, workspace, conversation, customer, agent
):
    turn = await _open_turn(db_session, workspace, conversation, customer, agent, state="finalizing")
    _svc, run = await _run(db_session, workspace, agent)
    ok = await TurnLifecycle(db_session).complete_finalized(
        turn, run_id=run.run_id, run_patch=HermesRunPatch(completed_at=None), finalized_revision=None)
    assert ok is False
    assert turn.state == "finalizing"  # run completed, but turn not transitioned
