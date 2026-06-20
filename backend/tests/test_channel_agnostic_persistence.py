"""TDD tests for channel-agnostic message persistence (Issue #36).

Tests that persist_message works for multiple channels (telegram_dm, instagram_dm)
and maintains backward compatibility with existing Telegram-only callers.
"""

import pytest

from app.modules.conversation_core.service import (
    PersistMessageInput,
    persist_message,
)
from app.services.conversation_state import (
    get_customer_conversation_state,
    project_dialog_last_message_text,
    project_dialog_unread_count,
)

pytestmark = pytest.mark.asyncio


class TestPersistMessageChannelAgnostic:
    """persist_message stores messages with the correct channel."""

    async def test_instagram_dm_channel(self, db_session, workspace):
        """Instagram DM messages get channel='instagram_dm'."""
        result = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="instagram_dm",
                sender_id=99001,
                sender_name="Malika",
                text="Narxi qancha?",
                is_outgoing=False,
                external_message_id="ig_msg_12345",
            ),
        )

        assert result.message.channel == "instagram_dm"
        assert result.message.content == "Narxi qancha?"
        assert result.conversation.channel == "instagram_dm"
        assert not result.is_duplicate

    async def test_telegram_dm_channel(self, db_session, workspace):
        """Telegram DM messages get channel='telegram_dm'."""
        result = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="telegram_dm",
                telegram_chat_id=123456,
                sender_id=99002,
                sender_name="Alisher",
                text="iPhone bormi?",
                is_outgoing=False,
                telegram_message_id=5001,
            ),
        )

        assert result.message.channel == "telegram_dm"
        assert result.conversation.channel == "telegram_dm"

    async def test_live_inbound_updates_dialog_projection(self, db_session, workspace):
        """Canonical persistence keeps chat-list projection in sync with history."""
        result = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="telegram_dm",
                telegram_chat_id=123459,
                sender_id=99005,
                sender_name="Latest Sender",
                text="fresh unread",
                is_outgoing=False,
                telegram_message_id=5003,
            ),
        )

        state = get_customer_conversation_state(result.conversation)
        assert state.sync is not None
        assert state.sync.dialog is not None
        assert state.sync.dialog.top_message_id == 5003
        assert state.sync.dialog.last_message_text == "fresh unread"
        assert state.sync.dialog.last_message_is_outgoing is False
        assert project_dialog_unread_count(result.conversation) == 1
        assert (
            project_dialog_last_message_text(
                result.conversation,
                local_text=None,
                local_at=None,
            )
            == "fresh unread"
        )

    async def test_outgoing_message_clears_dialog_unread(self, db_session, workspace):
        """Seller replies should move the projection forward and clear unread."""
        inbound = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="telegram_dm",
                telegram_chat_id=123460,
                sender_id=99006,
                sender_name="Buyer",
                text="customer ping",
                is_outgoing=False,
                telegram_message_id=5004,
            ),
        )

        result = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="telegram_dm",
                telegram_chat_id=123460,
                sender_id=99006,
                sender_name="Buyer",
                text="seller answer",
                is_outgoing=True,
                telegram_message_id=5005,
            ),
        )

        assert result.conversation.id == inbound.conversation.id
        state = get_customer_conversation_state(result.conversation)
        assert state.sync is not None
        assert state.sync.dialog is not None
        assert state.sync.dialog.top_message_id == 5005
        assert state.sync.dialog.last_message_text == "seller answer"
        assert state.sync.dialog.last_message_is_outgoing is True
        assert project_dialog_unread_count(result.conversation) == 0

    async def test_default_channel_is_telegram_dm(self, db_session, workspace):
        """When no channel specified, defaults to telegram_dm (backward compat)."""
        result = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                telegram_chat_id=123457,
                sender_id=99003,
                sender_name="Oybek",
                text="Salom",
                is_outgoing=False,
            ),
        )

        assert result.message.channel == "telegram_dm"
        assert result.conversation.channel == "telegram_dm"

    async def test_persists_media_runtime_descriptor_immediately(self, db_session, workspace):
        """AI-relevant media stores descriptor metadata before hydration runs."""
        result = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="telegram_dm",
                telegram_chat_id=123458,
                sender_id=99004,
                sender_name="Nigina",
                text="[voice] Mijoz ovozli xabar yubordi",
                is_outgoing=False,
                telegram_message_id=5002,
                media_type="voice",
            ),
        )

        assert result.message.media_metadata["ai_relevant"] is True
        assert result.message.media_metadata["hydration_status"] == "pending"
        assert (
            result.message.media_metadata["descriptor_text"]
            == "[voice] Mijoz ovozli xabar yubordi"
        )

    async def test_normalizes_gramjs_photo_before_media_runtime_projection(self, db_session, workspace):
        result = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="telegram_dm",
                telegram_chat_id=123459,
                sender_id=99005,
                sender_name="Dilorom",
                text="",
                is_outgoing=False,
                telegram_message_id=5003,
                media_type="MessageMediaPhoto",
                media_metadata={"mime_type": "image/jpeg"},
            ),
        )

        assert result.message.media_type == "photo"
        assert result.message.media_metadata["ai_relevant"] is True
        assert result.message.media_metadata["hydration_status"] == "pending"


class TestPersistMessageDedup:
    """Deduplication works across channels."""

    async def test_telegram_dedup_by_telegram_message_id(self, db_session, workspace):
        """Same telegram_message_id in same conversation is a duplicate."""
        input_data = PersistMessageInput(
            workspace_id=workspace.id,
            channel="telegram_dm",
            telegram_chat_id=200000,
            sender_id=99010,
            sender_name="Jasur",
            text="Test message",
            is_outgoing=False,
            telegram_message_id=7001,
        )

        first = await persist_message(db_session, input_data)
        second = await persist_message(db_session, input_data)

        assert not first.is_duplicate
        assert second.is_duplicate
        assert first.message.id == second.message.id

    async def test_instagram_dedup_by_external_message_id(self, db_session, workspace):
        """Same external_message_id in same conversation is a duplicate."""
        input_data = PersistMessageInput(
            workspace_id=workspace.id,
            channel="instagram_dm",
            sender_id=99011,
            sender_name="Dilnoza",
            text="Bu bormi?",
            is_outgoing=False,
            external_message_id="ig_msg_99999",
        )

        first = await persist_message(db_session, input_data)
        second = await persist_message(db_session, input_data)

        assert not first.is_duplicate
        assert second.is_duplicate


class TestPersistMessageWorkspaceIsolation:
    """Messages from different workspaces don't leak."""

    async def test_different_workspace_same_sender(
        self, db_session, workspace, workspace_b
    ):
        """Same sender_id in different workspaces creates separate conversations."""
        result_a = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                channel="instagram_dm",
                sender_id=88001,
                sender_name="Shared Customer",
                text="Hello workspace A",
                is_outgoing=False,
            ),
        )
        result_b = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace_b.id,
                channel="instagram_dm",
                sender_id=88001,
                sender_name="Shared Customer",
                text="Hello workspace B",
                is_outgoing=False,
            ),
        )

        assert result_a.conversation.id != result_b.conversation.id
        assert result_a.conversation.workspace_id == workspace.id
        assert result_b.conversation.workspace_id == workspace_b.id
