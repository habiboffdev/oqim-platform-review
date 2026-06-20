from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.agent_runtime_v2.records_queue import (
    RecordsConsumer,
    RecordsJob,
    RecordsQueue,
)

pytestmark = pytest.mark.asyncio


def _job(conversation_id: int = 1, **overrides) -> RecordsJob:
    base = dict(
        workspace_id=1,
        conversation_id=conversation_id,
        customer_id=2,
        agent_id=3,
        agent_session_id=4,
        hermes_run_id=f"run-{conversation_id}",
        agent_config=object(),
        agent_kind="seller",
        hermes_session_id="oqim:agent-session:4",
        session_db=object(),
        grounding=[],
        conversation_state={},
        reply_delivered=True,
    )
    base.update(overrides)
    return RecordsJob(**base)


async def test_enqueue_accepts_when_not_full():
    q = RecordsQueue(maxsize=4)
    assert q.enqueue(_job(1)) is True
    assert q.qsize() == 1
    assert q.dropped_count == 0


async def test_full_queue_drops_oldest_and_counts(caplog):
    q = RecordsQueue(maxsize=2)
    q.enqueue(_job(1))
    q.enqueue(_job(2))
    # Third enqueue is over capacity: oldest (conv 1) is dropped, newest accepted.
    accepted = q.enqueue(_job(3))
    assert accepted is False
    assert q.dropped_count == 1
    assert q.qsize() == 2
    drained = [(await q.get()).conversation_id for _ in range(2)]
    assert drained == [2, 3]  # conv 1 was dropped, 2 and 3 remain in order
    assert any("records_queue_full" in r.message for r in caplog.records)


async def test_consumer_drains_one_job_into_run_records():
    q = RecordsQueue(maxsize=4)
    run = AsyncMock()
    consumer = RecordsConsumer(queue=q, pool_size=1, run_records=run)
    q.enqueue(_job(7, hermes_run_id="run-7"))

    await consumer._drain_one()

    run.assert_awaited_once()
    assert run.await_args.kwargs["conversation_id"] == 7
    assert run.await_args.kwargs["hermes_run_id"] == "run-7"
    assert "db" not in run.await_args.kwargs  # consumer never supplies a db
    assert q.qsize() == 0


async def test_consumer_swallows_a_failing_job_and_marks_done():
    q = RecordsQueue(maxsize=4)
    run = AsyncMock(side_effect=RuntimeError("boom"))
    beats: list[int] = []
    consumer = RecordsConsumer(queue=q, pool_size=1, run_records=run)
    consumer.set_heartbeat_callback(lambda: beats.append(1))
    q.enqueue(_job(8))

    await consumer._drain_one()  # must NOT raise

    run.assert_awaited_once()
    assert q.qsize() == 0  # task_done was called despite the failure
    assert beats  # the consumer heartbeats each drained job


def test_settings_expose_records_and_runner_knobs():
    from app.core.config import Settings

    s = Settings()
    assert s.records_queue_maxsize >= 1
    assert s.records_consumer_pool_size >= 1
    assert s.turn_runner_dispatch_concurrency >= 1
    assert s.turn_runner_max_per_workspace >= 1
