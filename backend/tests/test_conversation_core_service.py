from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.conversation_core.service import (
    PersistMessageInput,
    create_seller_placeholder_message,
    persist_message,
)

pytestmark = pytest.mark.asyncio
_UNSET = object()


def _scalar_result(*, one=_UNSET, one_or_none=_UNSET):
    result = MagicMock()
    if one is not _UNSET:
        result.scalar_one.return_value = one
        result.scalar_one_or_none.return_value = one
    if one_or_none is not _UNSET:
        result.scalar_one_or_none.return_value = one_or_none
        result.scalar_one.return_value = one_or_none
    return result


def _make_db(*results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(results))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


class TestPersistTelegramMessage:
    async def test_existing_conversation_projection_does_not_lazy_load_customer(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        """Persist consumer must not crash when Conversation.customer is not preloaded."""
        customer = Customer(
            workspace_id=workspace.id,
            display_name="Azim",
            telegram_id=998877,
            external_id="998877",
            channel="telegram_dm",
        )
        db_session.add(customer)
        await db_session.flush()
        conversation = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=998877,
            external_chat_id="998877",
        )
        db_session.add(conversation)
        await db_session.flush()
        db_session.expunge_all()

        result = await persist_message(
            db_session,
            PersistMessageInput(
                workspace_id=workspace.id,
                telegram_chat_id=998877,
                sender_id=998877,
                sender_name="Azim",
                text="Yetib keldi",
                is_outgoing=False,
                telegram_message_id=303,
                message_ts=datetime.now(timezone.utc),
            ),
        )

        assert result.message.telegram_message_id == 303
        assert result.message.conversation_id == conversation.id

    async def test_creates_customer_conversation_and_message(self):
        customer = MagicMock(id=11, display_name="Dilshod")
        conversation = MagicMock(id=22, channel="dm", telegram_chat_id=998877)
        db = _make_db(
            _scalar_result(one=customer),
            _scalar_result(one=conversation),
            _scalar_result(one_or_none=None),
            _scalar_result(one=1),
        )

        result = await persist_message(
            db,
            PersistMessageInput(
                workspace_id=1,
                telegram_chat_id=998877,
                sender_id=445566,
                sender_name="Dilshod",
                text="Assalomu alaykum",
                is_outgoing=False,
                telegram_message_id=101,
                message_ts=datetime.now(timezone.utc),
            ),
        )

        assert result.is_duplicate is False
        assert result.customer is customer
        assert result.conversation is conversation
        assert isinstance(result.message, Message)
        assert result.message.sender_type == SenderType.CUSTOMER.value
        assert result.message.telegram_message_id == 101
        assert result.message.conversation_seq == 1
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(result.message)

    async def test_returns_duplicate_without_committing(self):
        customer = MagicMock(id=11, display_name="Dilshod")
        conversation = MagicMock(id=22, channel="dm", telegram_chat_id=998877)
        duplicate_message = MagicMock(id=44)
        db = _make_db(
            _scalar_result(one=customer),
            _scalar_result(one=conversation),
            _scalar_result(one_or_none=duplicate_message),
        )

        result = await persist_message(
            db,
            PersistMessageInput(
                workspace_id=1,
                telegram_chat_id=998877,
                sender_id=445566,
                sender_name="Dilshod",
                text="Assalomu alaykum",
                is_outgoing=False,
                telegram_message_id=101,
                message_ts=datetime.now(timezone.utc),
            ),
        )

        assert result.is_duplicate is True
        assert result.message is duplicate_message
        db.commit.assert_not_awaited()
        db.refresh.assert_not_awaited()

    async def test_returns_duplicate_when_concurrent_replay_wins_insert_race(self):
        customer = MagicMock(id=11, display_name="Dilshod")
        conversation = MagicMock(id=22, channel="dm", telegram_chat_id=998877)
        duplicate_message = MagicMock(id=44)
        reloaded_customer = MagicMock(id=11, display_name="Dilshod")
        reloaded_conversation = MagicMock(id=22, channel="dm", telegram_chat_id=998877)
        db = _make_db(
            _scalar_result(one=customer),
            _scalar_result(one=conversation),
            _scalar_result(one_or_none=None),
            _scalar_result(one=1),
            _scalar_result(one_or_none=duplicate_message),
            _scalar_result(one_or_none=reloaded_customer),
            _scalar_result(one_or_none=reloaded_conversation),
        )
        db.commit.side_effect = IntegrityError("insert", {}, Exception("duplicate message"))

        result = await persist_message(
            db,
            PersistMessageInput(
                workspace_id=1,
                telegram_chat_id=998877,
                sender_id=445566,
                sender_name="Dilshod",
                text="Assalomu alaykum",
                is_outgoing=False,
                telegram_message_id=101,
                message_ts=datetime.now(timezone.utc),
            ),
        )

        assert result.is_duplicate is True
        assert result.customer is reloaded_customer
        assert result.conversation is reloaded_conversation
        assert result.message is duplicate_message
        db.rollback.assert_awaited_once()
        db.refresh.assert_not_awaited()

    async def test_marks_outgoing_messages_as_read(self):
        customer = MagicMock(id=11, display_name="Dilshod")
        conversation = MagicMock(id=22, channel="dm", telegram_chat_id=998877)
        empty_placeholder_result = MagicMock()
        empty_placeholder_result.scalars.return_value.all.return_value = []
        db = _make_db(
            _scalar_result(one=customer),
            _scalar_result(one=conversation),
            _scalar_result(one_or_none=None),
            empty_placeholder_result,
            _scalar_result(one=1),
        )

        result = await persist_message(
            db,
            PersistMessageInput(
                workspace_id=1,
                telegram_chat_id=998877,
                sender_id=998877,
                sender_name="Dilshod",
                text="Bor, yuboraman",
                is_outgoing=True,
                telegram_message_id=202,
                message_ts=datetime.now(timezone.utc),
            ),
        )

        assert result.message.sender_type == SenderType.SELLER.value
        assert result.message.is_read is True
        assert result.message.conversation_seq == 1

    async def test_reconciles_outgoing_placeholder_on_real_telegram_echo(self):
        customer = MagicMock(id=11, display_name="Dilshod")
        conversation = MagicMock(id=22, channel="dm", telegram_chat_id=998877)
        placeholder = MagicMock(
            id=55,
            sender_type=SenderType.SELLER.value,
            content="Bor, yozib yuboraman",
            telegram_message_id=None,
            created_at=datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc),
        )
        placeholder_result = MagicMock()
        placeholder_result.scalars.return_value.all.return_value = [placeholder]
        db = _make_db(
            _scalar_result(one=customer),
            _scalar_result(one=conversation),
            _scalar_result(one_or_none=None),
            placeholder_result,
        )
        message_ts = datetime(2026, 4, 6, 12, 0, 5, tzinfo=timezone.utc)

        result = await persist_message(
            db,
            PersistMessageInput(
                workspace_id=1,
                telegram_chat_id=998877,
                sender_id=998877,
                sender_name="Dilshod",
                text="Bor, yozib yuboraman",
                is_outgoing=True,
                telegram_message_id=202,
                message_ts=message_ts,
            ),
        )

        assert result.is_duplicate is False
        assert result.message is placeholder
        assert placeholder.telegram_message_id == 202
        assert placeholder.telegram_timestamp == message_ts
        assert placeholder.created_at == message_ts
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(placeholder)


class TestCreateSellerPlaceholderMessage:
    async def test_creates_local_seller_message(self):
        conversation = MagicMock(id=22, channel="dm", last_message_at=None)
        db = _make_db(_scalar_result(one=1))

        message = await create_seller_placeholder_message(
            db,
            conversation=conversation,
            content="Yuborib beraman",
            client_message_uuid="send-123",
        )

        assert isinstance(message, Message)
        assert message.sender_type == SenderType.SELLER.value
        assert message.content == "Yuborib beraman"
        assert message.is_read is True
        assert message.client_message_uuid == "send-123"
        assert message.conversation_seq == 1
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(message)
