"""Explicit conversation command routes.

Read/projection routes live in `conversations.py`. This module owns deliberate
side effects: hydrate on chat-open, send seller message, update OQIM
Intelligence customer state, and mark read.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.routes.conversations import (
    _build_conversation_response,
    _count_unread_customer_messages,
    _load_latest_local_message_preview,
)
from app.core.deps import (
    get_current_workspace,
    get_db_session,
    get_delivery_service,
)
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.delivery_runtime import DeliveryRuntime
from app.models.message import Message
from app.models.workspace import Workspace
from app.modules.conversation_core import read_state as conversation_read_state
from app.modules.conversation_core.service import create_seller_placeholder_message
from app.modules.conversation_turns.service import ConversationTurnSessionService
from app.schemas.conversation import ConversationResponse, ConversationUpdate
from app.schemas.message import MessageResponse
from app.services.channel_adapter_contract import (
    UnsupportedChannelCapability,
    get_channel_adapter,
)
from app.services.conversation_hydration_runtime import (
    conversation_needs_hydration,
    enqueue_conversation_hydration,
    latest_local_message_for_conversation,
    project_conversation_hydration_runtime,
)
from app.services.conversation_state import (
    apply_manual_field_override,
    get_customer_conversation_state,
    project_conversation_tail,
    set_customer_conversation_state,
)
from app.services.delivery import DeliveryService
from app.services.delivery_runtime import (
    DELIVERY_CONFIRMED,
    DELIVERY_RECONCILED,
    DELIVERY_REQUESTED,
    DELIVERY_SENDING,
    DELIVERY_UNKNOWN,
)
from app.services.message_response_projection import serialize_message_response

logger = get_logger("api.conversation_commands")

router = APIRouter(prefix="/conversations", tags=["conversation-commands"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
DeliveryDep = Annotated[DeliveryService, Depends(get_delivery_service)]


class SendMessageRequest(BaseModel):
    content: str
    client_message_uuid: str | None = None


class HydrateConversationRequest(BaseModel):
    limit: int = 100


class HydrateConversationResponse(BaseModel):
    requested: int
    persisted: int
    duplicates: int
    unread_count: int = 0
    sync_status: str = "not_run"
    tail: dict | None = None
    hydration: dict | None = None


@router.post("/{conversation_id}/hydrate", response_model=HydrateConversationResponse)
async def hydrate_conversation_messages(
    conversation_id: int,
    body: HydrateConversationRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    """Queue chat-open hydration without performing route-time history fetch."""
    conv = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace.id,
        )
    )
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    read_state = await conversation_read_state.mark_conversation_read(
        session,
        workspace_id=workspace.id,
        conversation_id=conversation_id,
    )
    if read_state:
        conv = read_state.conversation
        await _mark_remote_read(
            session=session,
            workspace_id=workspace.id,
            conversation_id=conversation_id,
            read_state=read_state,
        )
    runtime = await enqueue_conversation_hydration(
        session,
        workspace_id=workspace.id,
        conversation=conv,
        reason="chat_open",
        requested_limit=body.limit,
    )
    await session.commit()
    latest_local_message = await latest_local_message_for_conversation(
        session,
        conversation_id=conv.id,
    )
    local_text, local_at = await _load_latest_local_message_preview(session, conv.id)
    tail = project_conversation_tail(
        conv,
        local_text=local_text,
        local_at=local_at,
        db_unread_count=read_state.unread_count if read_state else 0,
    )
    hydration = project_conversation_hydration_runtime(
        runtime,
        needed=conversation_needs_hydration(
            conv,
            latest_local_message=latest_local_message,
        ),
    )
    return HydrateConversationResponse(
        requested=0,
        persisted=0,
        duplicates=0,
        unread_count=read_state.unread_count if read_state else 0,
        sync_status=hydration.state,
        tail=tail.to_payload(),
        hydration=hydration.to_payload(),
    )


@router.post("/{conversation_id}/send-message", response_model=MessageResponse)
async def send_message(
    conversation_id: int,
    body: SendMessageRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
    delivery: DeliveryDep,
):
    """Send a seller message: save to DB, deliver via DeliveryService."""
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace.id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    channel = (conv.channel or "telegram_dm").strip().lower()
    is_telegram = channel in {"telegram_dm", "dm"}
    if is_telegram and conv.telegram_chat_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Message delivery temporarily unavailable",
        )
    if not is_telegram and not conv.external_chat_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Message delivery temporarily unavailable",
        )

    adapter = None
    if not is_telegram:
        try:
            adapter = get_channel_adapter(channel)
        except UnsupportedChannelCapability as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        if not adapter.capabilities().send_message:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{channel} does not support message delivery",
            )

    msg: Message | None = None
    created = False
    delivery_key = body.client_message_uuid or uuid4().hex
    existing_result = await session.execute(
        select(Message).where(
            Message.conversation_id == conv.id,
            Message.client_message_uuid == delivery_key,
        )
    )
    msg = existing_result.scalar_one_or_none()
    delivery_runtime = await session.scalar(
        select(DeliveryRuntime).where(
            DeliveryRuntime.workspace_id == workspace.id,
            DeliveryRuntime.client_idempotency_key == delivery_key,
        )
    )

    if msg is not None:
        current_delivery_state = delivery_runtime.state if delivery_runtime else msg.delivery_state
        if (
            msg.external_message_id
            or msg.delivery_state == DELIVERY_CONFIRMED
            or current_delivery_state in {
                DELIVERY_REQUESTED,
                DELIVERY_SENDING,
                DELIVERY_UNKNOWN,
                DELIVERY_CONFIRMED,
                DELIVERY_RECONCILED,
            }
        ):
            return serialize_message_response(msg, conv, delivery_runtime=delivery_runtime)

    delivery_result = None
    if msg is None:
        msg = await create_seller_placeholder_message(
            session,
            conversation=conv,
            content=body.content,
            client_message_uuid=delivery_key,
        )
        created = True

    if not msg.external_message_id and is_telegram:
        delivery_result = await delivery.deliver_message(
            conv.id,
            body.content,
            db=session,
            workspace_id=workspace.id,
            client_idempotency_key=delivery_key,
            message_id=msg.id,
        )
        if not delivery_result.success:
            logger.warning(
                "Delivery result uncertain for send-message conv=%d; keeping placeholder for echo reconciliation: %s",
                conv.id,
                delivery_result.error,
            )
    elif not msg.external_message_id and adapter is not None and conv.external_chat_id:
        try:
            adapter_result = await adapter.send_message(
                workspace_id=workspace.id,
                conversation_id=conv.external_chat_id,
                text=body.content,
                idempotency_key=delivery_key,
            )
            msg.external_message_id = adapter_result.external_message_id
            msg.delivery_state = (
                "confirmed"
                if adapter_result.status.status in {"sent", "delivered"}
                else adapter_result.status.status
            )
            await session.commit()
        except UnsupportedChannelCapability as exc:
            msg.delivery_state = "failed"
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

    if delivery_result and delivery_result.external_message_id:
        msg.external_message_id = delivery_result.external_message_id
        msg.delivery_state = "confirmed"
    elif delivery_result and delivery_result.state:
        msg.delivery_state = delivery_result.state
    elif delivery_result and not delivery_result.success:
        msg.delivery_state = "unknown"

    if created or delivery_result:
        await session.commit()

    if created:
        try:
            await ConversationTurnSessionService(session).complete_active_turns_for_agent_message(
                workspace_id=workspace.id,
                conversation_id=conv.id,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.warning(
                "Post-send agent-message turn completion failed for conversation=%d message=%d; delivery response remains authoritative",
                conv.id,
                msg.id,
                exc_info=True,
            )

    delivery_runtime = await session.scalar(
        select(DeliveryRuntime).where(
            DeliveryRuntime.workspace_id == workspace.id,
            DeliveryRuntime.client_idempotency_key == delivery_key,
        )
    )
    return serialize_message_response(msg, conv, delivery_runtime=delivery_runtime)


async def _mark_remote_read(
    *,
    session: AsyncSession,
    workspace_id: int,
    conversation_id: int,
    read_state: conversation_read_state.ConversationReadState,
) -> None:
    channel = (read_state.conversation.channel or "telegram_dm").strip().lower()
    conversation_ref = (
        read_state.conversation.external_chat_id
        or (
            str(read_state.conversation.telegram_chat_id)
            if read_state.conversation.telegram_chat_id is not None
            else None
        )
    )
    if not conversation_ref:
        return

    message_ref: str | None = None
    if channel in {"telegram_dm", "dm"}:
        if read_state.max_message_id is not None:
            message_ref = str(read_state.max_message_id)
    else:
        external_message_id = await session.scalar(
            select(Message.external_message_id)
            .where(
                Message.conversation_id == conversation_id,
                Message.sender_type == "customer",
                Message.external_message_id.is_not(None),
            )
            .order_by(Message.id.desc())
            .limit(1)
        )
        message_ref = str(external_message_id) if external_message_id else None
    if not message_ref:
        return

    try:
        adapter = get_channel_adapter(channel)
        if adapter.capabilities().mark_read:
            await adapter.mark_read(
                workspace_id=workspace_id,
                conversation_id=conversation_ref,
                message_id=message_ref,
            )
    except UnsupportedChannelCapability:
        logger.info(
            "Channel %s does not support remote mark_read for conversation=%d",
            channel,
            conversation_id,
        )
    except Exception as exc:
        logger.warning(
            "Remote mark_read failed channel=%s conversation=%d err=%s",
            channel,
            conversation_id,
            exc,
        )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: int,
    body: ConversationUpdate,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    """Update a conversation's pipeline stage, attention flag, etc."""
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace.id,
        )
        .options(selectinload(Conversation.customer))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    update_data = body.model_dump(exclude_unset=True)
    if update_data.get("pipeline_stage") is not None:
        state = get_customer_conversation_state(conv)
        apply_manual_field_override(
            state,
            field="pipeline_stage",
            value=update_data["pipeline_stage"],
        )
        set_customer_conversation_state(conv, state)
    for field, value in update_data.items():
        if field == "pipeline_stage":
            continue
        setattr(conv, field, value)

    await session.commit()
    await session.refresh(conv)

    unread_count = await _count_unread_customer_messages(session, conv.id)
    return _build_conversation_response(conv, unread_count=unread_count)


@router.post("/{conversation_id}/mark-read")
async def mark_conversation_read(
    conversation_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
):
    """Mark all customer messages in a conversation as read."""
    read_state = await conversation_read_state.mark_conversation_read(
        session,
        workspace_id=workspace.id,
        conversation_id=conversation_id,
    )
    if not read_state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    await _mark_remote_read(
        session=session,
        workspace_id=workspace.id,
        conversation_id=conversation_id,
        read_state=read_state,
    )

    try:
        from app.api.routes.ws import manager

        await manager.broadcast(workspace.id, {
            "type": "mark_read",
            "data": {
                "conversation_id": conversation_id,
                "unread_count": 0,
            },
        })
    except Exception:
        logger.warning("Failed to push mark_read WS event (non-fatal)")

    return {"ok": True}
