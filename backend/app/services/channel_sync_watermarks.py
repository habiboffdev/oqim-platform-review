from __future__ import annotations

from dataclasses import dataclass

from app.models.conversation import Conversation
from app.services.channel_sync_models import ChannelMessageRecord
from app.services.conversation_state import (
    ConversationSyncState,
    ConversationSyncWatermarks,
    get_customer_conversation_state,
    set_customer_conversation_state,
)


@dataclass(slots=True)
class ConversationSyncWatermark:
    oldest_external_message_id: str | None = None
    latest_external_message_id: str | None = None
    oldest_complete: bool = False
    latest_complete: bool = False


def get_sync_watermark(conversation: Conversation) -> ConversationSyncWatermark:
    state = get_customer_conversation_state(conversation)
    sync_state = state.sync.model_dump(exclude_none=True) if state.sync else {}
    watermarks = sync_state.get("watermarks")
    if not isinstance(watermarks, dict):
        return ConversationSyncWatermark()
    return ConversationSyncWatermark(
        oldest_external_message_id=_safe_str(watermarks.get("oldest_external_message_id")),
        latest_external_message_id=_safe_str(watermarks.get("latest_external_message_id")),
        oldest_complete=bool(watermarks.get("oldest_complete", False)),
        latest_complete=bool(watermarks.get("latest_complete", False)),
    )


def update_sync_watermark(
    *,
    conversation: Conversation,
    messages: list[ChannelMessageRecord],
    limit: int,
    after_external_message_id: str | None,
    before_external_message_id: str | None,
) -> None:
    if not messages:
        return

    sorted_messages = sorted(
        messages,
        key=lambda msg: _external_message_sort_key(msg.external_message_id),
    )
    fetched_oldest = sorted_messages[0].external_message_id
    fetched_latest = sorted_messages[-1].external_message_id

    watermark = get_sync_watermark(conversation)
    if _is_older_external_message(fetched_oldest, watermark.oldest_external_message_id):
        watermark.oldest_external_message_id = fetched_oldest
    if _is_newer_external_message(fetched_latest, watermark.latest_external_message_id):
        watermark.latest_external_message_id = fetched_latest

    if len(messages) < limit:
        if before_external_message_id and watermark.oldest_external_message_id == fetched_oldest:
            watermark.oldest_complete = True
        if after_external_message_id and watermark.latest_external_message_id == fetched_latest:
            watermark.latest_complete = True
        if (
            after_external_message_id is None
            and before_external_message_id is None
            and watermark.latest_external_message_id == fetched_latest
        ):
            watermark.latest_complete = True
            watermark.oldest_complete = True

    write_sync_watermark(conversation, watermark)


def mark_boundary_complete(
    *,
    conversation: Conversation,
    boundary: str,
    external_message_id: str,
) -> None:
    watermark = get_sync_watermark(conversation)
    if boundary == "oldest":
        if _is_older_external_message(external_message_id, watermark.oldest_external_message_id):
            watermark.oldest_external_message_id = external_message_id
        elif watermark.oldest_external_message_id is None:
            watermark.oldest_external_message_id = external_message_id
        watermark.oldest_complete = True
    elif boundary == "latest":
        if _is_newer_external_message(external_message_id, watermark.latest_external_message_id):
            watermark.latest_external_message_id = external_message_id
        elif watermark.latest_external_message_id is None:
            watermark.latest_external_message_id = external_message_id
        watermark.latest_complete = True
    write_sync_watermark(conversation, watermark)


def write_sync_watermark(
    conversation: Conversation,
    watermark: ConversationSyncWatermark,
) -> None:
    state = get_customer_conversation_state(conversation)
    sync_state = state.sync or ConversationSyncState()
    sync_state.watermarks = ConversationSyncWatermarks(
        oldest_external_message_id=watermark.oldest_external_message_id,
        latest_external_message_id=watermark.latest_external_message_id,
        oldest_complete=watermark.oldest_complete,
        latest_complete=watermark.latest_complete,
    )
    state.sync = sync_state
    set_customer_conversation_state(conversation, state)


def _safe_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _safe_str(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _external_message_sort_key(value: str | None) -> tuple[int, int | str]:
    if value is None:
        return (0, "")
    parsed = _safe_int(value)
    if parsed is not None:
        return (1, parsed)
    return (0, value)


def _is_older_external_message(candidate: str | None, current: str | None) -> bool:
    if candidate is None:
        return False
    if current is None:
        return True
    candidate_int = _safe_int(candidate)
    current_int = _safe_int(current)
    if candidate_int is not None and current_int is not None:
        return candidate_int < current_int
    return candidate < current


def _is_newer_external_message(candidate: str | None, current: str | None) -> bool:
    if candidate is None:
        return False
    if current is None:
        return True
    candidate_int = _safe_int(candidate)
    current_int = _safe_int(current)
    if candidate_int is not None and current_int is not None:
        return candidate_int > current_int
    return candidate > current
