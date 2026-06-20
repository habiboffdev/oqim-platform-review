"""Tests for the EventSpine publisher class.

These are the LOAD-BEARING tests for Phase 1: publish must never block the
caller, must never raise, and must log+count failures without side effects
on the hot path.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import patch

import fakeredis.aioredis
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_spine import EventSpine, MsgInbound
from app.models.event_spine_record import EventSpineRecord


@pytest.fixture
async def fake_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def spine(fake_redis):
    s = EventSpine(fake_redis)
    yield s
    await s.drain(timeout=1.0)


def _make_inbound(
    workspace_id: int = 7,
    text: str = "salom",
    message_id: int = 12345,
) -> MsgInbound:
    return MsgInbound(
        workspace_id=workspace_id,
        telegram_chat_id=4101,
        telegram_message_id=message_id,
        sender_telegram_id=98765,
        is_outgoing=False,
        text=text,
        sent_at=1.0,
        emitted_at=1.0,
        idempotency_key=f"tg:4101:{message_id}",
        channel_conversation_id="4101",
        channel_message_id=str(message_id),
    )


@asynccontextmanager
async def _session_context(session: AsyncSession):
    yield session


async def test_publish_returns_synchronously_without_blocking(spine):
    """publish() must return in well under 1ms regardless of Redis state."""
    event = _make_inbound()
    start = time.perf_counter()
    spine.publish(event)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 5.0, f"publish took {elapsed_ms}ms, must be <5ms"


async def test_publish_routes_to_workspace_stream(fake_redis, spine):
    spine.publish(_make_inbound(workspace_id=7))
    spine.publish(_make_inbound(workspace_id=11))
    await spine.drain()

    len_7 = await fake_redis.xlen("oqim:events:7")
    len_11 = await fake_redis.xlen("oqim:events:11")
    assert len_7 == 1
    assert len_11 == 1


async def test_append_does_not_leave_dedupe_marker_when_xadd_fails(fake_redis, spine):
    event = _make_inbound()

    with patch("app.core.event_spine.xadd_event", side_effect=RuntimeError("xadd down")):
        with pytest.raises(RuntimeError):
            await spine.append(event)

    assert await fake_redis.get("oqim:event_spine:appended:7:tg:4101:12345") is None

    stream_id = await spine.append(event)
    assert stream_id is not None
    assert await fake_redis.xlen("oqim:events:7") == 1


async def test_publish_when_redis_down_does_not_raise(fake_redis):
    """If Redis is unreachable, publish must not raise at call site."""
    await fake_redis.aclose()
    spine = EventSpine(fake_redis)

    # Must not raise
    spine.publish(_make_inbound())
    await spine.drain(timeout=1.0)
    # publish_failures counter increment also fails silently when redis is down
    # — the point is: no exception ever reaches the caller.


async def test_publish_carries_correlation_id(fake_redis, spine):
    event = MsgInbound(
        workspace_id=7,
        telegram_chat_id=4101,
        telegram_message_id=12345,
        sender_telegram_id=98765,
        is_outgoing=False,
        text="salom",
        sent_at=1.0,
        emitted_at=1.0,
        idempotency_key="tg:4101:12345",
        correlation_id="cid-abc-123",
    )
    spine.publish(event)
    await spine.drain()

    entries = await fake_redis.xrange("oqim:events:7")
    assert len(entries) == 1
    stream_id, fields = entries[0]
    assert "cid-abc-123" in fields["payload"]


async def test_publish_serialization_failure_is_contained(fake_redis, spine):
    """A serialization bug must not escape publish."""
    event = _make_inbound()

    # Patch at class level: Pydantic v2 forbids setting arbitrary attrs on model
    # instances, so we patch the class method, which the instance inherits.
    # drain() must be inside the context so the mock is still active when the
    # background task executes.
    with patch.object(MsgInbound, "model_dump_json", side_effect=TypeError("bad")):
        spine.publish(event)
        await spine.drain()

    # No entry landed in the stream
    length = await fake_redis.xlen("oqim:events:7")
    assert length == 0
    # But failure was counted
    failures = await fake_redis.get("oqim:event_spine:publish_failures")
    assert failures == "1"


async def test_drain_awaits_all_pending(fake_redis, spine):
    """drain() waits for in-flight background publishes."""
    for i in range(10):
        spine.publish(_make_inbound(text=f"msg-{i}", message_id=12345 + i))

    await spine.drain(timeout=2.0)
    length = await fake_redis.xlen("oqim:events:7")
    assert length == 10


async def test_drain_respects_timeout_without_hanging(fake_redis):
    """drain() with timeout returns even if tasks hang."""
    spine = EventSpine(fake_redis)

    async def hang_forever(*args, **kwargs):
        await asyncio.sleep(100.0)

    with patch("app.core.event_spine.xadd_event", side_effect=hang_forever):
        spine.publish(_make_inbound())
        start = time.perf_counter()
        await spine.drain(timeout=0.1)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"drain took {elapsed}s, should be <1s"


async def test_append_rejects_malformed_event(spine):
    with pytest.raises(Exception):
        await spine.append({"type": "msg.inbound", "workspace_id": 7})


async def test_append_is_idempotent_by_workspace_and_key(fake_redis, spine):
    event = _make_inbound(message_id=500)

    first_id = await spine.append(event)
    second_id = await spine.append(event)

    assert first_id is not None
    assert second_id is None
    assert await fake_redis.xlen("oqim:events:7") == 1


async def test_db_backed_replay_survives_redis_stream_loss(fake_redis, db_session):
    spine = EventSpine(
        fake_redis,
        db_factory=lambda: _session_context(db_session),
    )
    await spine.append(_make_inbound(text="first", message_id=610))
    await spine.append(_make_inbound(text="second", message_id=611))

    assert await fake_redis.xlen("oqim:events:7") == 2
    await fake_redis.flushall()

    events = await spine.replay_conversation(
        workspace_id=7,
        channel="telegram_dm",
        channel_conversation_id="4101",
    )

    assert [event.text for event in events if isinstance(event, MsgInbound)] == [
        "first",
        "second",
    ]


async def test_db_archive_is_idempotent_by_workspace_key(fake_redis, db_session):
    spine = EventSpine(
        fake_redis,
        db_factory=lambda: _session_context(db_session),
    )
    event = _make_inbound(text="once", message_id=612)

    first_id = await spine.append(event)
    second_id = await spine.append(event)

    assert first_id is not None
    assert second_id is None
    archive_count = await db_session.scalar(
        select(func.count())
        .select_from(EventSpineRecord)
        .where(
            EventSpineRecord.workspace_id == 7,
            EventSpineRecord.idempotency_key == "tg:4101:612",
        )
    )
    assert archive_count == 1


async def test_replay_conversation_returns_events_in_order(spine):
    spine.publish(_make_inbound(text="first", message_id=1))
    spine.publish(_make_inbound(text="other conversation", message_id=2))
    spine.publish(MsgInbound(
        workspace_id=7,
        telegram_chat_id=4102,
        telegram_message_id=3,
        sender_telegram_id=98765,
        is_outgoing=False,
        text="different chat",
        sent_at=1.0,
        emitted_at=1.0,
        idempotency_key="tg:4102:3",
        channel_conversation_id="4102",
        channel_message_id="3",
    ))
    spine.publish(_make_inbound(text="second", message_id=4))
    await spine.drain()

    events = await spine.replay_conversation(
        workspace_id=7,
        channel="telegram_dm",
        channel_conversation_id="4101",
    )

    assert [event.text for event in events if isinstance(event, MsgInbound)] == [
        "first",
        "other conversation",
        "second",
    ]


async def test_replay_conversation_walks_stream_in_batches(spine):
    for index in range(7):
        chat_id = 4101 if index % 2 == 0 else 4102
        spine.publish(MsgInbound(
            workspace_id=7,
            telegram_chat_id=chat_id,
            telegram_message_id=index + 1,
            sender_telegram_id=98765,
            is_outgoing=False,
            text=f"msg-{index}",
            sent_at=1.0,
            emitted_at=1.0,
            idempotency_key=f"tg:{chat_id}:{index + 1}",
            channel_conversation_id=str(chat_id),
            channel_message_id=str(index + 1),
        ))
    await spine.drain()

    events = await spine.replay_conversation(
        workspace_id=7,
        channel="telegram_dm",
        channel_conversation_id="4101",
        batch_size=2,
    )

    assert [event.text for event in events if isinstance(event, MsgInbound)] == [
        "msg-0",
        "msg-2",
        "msg-4",
        "msg-6",
    ]


async def test_replay_conversation_rejects_invalid_batch_size(spine):
    with pytest.raises(ValueError, match="batch_size"):
        await spine.replay_conversation(
            workspace_id=7,
            channel="telegram_dm",
            channel_conversation_id="4101",
            batch_size=0,
        )
