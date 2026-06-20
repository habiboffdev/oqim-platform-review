from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_turn_session import ConversationTurnSession
from app.models.customer import Customer
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.conversation_turns.service import (
    NON_TERMINAL_TURN_STATES,
    ConversationTurnSessionService,
)

pytestmark = pytest.mark.asyncio


async def test_turn_lease_contract_names_runtime_owner():
    from app.modules.conversation_turns.contracts import TurnLease

    lease = TurnLease(
        turn_session_id=1,
        workspace_id=2,
        conversation_id=3,
        agent_id=4,
        latest_customer_message_id=5,
        turn_revision=6,
        generation=7,
    )

    assert lease.turn_session_id == 1
    assert lease.latest_customer_message_id == 5


async def test_lease_ready_turns_fairly_claims_one_turn_per_workspace_first(
    db_session: AsyncSession,
    workspace: Workspace,
    workspace_b: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
):
    service = ConversationTurnSessionService(db_session)
    agent_b = Agent(workspace_id=workspace_b.id, name="Workspace B seller", agent_type="seller")
    db_session.add(agent_b)
    await db_session.flush()
    second_conversation = await _conversation_bundle(
        db_session,
        workspace=workspace,
        suffix=2,
    )
    foreign_conversation = await _conversation_bundle(
        db_session,
        workspace=workspace_b,
        suffix=3,
    )
    first_turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="A1",
    )
    second_turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=second_conversation[1],
        customer=second_conversation[0],
        agent=agent,
        text="A2",
    )
    foreign_turn = await _open_turn(
        service,
        db_session,
        workspace=workspace_b,
        conversation=foreign_conversation[1],
        customer=foreign_conversation[0],
        agent=agent_b,
        text="B1",
    )

    leases = await service.lease_ready_turns(
        limit=3,
        max_per_workspace=2,
    )

    assert [lease.workspace_id for lease in leases[:2]] == [workspace.id, workspace_b.id]
    assert {lease.turn_session_id for lease in leases} == {
        first_turn.id,
        second_turn.id,
        foreign_turn.id,
    }
    rows = (
        await db_session.execute(
            select(ConversationTurnSession).where(
                ConversationTurnSession.id.in_([lease.turn_session_id for lease in leases])
            )
        )
    ).scalars().all()
    assert {row.state for row in rows} == {"starting"}


async def test_lease_waits_for_burst_coalescing_window(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
):
    """A turn whose latest bubble is fresh is held back so bursts coalesce.

    Live repro: run 33 dispatched on "Assalomu alaykum" 1.8s before the real
    question landed (2026-06-09); the wasted run's reply was discarded and the
    customer waited two turn-times. Each new bubble restarts the window.
    """
    service = ConversationTurnSessionService(db_session)
    turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="Assalomu alaykum",
    )
    # fresh bubble inside the window -> not leased
    turn.latest_customer_message_at = datetime.now(UTC)
    await db_session.flush()
    assert await service.lease_ready_turns(limit=5, max_per_workspace=5) == []

    # window elapsed -> leased
    from app.modules.conversation_turns.service import TURN_COALESCE_SECONDS

    turn.latest_customer_message_at = datetime.now(UTC) - timedelta(
        seconds=TURN_COALESCE_SECONDS + 0.5
    )
    await db_session.flush()
    leases = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert [lease.turn_session_id for lease in leases] == [turn.id]


async def test_reclaim_stale_turn_leases_returns_starting_turns_to_open(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
):
    service = ConversationTurnSessionService(db_session)
    turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="A stale start",
    )
    turn.state = "starting"
    turn.updated_at = datetime.now(UTC) - timedelta(seconds=120)
    await db_session.flush()

    reclaimed = await service.reclaim_stale_turn_leases(
        lease_ttl_seconds=30,
        limit=10,
    )

    assert reclaimed == 1
    await db_session.refresh(turn)
    assert turn.state == "open"
    assert turn.stale_reason == "turn_lease_expired"


