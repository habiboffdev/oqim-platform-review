"""Tests for EventSpineDiffConsumer — divergence detection + lifecycle."""
from __future__ import annotations


import fakeredis.aioredis
import pytest

from app.core.event_spine import EventSpine, MsgInbound
from app.services.event_spine_diff_consumer import EventSpineDiffConsumer


@pytest.fixture
async def fake_redis_dc():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def consumer(fake_redis_dc):
    c = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: None,
        workspace_ids_provider=lambda: [7],
    )
    yield c
    await c.stop()


async def _always_ok(event, session):
    return None  # no divergence


async def _append_raw_event(redis, event):
    await redis.xadd(
        f"oqim:events:{event.workspace_id}",
        {"payload": event.model_dump_json()},
    )


async def test_consumer_implements_supervisor_protocol(consumer):
    """Must have start, stop, and set_heartbeat_callback."""
    assert hasattr(consumer, "start")
    assert hasattr(consumer, "stop")
    assert hasattr(consumer, "set_heartbeat_callback")


async def test_consumer_creates_group_on_first_tick(fake_redis_dc, consumer):
    """ensure_groups creates a group per workspace stream."""
    await fake_redis_dc.xadd("oqim:events:7", {"type": "test", "payload": "{}"})
    await consumer._ensure_groups()
    groups = await fake_redis_dc.xinfo_groups("oqim:events:7")
    assert any(g["name"] == "diff" for g in groups)


async def test_consumer_reads_and_acks_events(fake_redis_dc, consumer, monkeypatch):
    """A healthy tick reads events and ACKs what it processed."""
    monkeypatch.setattr(consumer, "_compare", _always_ok)

    spine = EventSpine(fake_redis_dc)
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
    )
    spine.publish(event)
    await spine.drain()

    await consumer._ensure_groups()
    processed = await consumer._run_once(block_ms=100)

    assert processed == 1
    # PEL empty means the event was acked
    pending = await fake_redis_dc.xpending("oqim:events:7", "diff")
    assert pending["pending"] == 0


async def test_consumer_recovers_missing_group_on_new_stream(fake_redis_dc, consumer, monkeypatch):
    """A new stream without the diff group should self-heal instead of looping on NOGROUP."""
    monkeypatch.setattr(consumer, "_compare", _always_ok)
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
    )
    await fake_redis_dc.xadd("oqim:events:7", {"type": event.type, "payload": event.model_dump_json()})

    processed = await consumer._run_once(block_ms=100)

    assert processed == 1
    groups = await fake_redis_dc.xinfo_groups("oqim:events:7")
    assert any(group["name"] == "diff" for group in groups)


# ------------------------------------------------------------------
# Comparator tests — EVENT_NO_DB + TEXT_MISMATCH
# ------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from app.core.event_spine import MsgDeleted, MsgEdited
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.event_spine_record import EventSpineRecord
from app.models.message import Message, SenderType
from app.models.workspace import Workspace


class _FakeSessionCM:
    """Adapter: makes an existing db_session usable by consumer's db_factory."""
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *a):
        return None


async def _seed_workspace(db_session) -> Workspace:
    ws = Workspace(
        phone_number="+998901111111",
        name="WS",
        password_hash="x",
    )
    db_session.add(ws)
    await db_session.flush()
    return ws


async def _seed_conversation(db_session, workspace: Workspace, telegram_chat_id: int = 4101) -> Conversation:
    cust = Customer(
        workspace_id=workspace.id,
        telegram_id=98765,
        display_name="c",
    )
    db_session.add(cust)
    await db_session.flush()
    conv = Conversation(
        workspace_id=workspace.id,
        customer_id=cust.id,
        telegram_chat_id=telegram_chat_id,
        channel="telegram_dm",
    )
    db_session.add(conv)
    await db_session.flush()
    return conv


async def test_event_no_db_divergence(db_session, fake_redis_dc):
    """Event published, no matching DB row → EVENT_NO_DB counter incremented."""
    ws = await _seed_workspace(db_session)
    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    spine = EventSpine(fake_redis_dc)
    event = MsgInbound(
        workspace_id=ws.id,
        telegram_chat_id=4101,
        telegram_message_id=99999,
        sender_telegram_id=98765,
        is_outgoing=False,
        text="ghost message",
        sent_at=1.0,
        emitted_at=datetime.now(timezone.utc).timestamp() - 5.0,  # past grace period
        idempotency_key="tg:4101:99999",
    )
    spine.publish(event)
    await spine.drain()

    await consumer._ensure_groups()
    await consumer._run_once(block_ms=100)

    counter = await fake_redis_dc.get("oqim:event_spine:div:event_no_db")
    assert counter == "1"


