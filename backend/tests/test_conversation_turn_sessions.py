from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.models.agent_session import AgentSessionEvent
from app.models.conversation_turn_session import ConversationTurnSession
from app.models.hermes_run import HermesRun, HermesRunEvent
from app.models.message import Message, SenderType
from app.modules.conversation_turns.active_runs import active_turn_run_registry
from app.modules.conversation_turns.service import (
    NON_TERMINAL_TURN_STATES,
    ConversationTurnSessionService,
)

pytestmark = pytest.mark.asyncio


class _FakeSteerAgent:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def steer(self, payload: str) -> bool:
        self.payloads.append(payload)
        return True


async def _customer_message(db_session, conversation, content: str, *, telegram_id: int | None = None) -> Message:
    message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content=content,
        telegram_message_id=telegram_id,
        created_at=datetime.now(UTC),
    )
    db_session.add(message)
    await db_session.flush()
    return message


async def test_burst_message_defers_to_successor_not_live_steer(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    service = ConversationTurnSessionService(db_session)
    first = await _customer_message(db_session, conversation, "Assalomu alaykum", telegram_id=1442)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=first,
        agent_id=agent.id,
    )
    await service.mark_running(
        turn_session_id=turn.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:burst-active",
        engine_run_id="engine:1",
    )
    db_session.add(
        HermesRun(
            run_id="hermes_run:burst-active",
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            lane="fast_interactive",
            run_mode="reply",
            trigger_type="conversation_turn",
            trigger_id=f"turn:{turn.id}:rev:1",
            conversation_id=conversation.id,
            correlation_id="test:burst-active",
            idempotency_key=f"test:burst-active:{turn.id}",
            state="running",
            source_refs=[],
            input_summary="Assalomu alaykum",
            details={},
            payload={},
        )
    )
    await db_session.flush()

    fake_agent = _FakeSteerAgent()
    handle = active_turn_run_registry.register(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:burst-active",
        agent=fake_agent,
        turn_revision_start=1,
    )
    try:
        second = await _customer_message(db_session, conversation, "Starter coins narxi qancha", telegram_id=1443)
        updated = await service.append_customer_message(
            workspace_id=workspace.id,
            conversation=conversation,
            customer=customer,
            message=second,
            agent_id=agent.id,
        )
    finally:
        active_turn_run_registry.unregister(handle)

    assert updated.id == turn.id
    assert updated.turn_revision == 2
    assert updated.latest_customer_message_id == second.id
    assert updated.steer_count == 1
    # The live Hermes loop is NEVER injected: with terminal talk tools a
    # steered payload drains into a dead tool-result and is silently lost
    # (live repro: run 33 / msg 136, 2026-06-09). The burst message defers.
    assert fake_agent.payloads == []

    # The handle reports only the revision the model actually saw, so
    # finalize dispatches a successor turn that carries the burst message.
    details = handle.finish()
    assert details["observed_revision"] == 1
    assert details["latest_known_revision"] == 2
    assert details["steer_deferred_count"] == 1
    finalization = await service.finalize_run_observation(
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:burst-active",
        observed_revision=details["observed_revision"],
        pending_steer_count=details["pending_steer_count"],
    )
    assert finalization.needs_successor is True
    assert finalization.can_deliver is False
    refreshed = await db_session.get(ConversationTurnSession, turn.id)
    assert refreshed.state == "continued"
    assert refreshed.stale_reason == "turn_revision_not_observed"

    events = (
        await db_session.execute(
            select(HermesRunEvent).where(
                HermesRunEvent.run_id == "hermes_run:burst-active",
                HermesRunEvent.tool_name == "turn.steer",
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].payload["turn_revision"] == 2
    assert events[0].payload["deferred_to_successor"] is True
    assert events[0].tool_state == "deferred"


async def test_appending_message_after_finalizing_reopens_turn_for_successor(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    service = ConversationTurnSessionService(db_session)
    first = await _customer_message(db_session, conversation, "hello", telegram_id=1501)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=first,
        agent_id=agent.id,
    )
    db_session.add(
        HermesRun(
            run_id="hermes_run:finalizing-successor",
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            lane="fast_interactive",
            run_mode="reply",
            trigger_type="conversation_turn",
            trigger_id=f"turn:{turn.id}:rev:1",
            conversation_id=conversation.id,
            correlation_id="test:finalizing-successor",
            idempotency_key=f"test:finalizing-successor:{turn.id}",
            state="running",
            source_refs=[],
            input_summary="hello",
            details={},
            payload={},
        )
    )
    await db_session.flush()
    finalized = await service.finalize_run_observation(
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:finalizing-successor",
        observed_revision=1,
    )
    assert finalized.can_deliver is True
    assert turn.state == "finalizing"

    second = await _customer_message(db_session, conversation, "how are uu", telegram_id=1502)
    updated = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=second,
        agent_id=agent.id,
    )

    assert updated.id == turn.id
    assert updated.state == "continued"
    assert updated.turn_revision == 2
    assert updated.latest_customer_message_id == second.id
    assert updated.stale_reason == "customer_message_after_finalization"


async def test_finalization_continues_when_run_did_not_observe_latest_revision(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    service = ConversationTurnSessionService(db_session)
    first = await _customer_message(db_session, conversation, "Salom")
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=first,
        agent_id=agent.id,
    )
    await service.mark_running(
        turn_session_id=turn.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:needs-successor",
    )
    second = await _customer_message(db_session, conversation, "Narxi qancha?")
    await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=second,
        agent_id=agent.id,
    )

    result = await service.finalize_run_observation(
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:needs-successor",
        observed_revision=1,
        pending_steer_count=0,
    )

    assert result.can_deliver is False
    assert result.needs_successor is True
    assert result.latest_customer_message_id == second.id
    stored = await db_session.get(ConversationTurnSession, turn.id)
    assert stored is not None
    assert stored.state == "continued"
    assert stored.stale_reason == "turn_revision_not_observed"


