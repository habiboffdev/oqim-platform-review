import pytest

from app.services.sync_session import build_sync_session
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationSyncState,
    get_customer_conversation_state,
    set_customer_conversation_state,
)

pytestmark = pytest.mark.asyncio


async def test_sync_session_returns_noop_when_client_sequence_is_current(db_session, workspace):
    response = await build_sync_session(
        session=db_session,
        workspace_id=workspace.id,
        server_sequence=7,
        client_sequence=7,
    )

    assert response.kind == "noop"
    assert response.action == "noop"
    assert response.projections == ()


async def test_sync_session_returns_scoped_message_delta_for_bounded_gap(
    db_session,
    workspace,
    conversation,
):
    conversation.message_sequence = 12
    conversation.message_revision = 12
    await db_session.flush()

    response = await build_sync_session(
        session=db_session,
        workspace_id=workspace.id,
        server_sequence=22,
        client_sequence=10,
        active_conversation_id=conversation.id,
        last_seen_conversation_seq=7,
        last_seen_conversation_revision=7,
    )

    assert response.kind == "delta"
    assert response.action == "refresh_scoped_runtime_delta"
    assert response.after_conversation_seq == 7
    projections = response.to_websocket_data()["projections"]
    assert {
        "name": "messages",
        "mode": "delta",
        "conversation_id": conversation.id,
        "after_conversation_seq": 7,
        "latest_conversation_seq": 12,
        "latest_conversation_revision": 12,
    } in projections
    assert {
        "name": "seller_agent_replies",
        "mode": "reset",
        "conversation_id": conversation.id,
    } in projections
    assert all(projection["name"] != "drafts" for projection in projections)
    assert response.to_websocket_data()["conversation_state"] == {
        "last_message_text": None,
        "last_message_at": conversation.last_message_at.isoformat(),
        "unread_count": 0,
        "latest_conversation_seq": 12,
        "latest_conversation_revision": 12,
    }


async def test_sync_session_carries_canonical_dialog_projection(
    db_session,
    workspace,
    conversation,
):
    conversation.message_sequence = 12
    conversation.message_revision = 12
    state = get_customer_conversation_state(conversation)
    state.sync = ConversationSyncState(
        dialog=ConversationDialogState(
            telegram_unread_count=4,
            last_message_text="Canonical reconnect preview",
            last_message_date="2026-04-28T01:30:00+00:00",
        )
    )
    set_customer_conversation_state(conversation, state)
    await db_session.flush()

    response = await build_sync_session(
        session=db_session,
        workspace_id=workspace.id,
        server_sequence=22,
        client_sequence=10,
        active_conversation_id=conversation.id,
        last_seen_conversation_seq=7,
        last_seen_conversation_revision=7,
    )

    assert response.to_websocket_data()["conversation_state"] == {
        "last_message_text": "Canonical reconnect preview",
        "last_message_at": conversation.last_message_at.isoformat(),
        "unread_count": 4,
        "latest_conversation_seq": 12,
        "latest_conversation_revision": 12,
    }


async def test_sync_session_resets_scoped_runtime_when_revision_drift_is_not_message_only(
    db_session,
    workspace,
    conversation,
):
    conversation.message_sequence = 12
    conversation.message_revision = 15
    await db_session.flush()

    response = await build_sync_session(
        session=db_session,
        workspace_id=workspace.id,
        server_sequence=22,
        client_sequence=10,
        active_conversation_id=conversation.id,
        last_seen_conversation_seq=7,
        last_seen_conversation_revision=7,
    )

    assert response.kind == "reset_required"
    assert response.action == "refresh_scoped_runtime"
    assert {
        "name": "messages",
        "mode": "reset",
        "conversation_id": conversation.id,
    } in response.to_websocket_data()["projections"]
