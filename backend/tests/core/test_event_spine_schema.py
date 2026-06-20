"""Tests for EventSpine event schema — types, discriminated union, validation."""
from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.core.event_spine import (
    BackfillWindowApplied,
    DeliveryFailed,
    DeliveryUnknown,
    DivergenceKind,
    Event,
    MsgDeleted,
    MsgEdited,
    MsgInbound,
    MsgSent,
    DeliveryConfirmed,
    MediaHydrationStateChanged,
)


def test_msg_inbound_construction_minimal_fields():
    event = MsgInbound(
        workspace_id=7,
        telegram_chat_id=4101,
        telegram_message_id=12345,
        sender_telegram_id=98765,
        is_outgoing=False,
        text="salom",
        sent_at=1_700_000_000.0,
        emitted_at=1_700_000_001.0,
        idempotency_key="tg:4101:12345",
    )
    assert event.type == "msg.inbound"
    assert event.schema_version == 1
    assert event.event_id
    assert event.workspace_id == 7
    assert event.channel == "telegram_dm"
    assert event.correlation_id is None


def test_msg_edited_requires_new_text():
    with pytest.raises(ValidationError):
        MsgEdited(
            workspace_id=7,
            telegram_chat_id=4101,
            telegram_message_id=12345,
            # new_text missing
            edited_at=1.0,
            emitted_at=1.0,
            idempotency_key="tg:4101:12345:edit:1.0",
        )


def test_msg_deleted_carries_list_of_ids():
    event = MsgDeleted(
        workspace_id=7,
        telegram_chat_id=4101,
        telegram_message_ids=[10, 11, 12],
        deleted_at=1.0,
        emitted_at=1.0,
        idempotency_key="tg:4101:del:abc123",
    )
    assert event.telegram_message_ids == [10, 11, 12]


def test_msg_sent_construction():
    event = MsgSent(
        workspace_id=7,
        conversation_id=99,
        text="Salom! Narxi...",
        action_record_id=42,
        emitted_at=1.0,
        idempotency_key="send:uuid-abc",
    )
    assert event.action_record_id == 42


def test_delivery_confirmed_construction():
    event = DeliveryConfirmed(
        workspace_id=7,
        conversation_id=99,
        action_record_id=42,
        external_message_id="tg:4101:8901",
        delivered_at=1.0,
        emitted_at=1.0,
        idempotency_key="action_record:42",
    )
    assert event.external_message_id == "tg:4101:8901"


