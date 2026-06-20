from __future__ import annotations

from app.models.conversation import Conversation
from app.models.delivery_runtime import DeliveryRuntime
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.schemas.delivery import DeliveryRuntimeProjection
from app.schemas.message import MessageResponse
from app.services.channel_media_access import ChannelMediaAccess
from app.services.delivery_runtime import project_delivery_runtime
from app.services.media_types import normalize_media_type


def serialize_message_response(
    message: Message,
    conversation: Conversation,
    media_runtime: MediaRuntime | None = None,
    delivery_runtime: DeliveryRuntime | None = None,
) -> MessageResponse:
    response = MessageResponse.model_validate(message)
    response.media_type = normalize_media_type(message.media_type, message.media_metadata)
    response.conversation_seq = message.conversation_seq
    media_urls = ChannelMediaAccess.message_urls(conversation=conversation, message=message)
    response.media_url = media_urls.full_url
    response.media_full_url = media_urls.full_url
    response.media_preview_url = media_urls.preview_url
    response.media_runtime = build_media_runtime_response(message, media_runtime)
    response.delivery_runtime = build_delivery_runtime_response(delivery_runtime)
    return response


def build_delivery_runtime_response(
    runtime: DeliveryRuntime | None,
) -> DeliveryRuntimeProjection | None:
    return project_delivery_runtime(runtime)


def build_media_runtime_response(
    message: Message,
    runtime: MediaRuntime | None,
) -> dict | None:
    if runtime is not None:
        return {
            "asset_state": runtime.asset_state,
            "semantic_state": runtime.semantic_state,
            "hydration_status": runtime.hydration_status,
            "action_state": runtime.action_state,
            "ai_relevant": runtime.ai_relevant,
            "attempt_count": runtime.attempt_count,
            "max_attempts": runtime.max_attempts,
            "next_attempt_at": runtime.next_attempt_at,
            "retry_after_seconds": runtime.retry_after_seconds,
        }
    metadata = message.media_metadata if isinstance(message.media_metadata, dict) else None
    runtime_payload = metadata.get("media_runtime") if metadata else None
    if not isinstance(runtime_payload, dict):
        return None
    return {
        "asset_state": runtime_payload.get("asset_state"),
        "semantic_state": runtime_payload.get("semantic_state"),
        "hydration_status": metadata.get("hydration_status"),
        "action_state": None,
        "ai_relevant": runtime_payload.get("ai_relevant"),
        "attempt_count": 0,
        "max_attempts": 0,
        "next_attempt_at": None,
        "retry_after_seconds": metadata.get("retry_after_seconds"),
    }
