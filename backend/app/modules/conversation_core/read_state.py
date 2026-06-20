from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message import Message, SenderType
from app.services.conversation_state import (
    get_customer_conversation_state,
    set_customer_conversation_state,
)


@dataclass(slots=True)
class ConversationReadState:
    conversation: Conversation
    unread_count: int = 0
    max_message_id: int | None = None


@dataclass(slots=True)
class OutboxReadState:
    conversation: Conversation
    read_outbox_max_id: int


async def mark_conversation_read(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation_id: int,
) -> ConversationReadState | None:
    conversation = await _get_conversation_by_id(session, workspace_id, conversation_id)
    if not conversation:
        return None

    await session.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.sender_type == SenderType.CUSTOMER.value,
            Message.is_read.is_(False),
        )
        .values(is_read=True)
    )

    max_msg_result = await session.execute(
        select(func.max(Message.telegram_message_id)).where(
            Message.conversation_id == conversation_id
        )
    )
    max_message_id = max_msg_result.scalar()
    _clear_dialog_unread(conversation)
    await session.commit()

    return ConversationReadState(
        conversation=conversation,
        unread_count=0,
        max_message_id=max_message_id,
    )


async def mark_inbox_read_up_to(
    session: AsyncSession,
    *,
    workspace_id: int,
    telegram_chat_id: int,
    max_id: int,
) -> ConversationReadState | None:
    if max_id <= 0:
        return None

    conversation = await _get_conversation_by_chat(session, workspace_id, telegram_chat_id)
    if not conversation:
        return None

    await session.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.telegram_message_id <= max_id,
            Message.sender_type == SenderType.CUSTOMER.value,
            Message.is_read.is_(False),
        )
        .values(is_read=True)
    )
    _clear_dialog_unread(conversation)
    await session.commit()

    return ConversationReadState(conversation=conversation, unread_count=0, max_message_id=max_id)


async def update_read_outbox_max(
    session: AsyncSession,
    *,
    workspace_id: int,
    telegram_chat_id: int,
    max_id: int,
) -> OutboxReadState | None:
    if max_id <= 0:
        return None

    conversation = await _get_conversation_by_chat(session, workspace_id, telegram_chat_id)
    if not conversation:
        return None

    effective_max = conversation.read_outbox_max_id or 0
    if conversation.read_outbox_max_id is None or max_id > conversation.read_outbox_max_id:
        conversation.read_outbox_max_id = max_id
        effective_max = max_id
        await session.commit()

    return OutboxReadState(conversation=conversation, read_outbox_max_id=effective_max)


def _clear_dialog_unread(conversation: Conversation) -> None:
    state = get_customer_conversation_state(conversation)
    if state.sync is None or state.sync.dialog is None:
        return
    state.sync.dialog.telegram_unread_count = 0
    set_customer_conversation_state(conversation, state)


async def _get_conversation_by_id(
    session: AsyncSession,
    workspace_id: int,
    conversation_id: int,
) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none()


async def _get_conversation_by_chat(
    session: AsyncSession,
    workspace_id: int,
    telegram_chat_id: int,
) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(
            Conversation.workspace_id == workspace_id,
            Conversation.telegram_chat_id == telegram_chat_id,
        )
    )
    return result.scalar_one_or_none()