async def test_finalization_allows_delivery_when_latest_revision_was_observed(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    service = ConversationTurnSessionService(db_session)
    message = await _customer_message(db_session, conversation, "Starter narxi?")
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=message,
        agent_id=agent.id,
    )
    await service.mark_running(
        turn_session_id=turn.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:observed",
    )

    result = await service.finalize_run_observation(
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:observed",
        observed_revision=1,
        pending_steer_count=0,
    )

    assert result.can_deliver is True
    assert result.finalized_revision == 1
    stored = await db_session.get(ConversationTurnSession, turn.id)
    assert stored is not None
    assert stored.state == "finalizing"
    assert stored.finalized_revision == 1


async def test_complete_finalized_turn_marks_finalizing_turn_completed(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    """S1 (#420): turn completion is owned by the turn-session service (folded from
    the dispatcher's private copy). A finalizing turn whose ``finalized_revision``
    matches transitions to ``completed``."""
    service = ConversationTurnSessionService(db_session)
    message = await _customer_message(db_session, conversation, "Starter narxi?")
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=message,
        agent_id=agent.id,
    )
    await service.mark_running(
        turn_session_id=turn.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:complete",
    )
    result = await service.finalize_run_observation(
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:complete",
        observed_revision=1,
        pending_steer_count=0,
    )
    assert result.finalized_revision == 1

    completed = await service.complete_finalized_turn(
        turn_session_id=turn.id,
        finalized_revision=result.finalized_revision,
    )
    assert completed is True
    stored = await db_session.get(ConversationTurnSession, turn.id)
    assert stored.state == "completed"
    assert stored.completed_at is not None


async def test_complete_finalized_turn_noop_when_not_finalizing(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    """Guard preserved from the dispatcher copy: a turn that is not in
    ``finalizing`` is never force-completed (protects the crash-loop fix)."""
    service = ConversationTurnSessionService(db_session)
    message = await _customer_message(db_session, conversation, "Salom")
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=message,
        agent_id=agent.id,
    )
    await service.mark_running(
        turn_session_id=turn.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:running",
    )

    completed = await service.complete_finalized_turn(
        turn_session_id=turn.id,
        finalized_revision=1,
    )
    assert completed is False
    stored = await db_session.get(ConversationTurnSession, turn.id)
    assert stored.state != "completed"


async def test_finalization_refreshes_turn_after_concurrent_message_update(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    service = ConversationTurnSessionService(db_session)
    first = await _customer_message(db_session, conversation, "Salom")
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=first,
        agent_id=agent.id,
    )
    await service.mark_running(
        turn_session_id=turn.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:refresh-finalize",
    )
    second = await _customer_message(db_session, conversation, "Starter narxi?")
    await db_session.execute(
        update(ConversationTurnSession)
        .where(ConversationTurnSession.id == turn.id)
        .values(
            turn_revision=2,
            latest_customer_message_id=second.id,
            latest_customer_message_at=second.created_at,
        )
        .execution_options(synchronize_session=False)
    )
    assert turn.turn_revision == 1

    result = await service.finalize_run_observation(
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:refresh-finalize",
        observed_revision=2,
        pending_steer_count=0,
    )

    assert result.can_deliver is True
    assert result.turn_revision == 2
    assert result.finalized_revision == 2
    assert result.latest_customer_message_id == second.id


async def test_turn_session_append_records_agent_session_event(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    service = ConversationTurnSessionService(db_session)
    first = await _customer_message(db_session, conversation, "Assalomu alaykum", telegram_id=1801)

    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=first,
        agent_id=agent.id,
    )

    events = (
        await db_session.execute(
            select(AgentSessionEvent)
            .where(
                AgentSessionEvent.conversation_id == conversation.id,
                AgentSessionEvent.agent_id == agent.id,
            )
            .order_by(AgentSessionEvent.sequence)
        )
    ).scalars().all()
    assert [event.event_type for event in events] == ["customer_message"]
    assert events[0].message_id == first.id
    assert events[0].payload["turn_session_id"] == turn.id
    assert events[0].payload["turn_revision"] == 1


