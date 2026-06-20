from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.conversation import Conversation
from app.models.delivery_runtime import DeliveryRuntime
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.services.message_response_projection import (
    build_media_runtime_response,
    serialize_message_response,
)

NOW = datetime(2026, 4, 27, 8, 0, tzinfo=timezone.utc)


def _conversation(**overrides) -> Conversation:
    data = {
        "id": 10,
        "workspace_id": 1,
        "customer_id": 20,
        "channel": "telegram_dm",
        "telegram_chat_id": 777_000,
    }
    data.update(overrides)
    return Conversation(**data)


def _message(**overrides) -> Message:
    data = {
        "id": 99,
        "conversation_id": 10,
        "channel": "telegram_dm",
        "sender_type": "customer",
        "content": "[media]",
        "telegram_message_id": 555,
        "is_read": False,
        "is_deleted": False,
        "created_at": NOW,
        "delivery_state": "confirmed",
        "conversation_seq": 42,
    }
    data.update(overrides)
    return Message(**data)


def test_serialize_message_response_uses_runtime_row_and_canonical_urls():
    message = _message(
        media_type="MessageMediaDocument",
        media_metadata={"mime_type": "image/gif", "file_name": "reply.gif"},
    )
    next_attempt_at = NOW + timedelta(seconds=30)
    runtime = MediaRuntime(
        workspace_id=1,
        conversation_id=10,
        message_id=99,
        channel="telegram_dm",
        media_type="gif",
        media_ref="telegram_dm:777000:555",
        asset_state="retrying",
        semantic_state="retrying",
        hydration_status="deferred",
        action_state="pending",
        ai_relevant=True,
        attempt_count=2,
        max_attempts=3,
        next_attempt_at=next_attempt_at,
        retry_after_seconds=12.5,
    )

    response = serialize_message_response(message, _conversation(), runtime)

    assert response.media_type == "gif"
    assert response.media_url == "/api/media/777000/555"
    assert response.media_full_url == "/api/media/777000/555"
    assert response.media_preview_url == "/api/media/777000/555?thumb=true"
    assert response.conversation_seq == 42
    assert response.media_runtime == {
        "asset_state": "retrying",
        "semantic_state": "retrying",
        "hydration_status": "deferred",
        "action_state": "pending",
        "ai_relevant": True,
        "attempt_count": 2,
        "max_attempts": 3,
        "next_attempt_at": next_attempt_at,
        "retry_after_seconds": 12.5,
    }


def test_serialize_message_response_exposes_delivery_runtime_projection():
    message = _message(
        sender_type="seller",
        content="Ha, bor",
        client_message_uuid="send-key-1",
        delivery_state="confirmed",
    )
    runtime = DeliveryRuntime(
        workspace_id=1,
        conversation_id=10,
        message_id=99,
        channel="telegram_dm",
        channel_conversation_id="777000",
        client_idempotency_key="send-key-1",
        state="reconciled",
        attempt_count=2,
        external_message_id="9001",
        last_error="sidecar_timeout",
    )

    response = serialize_message_response(
        message,
        _conversation(),
        delivery_runtime=runtime,
    )

    assert response.delivery_state == "confirmed"
    assert response.delivery_runtime is not None
    assert response.delivery_runtime.model_dump() == {
        "schema_version": "delivery_runtime.v1",
        "state": "reconciled",
        "customer_status": "sent",
        "next_action": "none",
        "is_terminal": True,
        "requires_reconciliation": False,
        "can_retry": False,
        "attempt_count": 2,
        "max_attempts": 3,
        "retry_budget_remaining": 1,
        "external_message_id": "9001",
        "last_error": "sidecar_timeout",
        "requested_at": None,
        "sending_at": None,
        "confirmed_at": None,
        "failed_at": None,
        "unknown_at": None,
        "reconciled_at": None,
        "updated_at": None,
    }


def test_serialize_message_response_exposes_failed_delivery_retry_state():
    message = _message(
        sender_type="seller",
        content="Qayta urinib ko'ramiz",
        client_message_uuid="send-key-failed",
        delivery_state="failed",
    )
    runtime = DeliveryRuntime(
        workspace_id=1,
        conversation_id=10,
        message_id=99,
        channel="telegram_dm",
        channel_conversation_id="777000",
        client_idempotency_key="send-key-failed",
        state="failed",
        attempt_count=3,
        last_error="sidecar unavailable",
    )

    response = serialize_message_response(
        message,
        _conversation(),
        delivery_runtime=runtime,
    )

    assert response.delivery_runtime is not None
    assert response.delivery_runtime.customer_status == "failed"
    assert response.delivery_runtime.next_action == "retry"
    assert response.delivery_runtime.is_terminal is True
    assert response.delivery_runtime.can_retry is True
    assert response.delivery_runtime.retry_budget_remaining == 0


def test_media_runtime_response_falls_back_to_legacy_metadata_projection():
    message = _message(
        media_type="MessageMediaPhoto",
        media_metadata={
            "hydration_status": "deferred",
            "retry_after_seconds": 3.25,
            "media_runtime": {
                "asset_state": "retrying",
                "semantic_state": "retrying",
                "ai_relevant": True,
            },
        },
    )

    assert build_media_runtime_response(message, None) == {
        "asset_state": "retrying",
        "semantic_state": "retrying",
        "hydration_status": "deferred",
        "action_state": None,
        "ai_relevant": True,
        "attempt_count": 0,
        "max_attempts": 0,
        "next_attempt_at": None,
        "retry_after_seconds": 3.25,
    }


def test_serialize_message_response_suppresses_urls_for_descriptor_only_media():
    message = _message(
        media_type="MessageMediaContact",
        media_metadata={"phone_number": "+998901234567"},
    )

    response = serialize_message_response(message, _conversation())

    assert response.media_type == "contact"
    assert response.media_url is None
    assert response.media_full_url is None
    assert response.media_preview_url is None
    assert response.media_runtime is None