async def test_inbound_match_no_divergence(db_session, fake_redis_dc):
    """Event published with matching DB row → zero divergence."""
    ws = await _seed_workspace(db_session)
    conv = await _seed_conversation(db_session, ws)
    msg = Message(
        conversation_id=conv.id,
        telegram_message_id=12345,
        content="salom",
        sender_type=SenderType.CUSTOMER,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    db_session.add(msg)
    await db_session.flush()

    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    spine = EventSpine(fake_redis_dc)
    event = MsgInbound(
        workspace_id=ws.id,
        telegram_chat_id=4101,
        telegram_message_id=12345,
        sender_telegram_id=98765,
        is_outgoing=False,
        text="salom",
        sent_at=1.0,
        emitted_at=datetime.now(timezone.utc).timestamp() - 5.0,
        idempotency_key="tg:4101:12345",
    )
    spine.publish(event)
    await spine.drain()

    await consumer._ensure_groups()
    await consumer._run_once(block_ms=100)

    counter = await fake_redis_dc.get("oqim:event_spine:div:event_no_db")
    assert counter is None


async def test_text_mismatch_divergence(db_session, fake_redis_dc):
    """MsgEdited event's new_text differs from DB row's content → TEXT_MISMATCH."""
    ws = await _seed_workspace(db_session)
    conv = await _seed_conversation(db_session, ws)
    msg = Message(
        conversation_id=conv.id,
        telegram_message_id=12345,
        content="old text",
        sender_type=SenderType.CUSTOMER,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    db_session.add(msg)
    await db_session.flush()

    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    spine = EventSpine(fake_redis_dc)
    event = MsgEdited(
        workspace_id=ws.id,
        telegram_chat_id=4101,
        telegram_message_id=12345,
        new_text="completely different",
        edited_at=1.0,
        emitted_at=datetime.now(timezone.utc).timestamp() - 5.0,
        idempotency_key="tg:4101:12345:edit:1.0",
    )
    spine.publish(event)
    await spine.drain()

    await consumer._ensure_groups()
    await consumer._run_once(block_ms=100)

    counter = await fake_redis_dc.get("oqim:event_spine:div:text_mismatch")
    assert counter == "1"


async def test_dedup_raced_divergence(db_session, fake_redis_dc):
    """Two events with same idempotency_key within 1s → DEDUP_RACED."""
    ws = await _seed_workspace(db_session)
    conv = await _seed_conversation(db_session, ws)
    # Seed a matching row so inbound comparator doesn't flag EVENT_NO_DB
    msg = Message(
        conversation_id=conv.id,
        telegram_message_id=12345,
        content="salom",
        sender_type=SenderType.CUSTOMER,
    )
    db_session.add(msg)
    await db_session.flush()

    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    # Append SAME idempotency_key twice. This intentionally bypasses
    # EventSpine.append(), which correctly deduplicates before the stream.
    for _ in range(2):
        event = MsgInbound(
            workspace_id=ws.id,
            telegram_chat_id=4101,
            telegram_message_id=12345,
            sender_telegram_id=98765,
            is_outgoing=False,
            text="salom",
            sent_at=1.0,
            emitted_at=datetime.now(timezone.utc).timestamp() - 5.0,
            idempotency_key="tg:4101:12345",
        )
        await _append_raw_event(fake_redis_dc, event)

    await consumer._ensure_groups()
    await consumer._run_once(block_ms=100)

    counter = await fake_redis_dc.get(f"oqim:event_spine:div:dedup_raced")
    assert counter == "1"


async def test_soft_deleted_message_matches_delete_event(db_session, fake_redis_dc):
    """A delete event should match an existing soft-deleted DB row."""
    ws = await _seed_workspace(db_session)
    conv = await _seed_conversation(db_session, ws)
    msg = Message(
        conversation_id=conv.id,
        telegram_message_id=12345,
        content="[deleted]",
        sender_type=SenderType.CUSTOMER,
        is_deleted=True,
    )
    db_session.add(msg)
    await db_session.flush()

    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    spine = EventSpine(fake_redis_dc)
    event = MsgDeleted(
        workspace_id=ws.id,
        telegram_chat_id=4101,
        telegram_message_ids=[12345],
        deleted_at=1.0,
        emitted_at=datetime.now(timezone.utc).timestamp() - 5.0,
        idempotency_key="tg:4101:del:abc",
    )
    spine.publish(event)
    await spine.drain()

    await consumer._ensure_groups()
    await consumer._run_once(block_ms=100)

    counter = await fake_redis_dc.get("oqim:event_spine:div:event_no_db")
    assert counter is None


# ------------------------------------------------------------------
# Send/confirm pairing tests
# ------------------------------------------------------------------

from app.core.event_spine import DeliveryConfirmed, MsgSent


async def test_msg_sent_without_confirm_after_timeout(db_session, fake_redis_dc):
    """MsgSent with no DeliveryConfirmed after 30s → SEND_NO_CONFIRM."""
    ws = await _seed_workspace(db_session)
    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    spine = EventSpine(fake_redis_dc)
    event = MsgSent(
        workspace_id=ws.id,
        conversation_id=99,
        text="Salom!",
        action_record_id=42,
        emitted_at=datetime.now(timezone.utc).timestamp() - 35.0,  # past 30s window
        idempotency_key="send:abc-123",
    )
    spine.publish(event)
    await spine.drain()

    await consumer._ensure_groups()
    await consumer._run_once(block_ms=100)
    # Sweep for stale unmatched sends
    await consumer._sweep_send_confirm_pairs()

    counter = await fake_redis_dc.get(f"oqim:event_spine:div:send_no_confirm")
    assert counter == "1"


async def test_msg_sent_with_confirm_no_divergence(db_session, fake_redis_dc):
    """Matched pair produces zero divergence."""
    ws = await _seed_workspace(db_session)
    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    sent_event = MsgSent(
        workspace_id=ws.id,
        conversation_id=99,
        text="Salom!",
        action_record_id=42,
        emitted_at=datetime.now(timezone.utc).timestamp() - 35.0,
        idempotency_key="send:abc-123",
    )
    confirm_event = DeliveryConfirmed(
        workspace_id=ws.id,
        conversation_id=99,
        action_record_id=42,
        external_message_id="tg:4101:8901",
        delivered_at=1.0,
        emitted_at=datetime.now(timezone.utc).timestamp() - 34.5,
        idempotency_key="action_record:42",
    )
    await _append_raw_event(fake_redis_dc, sent_event)
    await _append_raw_event(fake_redis_dc, confirm_event)

    await consumer._ensure_groups()
    await consumer._run_once(block_ms=100)
    await consumer._sweep_send_confirm_pairs()

    counter = await fake_redis_dc.get(f"oqim:event_spine:div:send_no_confirm")
    assert counter is None


async def test_manual_msg_sent_with_confirm_matches_by_idempotency_key(db_session, fake_redis_dc):
    """Manual sends without action_record_id should still match by shared send idempotency key."""
    ws = await _seed_workspace(db_session)
    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    sent_event = MsgSent(
        workspace_id=ws.id,
        conversation_id=99,
        text="Salom!",
        action_record_id=None,
        emitted_at=datetime.now(timezone.utc).timestamp() - 35.0,
        idempotency_key="send:manual-123",
    )
    confirm_event = DeliveryConfirmed(
        workspace_id=ws.id,
        conversation_id=99,
        action_record_id=None,
        external_message_id="tg:4101:8901",
        delivered_at=1.0,
        emitted_at=datetime.now(timezone.utc).timestamp() - 34.5,
        idempotency_key="send:manual-123",
    )
    await _append_raw_event(fake_redis_dc, sent_event)
    await _append_raw_event(fake_redis_dc, confirm_event)

    await consumer._ensure_groups()
    await consumer._run_once(block_ms=100)
    await consumer._sweep_send_confirm_pairs()

    assert await fake_redis_dc.get("oqim:event_spine:div:confirm_no_send") is None
    assert await fake_redis_dc.get("oqim:event_spine:div:send_no_confirm") is None


async def test_confirm_without_prior_send_divergence(db_session, fake_redis_dc):
    """DeliveryConfirmed with no prior MsgSent → CONFIRM_NO_SEND."""
    ws = await _seed_workspace(db_session)
    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    spine = EventSpine(fake_redis_dc)
    confirm_event = DeliveryConfirmed(
        workspace_id=ws.id,
        conversation_id=99,
        action_record_id=42,
        external_message_id="tg:4101:8901",
        delivered_at=1.0,
        emitted_at=datetime.now(timezone.utc).timestamp() - 5.0,
        idempotency_key="action_record:42",
    )
    spine.publish(confirm_event)
    await spine.drain()

    await consumer._ensure_groups()
    await consumer._run_once(block_ms=100)

    counter = await fake_redis_dc.get(f"oqim:event_spine:div:confirm_no_send")
    assert counter == "1"


async def test_db_no_event_divergence(db_session, fake_redis_dc):
    """Recent DB row with no matching spine event → DB_NO_EVENT."""
    ws = await _seed_workspace(db_session)
    conv = await _seed_conversation(db_session, ws)
    # Message created now, never published to spine (no dedup key in Redis)
    msg = Message(
        conversation_id=conv.id,
        telegram_message_id=77777,
        content="orphan",
        sender_type=SenderType.CUSTOMER,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(msg)
    await db_session.flush()

    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    await consumer._scan_db_for_orphans()

    counter = await fake_redis_dc.get(f"oqim:event_spine:div:db_no_event")
    assert counter == "1"

    await consumer._scan_db_for_orphans()

    counter = await fake_redis_dc.get(f"oqim:event_spine:div:db_no_event")
    assert counter == "1"


async def test_db_scan_uses_durable_append_marker_not_short_lived_dedupe(
    db_session,
    fake_redis_dc,
):
    """A DB row with an EventSpine append marker is not drift after dedupe TTL."""
    ws = await _seed_workspace(db_session)
    conv = await _seed_conversation(db_session, ws)
    msg = Message(
        conversation_id=conv.id,
        telegram_message_id=88888,
        content="canonical",
        sender_type=SenderType.CUSTOMER,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(msg)
    await db_session.flush()

    spine = EventSpine(fake_redis_dc)
    await spine.append(
        MsgInbound(
            workspace_id=ws.id,
            telegram_chat_id=conv.telegram_chat_id,
            telegram_message_id=88888,
            sender_telegram_id=12345,
            is_outgoing=False,
            text="canonical",
            sent_at=datetime.now(timezone.utc).timestamp(),
            emitted_at=1.0,
            idempotency_key=f"tg:{conv.telegram_chat_id}:88888",
        )
    )

    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    await consumer._scan_db_for_orphans()

    counter = await fake_redis_dc.get(f"oqim:event_spine:div:db_no_event")
    assert counter is None


async def test_db_scan_uses_archived_event_spine_record_when_redis_marker_is_missing(
    db_session,
    fake_redis_dc,
):
    """A durable EventSpine archive row is enough proof after Redis marker loss."""
    ws = await _seed_workspace(db_session)
    conv = await _seed_conversation(db_session, ws)
    msg = Message(
        conversation_id=conv.id,
        telegram_message_id=99901,
        content="archived",
        sender_type=SenderType.CUSTOMER,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(msg)
    db_session.add(
        EventSpineRecord(
            workspace_id=ws.id,
            event_id="evt-99901",
            event_type="msg.inbound",
            schema_version=1,
            channel="telegram",
            channel_account_id="",
            channel_conversation_id=str(conv.telegram_chat_id),
            channel_message_id="99901",
            idempotency_key=f"tg:{conv.telegram_chat_id}:99901",
            occurred_at=datetime.now(timezone.utc),
            received_at=datetime.now(timezone.utc),
            payload={},
        )
    )
    await db_session.flush()

    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    await consumer._scan_db_for_orphans()

    counter = await fake_redis_dc.get(f"oqim:event_spine:div:db_no_event")
    assert counter is None


async def test_db_scan_ignores_outgoing_seller_messages(
    db_session,
    fake_redis_dc,
):
    """Outgoing Telegram rows use msg.sent/delivery ids, not inbound tg ids."""
    ws = await _seed_workspace(db_session)
    conv = await _seed_conversation(db_session, ws)
    msg = Message(
        conversation_id=conv.id,
        telegram_message_id=99902,
        content="seller outbound",
        sender_type=SenderType.SELLER,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(msg)
    await db_session.flush()

    consumer = EventSpineDiffConsumer(
        redis=fake_redis_dc,
        db_factory=lambda: _FakeSessionCM(db_session),
        workspace_ids_provider=lambda: [ws.id],
    )

    await consumer._scan_db_for_orphans()

    counter = await fake_redis_dc.get(f"oqim:event_spine:div:db_no_event")
    assert counter is None
