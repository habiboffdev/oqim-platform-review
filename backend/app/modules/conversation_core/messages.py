from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.message import Message


@dataclass(slots=True)
class PaginatedMessagePage:
    items: list[Message]
    has_older: bool


async def get_paginated_message_page(
    session: AsyncSession,
    *,
    conversation: Conversation,
    limit: int,
    before_id: int | None = None,
    after_conversation_seq: int | None = None,
) -> PaginatedMessagePage:
    """Return messages in chronological order with a stable older-page cursor."""
    effective_time = func.coalesce(Message.telegram_timestamp, Message.created_at)
    channel_order_key = func.coalesce(Message.telegram_message_id, Message.id)
    query = (
        select(Message)
        .where(
            Message.conversation_id == conversation.id,
        )
    )

    if after_conversation_seq is not None:
        query = query.where(Message.conversation_seq > after_conversation_seq)
        query = query.order_by(
            Message.conversation_seq.asc().nullslast(),
            Message.id.asc(),
        ).limit(limit)
        result = await session.execute(query)
        items = result.scalars().all()

        return PaginatedMessagePage(items=items, has_older=False)

    if before_id:
        cursor_result = await session.execute(
            select(
                Message.id,
                effective_time.label("effective_time"),
                channel_order_key.label("channel_order_key"),
            ).where(
                Message.id == before_id,
                Message.conversation_id == conversation.id,
            )
        )
        cursor_row = cursor_result.first()
        if cursor_row:
            query = query.where(
                or_(
                    effective_time < cursor_row.effective_time,
                    and_(
                        effective_time == cursor_row.effective_time,
                        channel_order_key < cursor_row.channel_order_key,
                    ),
                )
            )

    query = query.order_by(
        effective_time.desc(),
        channel_order_key.desc(),
        Message.id.desc(),
    ).limit(limit + 1)
    result = await session.execute(query)
    desc_messages = result.scalars().all()

    has_older = len(desc_messages) > limit
    trimmed = desc_messages[:limit] if has_older else desc_messages
    items = list(reversed(trimmed))

    return PaginatedMessagePage(items=items, has_older=has_older)
