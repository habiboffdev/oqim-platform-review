from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.conversation_core.read_state import (
    mark_conversation_read,
    mark_inbox_read_up_to,
    update_read_outbox_max,
)
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationSyncState,
    get_customer_conversation_state,
    set_customer_conversation_state,
)

pytestmark = pytest.mark.asyncio


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    return result


def _make_db(*execute_results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(execute_results))
    db.commit = AsyncMock()
    return db


class TestMarkConversationRead:
    async def test_marks_conversation_read_and_returns_max_message_id(self):
        conversation = MagicMock(id=7, workspace_id=1)
        db = _make_db(
            _result(conversation),
            MagicMock(),
            _result(555),
        )

        state = await mark_conversation_read(
            db,
            workspace_id=1,
            conversation_id=7,
        )

        assert state is not None
        assert state.conversation is conversation
        assert state.unread_count == 0
        assert state.max_message_id == 555
        db.commit.assert_awaited_once()

    async def test_clears_dialog_projection_unread_count(self):
        conversation = MagicMock(id=7, workspace_id=1)
        conversation.crm_state = None
        state = get_customer_conversation_state(conversation)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(telegram_unread_count=4)
        )
        set_customer_conversation_state(conversation, state)
        db = _make_db(
            _result(conversation),
            MagicMock(),
            _result(555),
        )

        await mark_conversation_read(
            db,
            workspace_id=1,
            conversation_id=7,
        )

        cleared = get_customer_conversation_state(conversation)
        assert cleared.sync is not None
        assert cleared.sync.dialog is not None
        assert cleared.sync.dialog.telegram_unread_count == 0

    async def test_returns_none_for_missing_conversation(self):
        db = _make_db(_result(None))

        state = await mark_conversation_read(
            db,
            workspace_id=1,
            conversation_id=7,
        )

        assert state is None
        db.commit.assert_not_awaited()


class TestMarkInboxReadUpTo:
    async def test_marks_inbox_read_up_to_max_id(self):
        conversation = MagicMock(id=9, workspace_id=1)
        db = _make_db(_result(conversation), MagicMock())

        state = await mark_inbox_read_up_to(
            db,
            workspace_id=1,
            telegram_chat_id=123456,
            max_id=400,
        )

        assert state is not None
        assert state.conversation is conversation
        assert state.max_message_id == 400
        assert state.unread_count == 0
        db.commit.assert_awaited_once()

    async def test_zero_max_id_is_noop(self):
        db = _make_db()

        state = await mark_inbox_read_up_to(
            db,
            workspace_id=1,
            telegram_chat_id=123456,
            max_id=0,
        )

        assert state is None
        db.execute.assert_not_awaited()


class TestUpdateReadOutboxMax:
    async def test_updates_when_new_max_is_higher(self):
        conversation = MagicMock(id=11, read_outbox_max_id=300)
        db = _make_db(_result(conversation))

        state = await update_read_outbox_max(
            db,
            workspace_id=1,
            telegram_chat_id=123456,
            max_id=500,
        )

        assert state is not None
        assert conversation.read_outbox_max_id == 500
        assert state.read_outbox_max_id == 500
        db.commit.assert_awaited_once()

    async def test_keeps_existing_max_when_receipt_is_older(self):
        conversation = MagicMock(id=11, read_outbox_max_id=300)
        db = _make_db(_result(conversation))

        state = await update_read_outbox_max(
            db,
            workspace_id=1,
            telegram_chat_id=123456,
            max_id=200,
        )

        assert state is not None
        assert state.read_outbox_max_id == 300
        db.commit.assert_not_awaited()