async def test_complete_turn_for_trigger_only_closes_matching_latest_turn(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
):
    service = ConversationTurnSessionService(db_session)
    turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="First trigger",
    )
    turn.state = "starting"
    await db_session.flush()

    completed = await service.complete_turn_for_trigger(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        trigger_message_id=turn.latest_customer_message_id + 999,
        reason="wrong_trigger",
    )

    assert completed is False
    await db_session.refresh(turn)
    assert turn.state == "starting"

    completed = await service.complete_turn_for_trigger(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        trigger_message_id=turn.latest_customer_message_id,
        reason="seller_replied",
    )

    assert completed is True
    await db_session.refresh(turn)
    assert turn.state == "completed"
    assert turn.stale_reason == "seller_replied"


async def test_turn_runner_dispatches_latest_leased_turn_and_clears_wakeup_hint(
    db_session: AsyncSession,
    fake_redis,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
):
    from app.modules.conversation_turns.runner import ConversationTurnRunner

    service = ConversationTurnSessionService(db_session)
    turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="Assalomu alaykum",
    )
    message = await db_session.get(Message, turn.latest_customer_message_id)
    assert message is not None
    message.media_type = "photo"
    # Hydrated so the media-hold gate (lease_ready_turns) lets the turn dispatch;
    # this test exercises runner dispatch + media_type passthrough, not the hold.
    message.media_metadata = {"hydration_status": "hydrated"}
    await db_session.flush()
    dispatch_agent_runtime = AsyncMock(return_value=True)
    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(db_session),
        dispatch_agent_runtime=dispatch_agent_runtime,
    )

    await runner.enqueue_message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        message_id=turn.latest_customer_message_id,
        trigger_telemetry={"backend_webhook_received_at": 10},
    )

    processed = await runner.run_once(limit=1)

    assert processed == 1
    dispatch_agent_runtime.assert_awaited_once()
    call = dispatch_agent_runtime.await_args.kwargs
    assert call["workspace_id"] == workspace.id
    assert call["turn_session"].id == turn.id
    assert call["media_type"] == "photo"
    assert call["delivery"] is None
    assert call["trigger_telemetry"] == {"backend_webhook_received_at": 10.0}
    await db_session.refresh(turn)
    assert turn.state == "starting"
    assert await fake_redis.get(runner.queued_job_key(workspace.id, conversation.id)) is None
    assert (
        await fake_redis.get(
            runner.telemetry_key(
                workspace.id,
                conversation.id,
                turn.latest_customer_message_id,
            )
        )
        is None
    )


async def test_turn_runner_completes_skipped_turn_without_retry_loop(
    db_session: AsyncSession,
    fake_redis,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
):
    from app.modules.conversation_turns.runner import ConversationTurnRunner

    service = ConversationTurnSessionService(db_session)
    turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="Do not reply",
    )
    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(db_session),
        dispatch_agent_runtime=AsyncMock(return_value=False),
    )

    processed = await runner.run_once(limit=1)

    assert processed == 1
    await db_session.refresh(turn)
    assert turn.state == "completed"
    assert turn.stale_reason == "dispatch_skipped"


class _SessionCM:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *args) -> None:
        return None


async def _conversation_bundle(
    db_session: AsyncSession,
    *,
    workspace: Workspace,
    suffix: int,
) -> tuple[Customer, Conversation]:
    customer = Customer(
        workspace_id=workspace.id,
        display_name=f"Turn Customer {suffix}",
        phone_number=f"+998900000{suffix:03d}",
    )
    db_session.add(customer)
    await db_session.flush()
    conversation = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        telegram_chat_id=900000 + suffix,
        channel="telegram_dm",
        last_message_at=datetime.now(UTC),
    )
    db_session.add(conversation)
    await db_session.flush()
    return customer, conversation


async def _open_turn(
    service: ConversationTurnSessionService,
    db_session: AsyncSession,
    *,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
    text: str,
) -> ConversationTurnSession:
    message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content=text,
        created_at=datetime.now(UTC),
    )
    db_session.add(message)
    await db_session.flush()
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=message,
        agent_id=agent.id,
    )
    # Age the turn past the burst-coalescing window so lease tests exercise
    # their own concern; the window itself is covered by
    # test_lease_waits_for_burst_coalescing_window.
    turn.latest_customer_message_at = datetime.now(UTC) - timedelta(seconds=10)
    await db_session.flush()
    return turn


