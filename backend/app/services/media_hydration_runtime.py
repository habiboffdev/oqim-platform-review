from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.modules.conversation_turns.service import ConversationTurnSessionService
from app.services.channel_media_access import ChannelMediaAccess, MediaHydrationResult
from app.services.conversation_state import sync_media_readiness_for_conversation


@dataclass(slots=True)
class MediaHydrationActionResult:
    runtime_id: int
    conversation_id: int | None
    message_id: int | None
    status: str
    should_wake_agent_turn: bool = False


async def hydrate_media_runtime_job(
    session: AsyncSession,
    *,
    workspace_id: int,
    runtime: MediaRuntime,
    fetch_media: Callable[[], Awaitable[tuple[bytes, str] | None]] | None = None,
    media_access: ChannelMediaAccess | None = None,
) -> MediaHydrationActionResult:
    """Hydrate one leased media-runtime row without depending on reply dispatch."""
    if runtime.workspace_id != workspace_id:
        return MediaHydrationActionResult(
            runtime_id=runtime.id,
            conversation_id=runtime.conversation_id,
            message_id=runtime.message_id,
            status="workspace_mismatch",
        )

    conversation = await session.get(Conversation, runtime.conversation_id)
    message = await session.get(Message, runtime.message_id)
    if (
        conversation is None
        or message is None
        or conversation.workspace_id != workspace_id
        or message.conversation_id != conversation.id
    ):
        return MediaHydrationActionResult(
            runtime_id=runtime.id,
            conversation_id=getattr(conversation, "id", runtime.conversation_id),
            message_id=getattr(message, "id", runtime.message_id),
            status="missing_projection",
        )

    hydration: MediaHydrationResult = await (media_access or ChannelMediaAccess()).hydrate_for_ai(
        session=session,
        workspace_id=workspace_id,
        conversation=conversation,
        message=message,
        fetch_media=fetch_media,
    )
    should_wake_agent_turn = hydration.status in {"hydrated", "unavailable"}
    if should_wake_agent_turn:
        customer = await session.get(Customer, conversation.customer_id)
        if customer is not None and customer.workspace_id == workspace_id:
            await sync_media_readiness_for_conversation(
                session=session,
                conversation=conversation,
            )
            await session.flush()
            await ConversationTurnSessionService(session).append_customer_message(
                workspace_id=workspace_id,
                conversation=conversation,
                customer=customer,
                message=message,
            )

    return MediaHydrationActionResult(
        runtime_id=runtime.id,
        conversation_id=conversation.id,
        message_id=message.id,
        status=hydration.status,
        should_wake_agent_turn=should_wake_agent_turn,
    )