def test_event_union_discriminates_by_type():
    payload = {
        "type": "msg.inbound",
        "workspace_id": 7,
        "telegram_chat_id": 4101,
        "telegram_message_id": 12345,
        "sender_telegram_id": 98765,
        "is_outgoing": False,
        "text": "salom",
        "sent_at": 1.0,
        "emitted_at": 1.0,
        "idempotency_key": "tg:4101:12345",
    }
    from pydantic import TypeAdapter
    adapter = TypeAdapter(Event)
    event = adapter.validate_python(payload)
    assert isinstance(event, MsgInbound)


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (
            {
                "type": "message.received",
                "workspace_id": 7,
                "telegram_chat_id": 4101,
                "telegram_message_id": 12345,
                "sender_telegram_id": 98765,
                "is_outgoing": False,
                "text": "salom",
                "sent_at": 1.0,
                "emitted_at": 1.0,
                "idempotency_key": "tg:4101:12345",
            },
            MsgInbound,
        ),
        (
            {
                "type": "message.edited",
                "workspace_id": 7,
                "telegram_chat_id": 4101,
                "telegram_message_id": 12345,
                "new_text": "salom edit",
                "edited_at": 2.0,
                "emitted_at": 2.0,
                "idempotency_key": "tg:4101:12345:edit:2",
            },
            MsgEdited,
        ),
        (
            {
                "type": "message.deleted",
                "workspace_id": 7,
                "telegram_chat_id": 4101,
                "telegram_message_ids": [12345],
                "deleted_at": 3.0,
                "emitted_at": 3.0,
                "idempotency_key": "tg:4101:del:12345",
            },
            MsgDeleted,
        ),
        (
            {
                "type": "message.send_requested",
                "workspace_id": 7,
                "conversation_id": 99,
                "text": "seller reply",
                "emitted_at": 4.0,
                "idempotency_key": "send:client-1",
            },
            MsgSent,
        ),
        (
            {
                "type": "message.send_confirmed",
                "workspace_id": 7,
                "conversation_id": 99,
                "action_record_id": None,
                "external_message_id": "8901",
                "delivered_at": 5.0,
                "emitted_at": 5.0,
                "idempotency_key": "delivery:send:client-1",
            },
            DeliveryConfirmed,
        ),
        (
            {
                "type": "message.send_unknown",
                "workspace_id": 7,
                "conversation_id": 99,
                "client_idempotency_key": "send:client-1",
                "reason": "sidecar_timeout",
                "marked_at": 6.0,
                "emitted_at": 6.0,
                "idempotency_key": "delivery_unknown:send:client-1",
            },
            DeliveryUnknown,
        ),
        (
            {
                "type": "message.send_failed",
                "workspace_id": 7,
                "conversation_id": 99,
                "client_idempotency_key": "send:client-1",
                "error": "sidecar_500",
                "failed_at": 6.5,
                "emitted_at": 6.5,
                "idempotency_key": "delivery_failed:send:client-1",
            },
            DeliveryFailed,
        ),
        (
            {
                "type": "message.backfilled",
                "workspace_id": 7,
                "telegram_chat_id": 4101,
                "oldest_external_message_id": "10",
                "latest_external_message_id": "20",
                "oldest_complete": True,
                "latest_complete": True,
                "applied_at": 7.0,
                "emitted_at": 7.0,
                "idempotency_key": "backfill:4101",
            },
            BackfillWindowApplied,
        ),
        (
            {
                "type": "media.hydration_completed",
                "workspace_id": 7,
                "telegram_chat_id": 4101,
                "telegram_message_id": 12345,
                "hydration_status": "hydrated",
                "asset_state": "stream_ready",
                "semantic_state": "ready",
                "action_state": "completed",
                "media_evidence": {
                    "schema_version": "media_evidence.v1",
                    "modality": "photo",
                    "summary": "Customer sent media evidence.",
                    "observations": [
                        {
                            "kind": "visible_object",
                            "value": "ring",
                            "confidence": 0.84,
                            "fields": {},
                        }
                    ],
                    "embedded_text": [],
                    "transcript": None,
                    "customer_supplied": True,
                    "confidence": 0.84,
                },
                "changed_at": 8.0,
                "emitted_at": 8.0,
                "idempotency_key": "media:4101:12345",
            },
            MediaHydrationStateChanged,
        ),
        (
            {
                "type": "media.hydration_deferred",
                "workspace_id": 7,
                "telegram_chat_id": 4101,
                "telegram_message_id": 12346,
                "hydration_status": "deferred",
                "asset_state": "retrying",
                "semantic_state": "retrying",
                "action_state": "deferred",
                "changed_at": 8.0,
                "emitted_at": 8.0,
                "idempotency_key": "media:4101:12346:deferred",
            },
            MediaHydrationStateChanged,
        ),
    ],
)
def test_target_canonical_event_names_discriminate_to_runtime_events(payload, expected_type):
    from pydantic import TypeAdapter

    adapter = TypeAdapter(Event)
    event = adapter.validate_python(payload)

    assert isinstance(event, expected_type)


def test_event_roundtrip_json():
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
    from pydantic import TypeAdapter
    adapter = TypeAdapter(Event)
    roundtripped = adapter.validate_json(event.model_dump_json())
    assert isinstance(roundtripped, MsgInbound)
    assert roundtripped.text == "salom"


def test_divergence_kind_values():
    assert DivergenceKind.EVENT_NO_DB.value == "event_no_db"
    assert DivergenceKind.DB_NO_EVENT.value == "db_no_event"
    assert DivergenceKind.TEXT_MISMATCH.value == "text_mismatch"
    assert DivergenceKind.DEDUP_RACED.value == "dedup_raced"
    assert DivergenceKind.SEND_NO_CONFIRM.value == "send_no_confirm"
    assert DivergenceKind.CONFIRM_NO_SEND.value == "confirm_no_send"


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        MsgInbound(
            workspace_id=7,
            telegram_chat_id=4101,
            telegram_message_id=12345,
            sender_telegram_id=98765,
            is_outgoing=False,
            text="salom",
            sent_at=1.0,
            emitted_at=1.0,
            idempotency_key="tg:4101:12345",
            mystery_field="nope",  # should reject
        )


# ---- from_webhook constructors + deterministic keys ----