async def test_turn_runner_dispatches_full_burst_not_just_latest_message(
    db_session: AsyncSession,
    fake_redis,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
):
    """Live failure 2026-06-11: "salom" + "kurs haqida..." coalesced into one
    turn but only the LAST message reached the model — it never saw the
    greeting, so it never greeted back. The dispatch must carry every customer
    message in the turn window."""
    from app.modules.conversation_turns.runner import ConversationTurnRunner

    service = ConversationTurnSessionService(db_session)
    turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="salom",
    )
    second = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="kurs haqida malumot bering",
        created_at=datetime.now(UTC),
    )
    db_session.add(second)
    await db_session.flush()
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=second,
        agent_id=agent.id,
    )
    turn.latest_customer_message_at = datetime.now(UTC) - timedelta(seconds=10)
    await db_session.flush()

    dispatch_agent_runtime = AsyncMock(return_value=True)
    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(db_session),
        dispatch_agent_runtime=dispatch_agent_runtime,
    )
    await runner.enqueue_message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        message_id=turn.latest_customer_message_id,
    )

    processed = await runner.run_once(limit=1)

    assert processed == 1
    call = dispatch_agent_runtime.await_args.kwargs
    burst = call["burst_messages"]
    assert [m.content for m in burst] == ["salom", "kurs haqida malumot bering"]
    # the trigger message stays the latest one (reply refs, ids)
    assert call["message"].content == "kurs haqida malumot bering"


def test_burst_prompt_text_joins_all_customer_messages():
    from types import SimpleNamespace

    from app.modules.agent_runtime_v2.dispatcher import _burst_prompt_text

    salom = SimpleNamespace(content="salom", media_type=None)
    question = SimpleNamespace(content="kurs haqida malumot bering", media_type=None)
    voice = SimpleNamespace(content="", media_type="voice")

    assert _burst_prompt_text([salom, question], question) == (
        "salom\nkurs haqida malumot bering"
    )
    # empty-content media messages render their media marker
    assert _burst_prompt_text([voice, question], question) == (
        "[voice]\nkurs haqida malumot bering"
    )
    # no burst falls back to the trigger message
    assert _burst_prompt_text([], question) == "kurs haqida malumot bering"
    assert _burst_prompt_text(None, question) == "kurs haqida malumot bering"


async def test_run_once_reclaims_stale_running_hermes_runs(
    db_session: AsyncSession,
    fake_redis,
    workspace: Workspace,
    agent: Agent,
):
    """The runner's maintenance pass must reclaim HermesRuns stuck 'running'
    past the TTL — a turn aborted before finalization must never leave the
    central run record lying as running forever (#418)."""
    from app.models.hermes_run import HermesRun
    from app.modules.conversation_turns.runner import ConversationTurnRunner
    from app.modules.hermes_runtime.contracts import HermesRunInput
    from app.modules.hermes_runtime.service import HermesRunService

    run_service = HermesRunService(db_session)
    await run_service.start_or_dedupe(
        HermesRunInput(
            run_id="hermes_run:runner-stale",
            workspace_id=workspace.id,
            agent_id=agent.id,
            trigger_type="conversation_turn",
            trigger_id="trigger:runner-stale",
        )
    )
    await run_service.mark_running("hermes_run:runner-stale")
    stale = await db_session.scalar(
        select(HermesRun).where(HermesRun.run_id == "hermes_run:runner-stale")
    )
    assert stale is not None
    stale.updated_at = datetime.now(UTC) - timedelta(seconds=1200)
    await db_session.flush()

    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(db_session),
        dispatch_agent_runtime=AsyncMock(return_value=True),
    )

    await runner.run_once(limit=1)

    await db_session.refresh(stale)
    assert stale.state == "failed"
    assert stale.error_code == "stale_running_reclaimed"
    assert stale.completed_at is not None


