from __future__ import annotations

from app.models.conversation import Conversation
from app.models.message import Message
from app.services.conversation_state import (
    ConversationSyncState,
    ConversationSyncWatermarks,
    external_cursor_for_message,
    get_customer_conversation_state,
    has_exhausted_older_history,
    project_visible_gap_repair_request,
    set_customer_conversation_state,
    should_surface_older_history_from_state,
)


def _conversation_with_watermark(
    *,
    oldest_external_message_id: str | None,
    oldest_complete: bool,
) -> Conversation:
    conversation = Conversation(
        workspace_id=1,
        customer_id=1,
        channel="telegram_dm",
        telegram_chat_id=9001,
        external_chat_id="9001",
    )
    state = get_customer_conversation_state(conversation)
    state.sync = ConversationSyncState(
        watermarks=ConversationSyncWatermarks(
            oldest_external_message_id=oldest_external_message_id,
            latest_external_message_id="100",
            oldest_complete=oldest_complete,
            latest_complete=True,
        )
    )
    set_customer_conversation_state(conversation, state)
    return conversation


def test_has_exhausted_older_history_uses_canonical_sync_watermark():
    conversation = _conversation_with_watermark(
        oldest_external_message_id="500",
        oldest_complete=True,
    )

    assert has_exhausted_older_history(conversation, external_cursor="499") is True
    assert has_exhausted_older_history(conversation, external_cursor="500") is True
    assert has_exhausted_older_history(conversation, external_cursor="501") is False


def test_has_exhausted_older_history_requires_complete_watermark():
    conversation = _conversation_with_watermark(
        oldest_external_message_id="500",
        oldest_complete=False,
    )

    assert has_exhausted_older_history(conversation, external_cursor="499") is False


def test_should_surface_older_history_from_state_without_route_sync_helper():
    conversation = _conversation_with_watermark(
        oldest_external_message_id="500",
        oldest_complete=True,
    )
    oldest_message = Message(
        conversation_id=1,
        telegram_message_id=500,
        external_message_id="500",
    )

    assert external_cursor_for_message(oldest_message) == "500"
    assert should_surface_older_history_from_state(
        conversation,
        page_has_older=False,
        oldest_message=oldest_message,
    ) is False


def test_should_surface_older_history_when_watermark_is_not_complete():
    conversation = _conversation_with_watermark(
        oldest_external_message_id="500",
        oldest_complete=False,
    )
    oldest_message = Message(
        conversation_id=1,
        telegram_message_id=500,
        external_message_id="500",
    )

    assert should_surface_older_history_from_state(
        conversation,
        page_has_older=False,
        oldest_message=oldest_message,
    ) is True


def test_project_visible_gap_repair_request_returns_newer_gap_edge():
    conversation = _conversation_with_watermark(
        oldest_external_message_id="100",
        oldest_complete=False,
    )
    messages = [
        Message(conversation_id=1, telegram_message_id=100),
        Message(conversation_id=1, telegram_message_id=130),
    ]

    request = project_visible_gap_repair_request(conversation, messages=messages)

    assert request is not None
    assert request.reason == "visible_telegram_id_gap"
    assert request.before_external_message_id == "130"
