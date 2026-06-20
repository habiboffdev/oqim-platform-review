"""Unified webhook intake — PRD #139.

Receives messages from the GramJS sidecar (and later Business Bot, Instagram)
via X-Sidecar-Key auth. Resolves workspace from telegram_user_id.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.correlation import current_correlation_id
from app.core.deps import get_conversation_turn_runner, get_db_session, get_settings_dep, verify_sidecar_key
from app.core.event_spine import MediaHydrationStateChanged, MsgDeleted, MsgEdited, MsgInbound
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.models.onboarding_runtime import OnboardingRuntime
from app.models.workspace import Workspace
from app.services.channel_conversation_sync import (
    ChannelConversationShell,
    ChannelConversationSync,
)
from app.services.inbound_pipeline import record_pending_edit
from app.services.media_hydration_runtime import hydrate_media_runtime_job
from app.services.media_runtime import MEDIA_ACTION_COMPLETED, ensure_media_runtime_for_message
from app.services.onboarding_runtime import onboarding_runtime_is_active

if TYPE_CHECKING:
    from app.modules.conversation_turns.runner import ConversationTurnRunner

router = APIRouter(prefix="/webhook", tags=["webhook"], dependencies=[Depends(verify_sidecar_key)])
logger = get_logger("api.webhook")

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
ConversationTurnRunnerDep = Annotated["ConversationTurnRunner", Depends(get_conversation_turn_runner)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


class TelegramWebhookPayload(BaseModel):
    sellerUserId: str = ""
    workspaceId: int | None = None
    chatId: str
    senderId: str
    senderName: str = ""
    senderUsername: str | None = None  # noqa: N815 — sidecar wire format is camelCase
    messageId: float
    text: str = ""
    date: int
    isOutgoing: bool = False
    mediaType: str | None = None
    mediaMetadata: dict | None = None
    textEntities: list[dict] | None = None
    replyToMsgId: int | None = None
    forwardFromName: str | None = None
    forwardDate: int | None = None
    groupedId: int | None = None
    mediaBytes: str | None = None
    isHistorical: bool = False
    historical: bool = False
    syncMode: str | None = None
    source: str | None = None
    telegram_update_received_at: float | None = None
    telegram_state_applied_at: float | None = None
    hot_event_built_at: float | None = None
    outbox_enqueued_at: float | None = None


class TelegramMessageEditPayload(BaseModel):
    sellerUserId: str
    chatId: str | None = None
    messageId: int
    text: str
    textEntities: list[dict] | None = None
    editedAt: float | None = None


class TelegramMessageDeletePayload(BaseModel):
    sellerUserId: str
    chatId: str | None = None
    messageIds: list[int]
    deletedAt: float | None = None


class TelegramDialogSyncItem(BaseModel):
    chatId: str
    title: str
    unreadCount: int = 0
    topMessageId: int | None = None
    lastMessageText: str | None = None
    lastMessageDate: int | None = None
    lastMessageIsOutgoing: bool = False


class TelegramDialogSyncPayload(BaseModel):
    sellerUserId: str
    dialogs: list[TelegramDialogSyncItem]


class TelegramMediaHydrationPayload(BaseModel):
    workspace_id: int = Field(alias="workspaceId")
    seller_user_id: str = Field(default="", alias="sellerUserId")
    chat_id: str = Field(alias="chatId")
    message_id: int = Field(alias="messageId")
    media_key: str = Field(alias="mediaKey")
    mime_type: str = Field(alias="mimeType")
    content_base64: str = Field(alias="contentBase64")
    media_kind: str | None = Field(default=None, alias="mediaKind")
    document_id: str | None = Field(default=None, alias="documentId")
    photo_id: str | None = Field(default=None, alias="photoId")
    size: int | None = None
    downloaded_at: float | None = Field(default=None, alias="downloadedAt")
    source: str | None = None


async def _resolve_workspace(
    session: AsyncSession,
    seller_user_id: str,
    *,
    workspace_id: int | None = None,
) -> Workspace:
    if seller_user_id:
        try:
            telegram_user_id = int(seller_user_id)
        except (TypeError, ValueError):
            telegram_user_id = None
        if telegram_user_id is not None:
            result = await session.execute(select(Workspace).where(Workspace.telegram_user_id == telegram_user_id))
            workspace = result.scalar_one_or_none()
            if workspace:
                return workspace
    if workspace_id:
        result = await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        workspace = result.scalar_one_or_none()
        if workspace:
            return workspace
    lookup = f"Telegram user {seller_user_id}" if seller_user_id else f"workspace {workspace_id}"
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No workspace for this {lookup}",
    )


async def _observe_event_spine_workspace(request: Request, workspace_id: int) -> None:
    consumer = getattr(request.app.state, "event_spine_persist_consumer", None)
    observe_workspace = getattr(consumer, "observe_workspace", None)
    if observe_workspace is not None:
        await observe_workspace(workspace_id)


def _decode_media_content(content_b64: str) -> bytes:
    try:
        content = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_media_content",
        ) from exc
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty_media_content",
        )
    return content


def _media_hydration_event_type(runtime: MediaRuntime) -> str:
    if runtime.action_state == "completed":
        return "media.hydration_completed"
    if runtime.action_state == "failed":
        return "media.hydration_failed"
    if runtime.action_state == "deferred":
        return "media.hydration_deferred"
    return "media.hydration_state_changed"


def _media_hydration_event(
    *,
    workspace: Workspace,
    conversation: Conversation,
    message: Message,
    runtime: MediaRuntime,
    event_type: str,
    media_key: str,
    changed_at: float,
    seller_user_id: str | None,
) -> MediaHydrationStateChanged:
    telegram_chat_id = int(conversation.telegram_chat_id or conversation.external_chat_id or 0)
    telegram_message_id = int(message.telegram_message_id or message.external_message_id or 0)
    media_evidence = (
        runtime.commercial_semantics
        if isinstance(runtime.commercial_semantics, dict)
        else None
    )
    return MediaHydrationStateChanged(
        type=event_type,
        workspace_id=int(workspace.id),
        channel=runtime.channel or message.channel or conversation.channel or "telegram_dm",
        channel_account_id=str(seller_user_id or ""),
        channel_conversation_id=str(telegram_chat_id),
        channel_message_id=str(telegram_message_id),
        correlation_id=current_correlation_id(),
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        hydration_status=runtime.hydration_status,
        asset_state=runtime.asset_state,
        semantic_state=runtime.semantic_state,
        action_state=runtime.action_state,
        ai_relevant=bool(runtime.ai_relevant),
        mime_type=runtime.mime_type,
        normalized_text=runtime.normalized_text,
        media_evidence=media_evidence,
        commercial_semantics=media_evidence,
        last_error=runtime.last_error,
        changed_at=changed_at,
        occurred_at=changed_at,
        emitted_at=datetime.now(UTC).timestamp(),
        idempotency_key=(
            f"media:sink:{workspace.id}:{telegram_chat_id}:{telegram_message_id}:"
            f"{media_key}:{runtime.hydration_status}"
        ),
    )


@router.post("/telegram")
async def webhook_telegram(
    body: TelegramWebhookPayload,
    session: SessionDep,
    conversation_turn_runner: ConversationTurnRunnerDep,
    settings: SettingsDep,
    request: Request,
):
    """Receive a message from the GramJS sidecar webhook."""
    workspace = await _resolve_workspace(session, body.sellerUserId, workspace_id=body.workspaceId)
    await _observe_event_spine_workspace(request, workspace.id)
    payload = body.model_dump()
    payload["backend_webhook_received_at"] = datetime.now(UTC).timestamp()

    # Runtime truth boundary: append before projection work.
    stream_id = await request.app.state.event_spine.append(
        MsgInbound.from_webhook(
            payload,
            workspace_id=workspace.id,
            correlation_id=current_correlation_id(),
        )
    )
    if settings.is_event_spine_authoritative():
        return {
            "status": "accepted",
            "source_of_truth": "event_spine",
            "stream_id": stream_id,
        }

    sync = ChannelConversationSync()
    return await sync.ingest_event(
        raw_payload=payload,
        workspace=workspace,
        session=session,
        conversation_turn_runner=conversation_turn_runner,
        channel="telegram_dm",
    )


class TelegramTypingPayload(BaseModel):
    sellerUserId: str = ""  # noqa: N815 — sidecar wire format is camelCase
    workspaceId: int | None = None  # noqa: N815
    chatId: str  # noqa: N815


@router.post("/telegram/typing")
async def webhook_telegram_typing(
    body: TelegramTypingPayload,
    session: SessionDep,
):
    """Transient "yozmoqda…" signal: holds the turn lease while the customer
    types so bursts coalesce. Dispatch-timing input only — never business
    truth, never an action trigger, best-effort by design."""
    from sqlalchemy import select as sa_select

    from app.models.conversation import Conversation
    from app.modules.conversation_turns.service import ConversationTurnSessionService

    workspace = await _resolve_workspace(session, body.sellerUserId, workspace_id=body.workspaceId)
    conversation_id = (
        await session.execute(
            sa_select(Conversation.id).where(
                Conversation.workspace_id == workspace.id,
                Conversation.telegram_chat_id == int(body.chatId),
            )
        )
    ).scalar_one_or_none()
    if conversation_id is None:
        return {"status": "ignored", "reason": "no_conversation"}
    touched = await ConversationTurnSessionService(session).mark_customer_typing(
        workspace_id=workspace.id,
        conversation_id=conversation_id,
    )
    await session.commit()
    return {"status": "ok", "touched": touched}


@router.post("/telegram/dialog-sync")
async def webhook_telegram_dialog_sync(
    body: TelegramDialogSyncPayload,
    session: SessionDep,
):
    workspace = await _resolve_workspace(session, body.sellerUserId)
    workspace_id = int(workspace.id)
    onboarding_completed = bool(workspace.onboarding_completed)
    sync = ChannelConversationSync()
    synced_count = await sync.apply_conversation_shells(
        session=session,
        workspace_id=workspace_id,
        channel="telegram_dm",
        shells=[
            ChannelConversationShell(
                external_chat_id=dialog.chatId,
                title=dialog.title,
                unread_count=dialog.unreadCount,
                top_message_id=dialog.topMessageId,
                last_message_text=dialog.lastMessageText,
                last_message_date=(
                    datetime.fromtimestamp(dialog.lastMessageDate, tz=UTC) if dialog.lastMessageDate else None
                ),
                last_message_is_outgoing=dialog.lastMessageIsOutgoing,
            )
            for dialog in body.dialogs
        ],
    )
    runtime = await session.scalar(
        select(OnboardingRuntime).where(OnboardingRuntime.workspace_id == workspace_id)
    )
    should_defer_hydration = (
        not onboarding_completed
        or onboarding_runtime_is_active(runtime)
    )
    hydration_deferred_reason = (
        "onboarding_owns_history_import"
        if should_defer_hydration
        else None
    )
    hydration_queue = (
        await sync.queue_stale_dialog_hydrations(
            session=session,
            workspace_id=workspace_id,
            channel="telegram_dm",
            max_conversations=3,
            request_limit=50,
        )
        if not should_defer_hydration
        else None
    )
    queued_hydration_count = (
        hydration_queue.queued_conversations if hydration_queue is not None else 0
    )
    if synced_count or queued_hydration_count:
        try:
            from app.api.routes.ws import manager as ws_manager

            await ws_manager.broadcast(
                workspace_id,
                {
                    "type": "conversation_updated",
                    "data": {
                        "source": "telegram_dialog_sync",
                        "synced_count": synced_count,
                        "queued_hydration_count": queued_hydration_count,
                        "hydration_deferred_reason": hydration_deferred_reason,
                    },
                },
            )
        except Exception:
            logger.warning(
                "dialog_sync.websocket_broadcast_failed workspace=%d",
                workspace_id,
                exc_info=True,
            )
    return {
        "synced_count": synced_count,
        "queued_hydration_count": queued_hydration_count,
        "hydration_deferred_reason": hydration_deferred_reason,
    }


@router.post("/telegram/media-hydration")
async def webhook_telegram_media_hydration(
    body: TelegramMediaHydrationPayload,
    session: SessionDep,
    conversation_turn_runner: ConversationTurnRunnerDep,
    request: Request,
):
    """Receive a Telegram media blob downloaded by the sidecar media lane."""
    workspace = await _resolve_workspace(
        session,
        body.seller_user_id,
        workspace_id=body.workspace_id,
    )
    await _observe_event_spine_workspace(request, workspace.id)

    try:
        chat_id = int(body.chat_id)
        message_id = int(body.message_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_media_identity",
        ) from exc

    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.workspace_id == workspace.id,
            Conversation.telegram_chat_id == chat_id,
        )
    )
    if conversation is None:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"status": "retry", "detail": "no_conversation"},
        )

    message = await session.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id,
            Message.telegram_message_id == message_id,
        )
    )
    if message is None:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"status": "retry", "detail": "no_message"},
        )

    runtime = await ensure_media_runtime_for_message(
        session,
        workspace_id=workspace.id,
        conversation=conversation,
        message=message,
    )
    if runtime is None:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"status": "retry", "detail": "media_not_applicable"},
        )
    if (
        runtime.action_state == MEDIA_ACTION_COMPLETED
        and runtime.hydration_status == "hydrated"
    ):
        return {
            "status": "hydrated",
            "runtime_id": runtime.id,
            "message_id": message.id,
            "conversation_id": conversation.id,
            "event_type": "media.hydration_completed",
            "idempotent": True,
        }

    content = _decode_media_content(body.content_base64)

    async def _fetch_media() -> tuple[bytes, str]:
        return content, body.mime_type or "application/octet-stream"

    result = await hydrate_media_runtime_job(
        session,
        workspace_id=workspace.id,
        runtime=runtime,
        fetch_media=_fetch_media,
    )
    refreshed_runtime = await session.get(MediaRuntime, runtime.id)
    if refreshed_runtime is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="media_runtime_missing_after_hydration",
        )

    event_type = _media_hydration_event_type(refreshed_runtime)
    changed_at = datetime.now(UTC).timestamp()
    event = _media_hydration_event(
        workspace=workspace,
        conversation=conversation,
        message=message,
        runtime=refreshed_runtime,
        event_type=event_type,
        media_key=body.media_key,
        changed_at=changed_at,
        seller_user_id=body.seller_user_id,
    )
    event_spine = getattr(request.app.state, "event_spine", None)
    stream_id = await event_spine.append(event) if event_spine is not None else None

    if (
        result.should_wake_agent_turn
        and result.conversation_id is not None
        and hasattr(conversation_turn_runner, "enqueue_conversation")
    ):
        await conversation_turn_runner.enqueue_conversation(
            workspace_id=workspace.id,
            conversation_id=int(result.conversation_id),
        )

    return {
        "status": result.status,
        "runtime_id": refreshed_runtime.id,
        "message_id": result.message_id,
        "conversation_id": result.conversation_id,
        "event_type": event_type,
        "stream_id": stream_id,
    }


@router.post("/telegram/message-edit")
async def webhook_telegram_message_edit(
    body: TelegramMessageEditPayload,
    session: SessionDep,
    conversation_turn_runner: ConversationTurnRunnerDep,
    settings: SettingsDep,
    request: Request,
):
    from app.services.inbound_pipeline import process_message_edit

    workspace = await _resolve_workspace(session, body.sellerUserId)

    # Runtime truth boundary: append before projection work when chat identity exists.
    if body.chatId:
        await _observe_event_spine_workspace(request, workspace.id)
        stream_id = await request.app.state.event_spine.append(
            MsgEdited.from_webhook(
                body.model_dump(),
                workspace_id=workspace.id,
                edited_at=body.editedAt,
                correlation_id=current_correlation_id(),
            )
        )
        if settings.is_event_spine_authoritative():
            return {
                "status": "accepted",
                "source_of_truth": "event_spine",
                "stream_id": stream_id,
                "reply_generation_retriggered": False,
            }

    conv_stmt = select(Conversation).where(Conversation.workspace_id == workspace.id)
    if body.chatId:
        conv_stmt = conv_stmt.where(Conversation.telegram_chat_id == int(body.chatId))
    conv_result = await session.execute(conv_stmt)
    conversations = conv_result.scalars().all()
    if not conversations:
        return {
            "status": "accepted",
            "detail": "no_conversation",
            "reply_generation_retriggered": False,
        }

    conversation_ids = [conversation.id for conversation in conversations]
    msg_result = await session.execute(
        select(Message).where(
            Message.conversation_id.in_(conversation_ids),
            Message.telegram_message_id == body.messageId,
        )
    )
    message = msg_result.scalar_one_or_none()
    if not message:
        if body.chatId and len(conversations) == 1:
            conversation = conversations[0]
            record_pending_edit(
                conversation=conversation,
                telegram_message_id=body.messageId,
                text=body.text,
            )
            session.add(conversation)
            await session.commit()
            return {
                "status": "accepted",
                "detail": "pending_edit",
                "reply_generation_retriggered": False,
            }
        return {
            "status": "accepted",
            "detail": "no_message",
            "reply_generation_retriggered": False,
        }

    conversation = next(
        (candidate for candidate in conversations if candidate.id == message.conversation_id),
        None,
    )
    if not conversation:
        return {
            "status": "accepted",
            "detail": "no_conversation",
            "reply_generation_retriggered": False,
        }

    result = await process_message_edit(
        session=session,
        workspace=workspace,
        conversation=conversation,
        message=message,
        conversation_turn_runner=conversation_turn_runner,
        edited_text=body.text,
        text_entities=body.textEntities,
    )

    return {
        "status": "updated",
        "message_id": result.message_id,
        "reply_generation_retriggered": result.reply_generation_triggered,
    }


@router.post("/telegram/message-delete")
async def webhook_telegram_message_delete(
    body: TelegramMessageDeletePayload,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
):
    from app.services.inbound_pipeline import process_message_delete

    workspace = await _resolve_workspace(session, body.sellerUserId)

    if not body.chatId:
        return {
            "deleted_count": 0,
            "detail": "chat_id_required_for_safe_delete",
        }

    # Runtime truth boundary: append before projection work.
    await _observe_event_spine_workspace(request, workspace.id)
    stream_id = await request.app.state.event_spine.append(
        MsgDeleted.from_webhook(
            body.model_dump(),
            workspace_id=workspace.id,
            deleted_at=body.deletedAt,
            correlation_id=current_correlation_id(),
        )
    )
    if settings.is_event_spine_authoritative():
        return {
            "deleted_count": 0,
            "status": "accepted",
            "source_of_truth": "event_spine",
            "stream_id": stream_id,
        }

    stmt = (
        select(Message, Conversation)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Message.telegram_message_id.in_(body.messageIds),
            Conversation.workspace_id == workspace.id,
            Conversation.telegram_chat_id == int(body.chatId),
        )
    )

    result = await session.execute(stmt)
    rows = result.all()
    for message, conversation in rows:
        await process_message_delete(
            session=session,
            workspace=workspace,
            conversation=conversation,
            message=message,
        )
    return {"deleted_count": len(rows)}