async def test_run_once_reconciles_turn_session_orphaned_by_failed_run(
    db_session: AsyncSession,
    fake_redis,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
):
    """End-to-end recovery: a turn killed mid-flight (its HermesRun stuck
    'running' past TTL) must be fully recovered by ONE maintenance pass — the
    run is failed AND the orphaned turn-session is terminated, so the chat
    unwedges instead of swallowing every later message into the zombie."""
    from app.models.hermes_run import HermesRun
    from app.modules.conversation_turns.runner import ConversationTurnRunner
    from app.modules.hermes_runtime.contracts import HermesRunInput
    from app.modules.hermes_runtime.service import HermesRunService

    service = ConversationTurnSessionService(db_session)
    turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="ha",
    )
    await service.mark_running(
        turn_session_id=turn.id,
        agent_id=agent.id,
        hermes_run_id="hermes_run:runner-zombie",
        engine_run_id="engine:z",
    )
    run_service = HermesRunService(db_session)
    await run_service.start_or_dedupe(
        HermesRunInput(
            run_id="hermes_run:runner-zombie",
            workspace_id=workspace.id,
            agent_id=agent.id,
            trigger_type="conversation_turn",
            trigger_id=f"turn:{turn.id}:rev:1",
        )
    )
    await run_service.mark_running("hermes_run:runner-zombie")
    stale = await db_session.scalar(
        select(HermesRun).where(HermesRun.run_id == "hermes_run:runner-zombie")
    )
    assert stale is not None
    stale.updated_at = datetime.now(UTC) - timedelta(seconds=1200)
    await db_session.flush()

    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(db_session),
        dispatch_agent_runtime=AsyncMock(return_value=True),
    )

    await runner.run_once(limit=1)

    await db_session.refresh(stale)
    refreshed_turn = await db_session.get(ConversationTurnSession, turn.id)
    assert stale.state == "failed"  # the run was reclaimed
    assert refreshed_turn.state not in NON_TERMINAL_TURN_STATES  # turn reconciled
    assert refreshed_turn.stale_reason == "run_failed_reclaimed"
    assert refreshed_turn.active_hermes_run_id is None


async def test_release_failed_lease_quarantines_poisoned_turn_after_repeated_failures(
    db_session: AsyncSession,
    fake_redis,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
):
    """A turn that keeps failing dispatch must be quarantined to a terminal state
    instead of being re-leased forever (poisoned-turn crash-loop, #415)."""
    from app.modules.conversation_turns.contracts import TurnLease
    from app.modules.conversation_turns.runner import ConversationTurnRunner

    service = ConversationTurnSessionService(db_session)
    turn = await _open_turn(
        service, db_session,
        workspace=workspace, conversation=conversation, customer=customer, agent=agent,
        text="poison",
    )
    lease = TurnLease(
        turn_session_id=turn.id,
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        latest_customer_message_id=turn.latest_customer_message_id,
        turn_revision=int(turn.turn_revision or 1),
        generation=int(turn.generation or 1),
    )
    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(db_session),
        dispatch_agent_runtime=AsyncMock(),
    )

    # First failures are retryable: reopened, counter climbs but state stays active.
    for expected in (1, 2):
        turn.state = "starting"
        await db_session.flush()
        await runner._release_failed_lease(lease, reason="dispatch_error")
        await db_session.refresh(turn)
        assert turn.state == "open"
        assert turn.failed_dispatch_count == expected

    # Third consecutive failure: quarantined (terminal), never leased again.
    turn.state = "starting"
    await db_session.flush()
    await runner._release_failed_lease(lease, reason="dispatch_error")
    await db_session.refresh(turn)
    assert turn.state == "quarantined"
    assert turn.failed_dispatch_count == 3

    leases = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert turn.id not in {leased.turn_session_id for leased in leases}


async def test_run_once_survives_poisoned_maintenance_and_still_leases(
    db_session: AsyncSession,
    fake_redis,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
    monkeypatch,
):
    """One bad row in maintenance must never halt the workspace: the 2026-06-11
    outage turned ONE duplicate turn into a crash-loop that stopped ALL
    replies. Maintenance failure is logged and isolated; leasing proceeds."""
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    from app.modules.conversation_turns.runner import ConversationTurnRunner

    service = ConversationTurnSessionService(db_session)
    turn = await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="hardened",
    )
    # The resilience path rolls the session back to the last commit boundary;
    # commit here so fixture rows survive (savepoint join mode makes this safe
    # — the outer test transaction still rolls everything back at teardown).
    await db_session.commit()

    async def poisoned_reclaim(self, **kwargs):
        raise SAIntegrityError(
            "UPDATE conversation_turn_sessions", {}, Exception("duplicate key")
        )

    monkeypatch.setattr(
        ConversationTurnSessionService, "reclaim_stale_turn_leases", poisoned_reclaim
    )
    dispatch = AsyncMock(return_value=True)
    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(db_session),
        dispatch_agent_runtime=dispatch,
    )

    processed = await runner.run_once(limit=1)

    assert processed == 1
    dispatch.assert_awaited_once()
    assert dispatch.await_args.kwargs["turn_session"].id == turn.id