async def test_turn_steering_records_agent_session_event(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    service = ConversationTurnSessionService(db_session)
    first = await _customer_message(db_session, conversation, "salom", telegram_id=1901)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=first,
        agent_id=agent.id,
    )
    await service.mark_running(
        turn_session_id=turn.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:steer-session-event",
    )
    db_session.add(
        HermesRun(
            run_id="hermes_run:steer-session-event",
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            lane="fast_interactive",
            run_mode="reply",
            trigger_type="conversation_turn",
            trigger_id=f"turn:{turn.id}:rev:1",
            conversation_id=conversation.id,
            correlation_id="test:steer-session-event",
            idempotency_key=f"test:steer-session-event:{turn.id}",
            state="running",
            source_refs=[],
            input_summary="salom",
            details={},
            payload={},
        )
    )
    await db_session.flush()
    fake_agent = _FakeSteerAgent()
    handle = active_turn_run_registry.register(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        turn_session_id=turn.id,
        hermes_run_id="hermes_run:steer-session-event",
        agent=fake_agent,
        turn_revision_start=1,
    )
    try:
        second = await _customer_message(db_session, conversation, "narxi qancha", telegram_id=1902)
        await service.append_customer_message(
            workspace_id=workspace.id,
            conversation=conversation,
            customer=customer,
            message=second,
            agent_id=agent.id,
        )
    finally:
        active_turn_run_registry.unregister(handle)

    events = (
        await db_session.execute(
            select(AgentSessionEvent)
            .where(AgentSessionEvent.event_type == "in_flight_steering")
            .order_by(AgentSessionEvent.sequence)
        )
    ).scalars().all()
    assert len(events) == 1
    # deferred-to-successor: the live run is never injected
    assert events[0].payload["accepted"] is False
    assert events[0].hermes_run_id == "hermes_run:steer-session-event"


async def _running_turn_with_run(
    db_session,
    service,
    workspace,
    conversation,
    customer,
    agent,
    *,
    run_id: str,
    run_state: str,
    telegram_id: int,
):
    """A turn marked 'running' against a HermesRun in `run_state`."""
    msg = await _customer_message(db_session, conversation, "ha", telegram_id=telegram_id)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=msg,
        agent_id=agent.id,
    )
    turn = await service.mark_running(
        turn_session_id=turn.id,
        agent_id=agent.id,
        hermes_run_id=run_id,
        engine_run_id="engine:x",
    )
    db_session.add(
        HermesRun(
            run_id=run_id,
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            lane="fast_interactive",
            run_mode="reply",
            trigger_type="conversation_turn",
            trigger_id=f"turn:{turn.id}:rev:1",
            conversation_id=conversation.id,
            correlation_id=f"test:{run_id}",
            idempotency_key=f"test:{run_id}:{turn.id}",
            state=run_state,
            source_refs=[],
            input_summary="ha",
            details={},
            payload={},
        )
    )
    await db_session.flush()
    return turn


async def test_reconcile_failed_run_turns_terminates_zombie(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    """An oqim-api restart / #418 aborts a turn mid-flight: the HermesRun janitor
    marks the run 'failed', but reclaim_stale_turn_leases only recovers
    'starting' leases, so the turn-session stays 'running' forever and swallows
    new customer messages. The reconciler terminates turns whose run is failed."""
    service = ConversationTurnSessionService(db_session)
    turn = await _running_turn_with_run(
        db_session,
        service,
        workspace,
        conversation,
        customer,
        agent,
        run_id="hermes_run:zombie",
        run_state="failed",
        telegram_id=9001,
    )
    assert turn.state == "running"
    assert turn.active_hermes_run_id == "hermes_run:zombie"

    n = await service.reconcile_failed_run_turns(limit=10)

    assert n == 1
    refreshed = await db_session.get(ConversationTurnSession, turn.id)
    assert refreshed.state not in NON_TERMINAL_TURN_STATES
    assert refreshed.active_hermes_run_id is None
    assert refreshed.active_engine_run_id is None
    assert refreshed.stale_reason == "run_failed_reclaimed"


async def test_reconcile_failed_run_turns_leaves_live_running_turn(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    """A turn whose run is still 'running' is genuinely in-flight, NOT a zombie:
    the reconciler must not touch it (no false-positive on a live turn)."""
    service = ConversationTurnSessionService(db_session)
    turn = await _running_turn_with_run(
        db_session,
        service,
        workspace,
        conversation,
        customer,
        agent,
        run_id="hermes_run:live",
        run_state="running",
        telegram_id=9002,
    )

    n = await service.reconcile_failed_run_turns(limit=10)

    assert n == 0
    refreshed = await db_session.get(ConversationTurnSession, turn.id)
    assert refreshed.state == "running"
    assert refreshed.active_hermes_run_id == "hermes_run:live"