def test_msg_inbound_from_webhook_deterministic_key():
    payload = {
        "sellerUserId": "123",
        "chatId": "4101",
        "senderId": "98765",
        "senderName": "Customer",
        "messageId": 12345.0,
        "text": "salom",
        "date": 1_700_000_000,
        "isOutgoing": False,
        "mediaType": None,
        "mediaMetadata": {"mimeType": "image/jpeg", "thumbnailAvailable": True},
        "textEntities": [{"type": "bold", "offset": 0, "length": 5}],
        "replyToMsgId": 12000,
        "forwardFromName": "Ali",
        "forwardDate": 1_699_999_999,
        "groupedId": 555,
        "telegram_update_received_at": 1_700_000_001.1,
        "telegram_state_applied_at": 1_700_000_001.15,
        "hot_event_built_at": 1_700_000_001.2,
        "outbox_enqueued_at": 1_700_000_001.3,
        "backend_webhook_received_at": 1_700_000_001.4,
    }
    event = MsgInbound.from_webhook(payload, workspace_id=7, correlation_id="cid-abc")
    assert event.idempotency_key == "tg:4101:12345"
    assert event.workspace_id == 7
    assert event.correlation_id == "cid-abc"
    assert event.channel_account_id == "123"
    assert event.channel_conversation_id == "4101"
    assert event.channel_message_id == "12345"
    assert event.telegram_chat_id == 4101
    assert event.telegram_message_id == 12345
    assert event.sender_telegram_id == 98765
    assert event.is_outgoing is False
    assert event.text == "salom"
    assert event.media_metadata == {"mimeType": "image/jpeg", "thumbnailAvailable": True}
    assert event.text_entities == [{"type": "bold", "offset": 0, "length": 5}]
    assert event.reply_to_msg_id == 12000
    assert event.forward_from_name == "Ali"
    assert event.forward_date == 1_699_999_999
    assert event.grouped_id == 555
    assert event.sent_at == 1_700_000_000.0
    assert event.telegram_update_received_at == 1_700_000_001.1
    assert event.telegram_state_applied_at == 1_700_000_001.15
    assert event.hot_event_built_at == 1_700_000_001.2
    assert event.outbox_enqueued_at == 1_700_000_001.3
    assert event.backend_webhook_received_at == 1_700_000_001.4


def test_msg_inbound_live_recovery_gets_distinct_idempotency_key():
    payload = {
        "sellerUserId": "123",
        "chatId": "4101",
        "senderId": "98765",
        "messageId": 12345,
        "text": "salom",
        "date": 1_700_000_000,
        "isOutgoing": False,
        "source": "live_recovery",
    }

    event = MsgInbound.from_webhook(payload, workspace_id=7)

    assert event.idempotency_key == "tg:4101:12345:live_recovery"
    assert event.source == "live_recovery"
    assert event.is_historical is False


def test_msg_edited_from_webhook_deterministic_key_includes_edit_ts():
    payload = {
        "sellerUserId": "123",
        "chatId": "4101",
        "messageId": 12345,
        "text": "salom tuzatildi",
        "textEntities": [{"type": "italic", "offset": 0, "length": 5}],
    }
    event = MsgEdited.from_webhook(payload, workspace_id=7, edited_at=1_700_000_050.0)
    # edit key includes edited_at so two edits of same msg are distinct events
    assert event.idempotency_key == "tg:4101:12345:edit:1700000050.000000"
    assert event.new_text == "salom tuzatildi"
    assert event.text_entities == [{"type": "italic", "offset": 0, "length": 5}]


def test_msg_deleted_from_webhook_key_is_hash_of_sorted_ids():
    payload = {
        "sellerUserId": "123",
        "chatId": "4101",
        "messageIds": [12, 10, 11],  # unsorted
    }
    event = MsgDeleted.from_webhook(payload, workspace_id=7, deleted_at=1_700_000_060.0)
    # sorted IDs → stable hash
    expected_hash = hashlib.sha256(b"10,11,12").hexdigest()[:12]
    assert event.idempotency_key == f"tg:4101:del:{expected_hash}"
    assert event.telegram_message_ids == [10, 11, 12]  # stored sorted


def test_msg_sent_default_idempotency_key_is_uuid():
    event = MsgSent.build(workspace_id=7, conversation_id=99, text="hi", action_record_id=42)
    assert event.idempotency_key.startswith("send:")
    # UUID4 hex is 32 chars
    assert len(event.idempotency_key) == len("send:") + 32


def test_msg_sent_respects_explicit_client_key():
    event = MsgSent.build(
        workspace_id=7, conversation_id=99, text="hi", action_record_id=42,
        client_idempotency_key="send:explicit-uuid",
    )
    assert event.idempotency_key == "send:explicit-uuid"


def test_delivery_confirmed_key_from_action_record():
    event = DeliveryConfirmed.build(
        workspace_id=7, conversation_id=99, action_record_id=42,
        external_message_id="tg:4101:8901",
    )
    assert event.idempotency_key == "delivery:action_record:42"
    assert event.causation_id == "action_record:42"


def test_delivery_confirmed_key_fallback_when_no_action_record():
    event = DeliveryConfirmed.build(
        workspace_id=7, conversation_id=99, action_record_id=None,
        external_message_id="tg:4101:8901",
    )
    assert event.idempotency_key.startswith("delivery:")
    assert len(event.idempotency_key) == len("delivery:") + 32