async def test_run_once_survives_lease_failure_without_crashing(
    db_session: AsyncSession,
    fake_redis,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    agent: Agent,
    monkeypatch,
):
    """A leasing failure is one skipped cycle, never an unhandled exception
    propagating into the supervisor's 1s retry crash-loop."""
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    from app.modules.conversation_turns.runner import ConversationTurnRunner

    service = ConversationTurnSessionService(db_session)
    await _open_turn(
        service,
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        agent=agent,
        text="lease poison",
    )
    await db_session.commit()

    async def poisoned_lease(self, **kwargs):
        raise SAIntegrityError(
            "UPDATE conversation_turn_sessions", {}, Exception("duplicate key")
        )

    monkeypatch.setattr(
        ConversationTurnSessionService, "lease_ready_turns", poisoned_lease
    )
    dispatch = AsyncMock(return_value=True)
    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(db_session),
        dispatch_agent_runtime=dispatch,
    )

    processed = await runner.run_once(limit=1)  # must NOT raise

    assert processed == 0
    dispatch.assert_not_awaited()


async def test_dispatch_leases_runs_concurrently_up_to_the_bound(fake_redis):
    """Different conversations dispatch CONCURRENTLY, bounded by dispatch_concurrency
    (the 2026-06-15 outage was a serial loop where one slow turn blocked the next
    customer). 6 leases with a bound of 3 must reach exactly 3-in-flight, never more,
    and all 6 are processed."""
    import asyncio

    from app.modules.conversation_turns.contracts import TurnLease
    from app.modules.conversation_turns.runner import ConversationTurnRunner

    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(None),
        dispatch_agent_runtime=AsyncMock(return_value=True),
        dispatch_concurrency=3,
    )

    in_flight = 0
    max_in_flight = 0
    gate = asyncio.Event()

    async def _fake_dispatch(lease):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # Hold until enough are in flight to observe the bound, then drain.
        if max_in_flight >= 3:
            gate.set()
        await gate.wait()
        in_flight -= 1
        return True

    runner._dispatch_lease = _fake_dispatch  # type: ignore[assignment]

    leases = [
        TurnLease(
            turn_session_id=i,
            workspace_id=1,
            conversation_id=i,
            agent_id=1,
            latest_customer_message_id=i,
            turn_revision=1,
            generation=1,
        )
        for i in range(1, 7)
    ]

    processed = await runner._dispatch_leases(leases)

    assert processed == 6
    assert max_in_flight == 3  # never exceeded the bound


async def test_dispatch_leases_releases_a_failed_lease(fake_redis):
    """A dispatch that raises is isolated: that lease is released, the others still
    process, and the runner never crashes the batch."""
    from app.modules.conversation_turns.contracts import TurnLease
    from app.modules.conversation_turns.runner import ConversationTurnRunner

    runner = ConversationTurnRunner(
        redis_url="redis://unused",
        redis=fake_redis,
        db_factory=lambda: _SessionCM(None),
        dispatch_agent_runtime=AsyncMock(return_value=True),
        dispatch_concurrency=4,
    )

    released: list[int] = []

    async def _fake_dispatch(lease):
        if lease.turn_session_id == 2:
            raise RuntimeError("boom")
        return True

    async def _fake_release(lease, *, reason):
        released.append(lease.turn_session_id)

    runner._dispatch_lease = _fake_dispatch  # type: ignore[assignment]
    runner._release_failed_lease = _fake_release  # type: ignore[assignment]

    leases = [
        TurnLease(
            turn_session_id=i, workspace_id=1, conversation_id=i, agent_id=1,
            latest_customer_message_id=i, turn_revision=1, generation=1,
        )
        for i in range(1, 4)
    ]

    processed = await runner._dispatch_leases(leases)

    assert processed == 2  # leases 1 and 3 succeeded
    assert released == [2]  # the failing lease was released
