from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce_catalog import CatalogMediaRecord
from app.models.commercial_spine import BusinessBrainFactRecord
from app.models.conversation import Conversation
from app.models.media_vault import MediaVaultRecord
from app.models.message import Message, SenderType
from app.modules.commercial_spine.contracts import CommercialEvent
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.conversation_core.service import create_seller_placeholder_message
from app.modules.telegram_tools.contracts import (
    TELEGRAM_EDIT_MESSAGE,
    TELEGRAM_FETCH_MEDIA,
    TELEGRAM_READ_MESSAGES,
    TELEGRAM_SEND_MESSAGE,
    TELEGRAM_SEND_REACTION,
    TELEGRAM_SYNC_HISTORY,
    TELEGRAM_WATCH_CHANNEL,
    TelegramToolMessage,
    TelegramToolResult,
)
from app.modules.tool_grants.service import ToolGrantService
from app.modules.triggers.contracts import TriggerInput
from app.modules.triggers.service import TriggerService
from app.services.channel_adapter_contract import (
    ChannelAdapter,
    ChannelMediaRef,
    ChannelOutboundMedia,
    UnsupportedChannelCapability,
    get_channel_adapter,
)
from app.services.channel_sync_runtime import ChannelSyncRateLimitError
from app.services.delivery_runtime import DELIVERY_CONFIRMED, DELIVERY_RECONCILED

if TYPE_CHECKING:
    from app.services.channel_conversation_sync import ChannelConversationSync

logger = logging.getLogger(__name__)

CONFIRMED_MESSAGE_DELIVERY_STATES = {DELIVERY_CONFIRMED, DELIVERY_RECONCILED}
UNKNOWN_MESSAGE_DELIVERY_STATES = {"unknown"}


class TelegramToolDelivery(Protocol):
    async def deliver_message(
        self,
        conversation_id: int,
        text: str,
        *,
        db: AsyncSession,
        workspace_id: int | None = None,
        action_record_id: int | None = None,
        client_idempotency_key: str | None = None,
        message_id: int | None = None,
        reply_to_message_id: int | None = None,
        delay_override_seconds: float | None = None,
        typing_indicator: bool = True,
        online_tail_seconds: float = 0.0,
    ) -> Any: ...

    async def deliver_media(
        self,
        conversation_id: int,
        media: ChannelOutboundMedia,
        *,
        caption: str | None = None,
        db: AsyncSession,
        workspace_id: int | None = None,
        action_record_id: int | None = None,
        client_idempotency_key: str | None = None,
        message_id: int | None = None,
        reply_to_message_id: int | None = None,
        delay_override_seconds: float | None = None,
        typing_indicator: bool = True,
        online_tail_seconds: float = 0.0,
    ) -> Any: ...


class TelegramToolRuntime:
    """Agent-facing Telegram tool boundary.

    This is intentionally above the sidecar and below agents. Agents request a
    scoped operation, this runtime checks ToolGrant, performs the operation
    through existing delivery/sync adapters, records usage, and writes an audit
    event into Commercial Spine.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        delivery: TelegramToolDelivery | None = None,
        adapter: ChannelAdapter | None = None,
        sync: ChannelConversationSync | None = None,
        repository: CommercialSpineRepository | None = None,
    ) -> None:
        self._session = session
        self._delivery = delivery
        self._adapter = adapter or get_channel_adapter("telegram_dm")
        self._sync = sync
        self._repository = repository or CommercialSpineRepository(session)
        self._grants = ToolGrantService(session)

    async def read_messages(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        conversation_id: int,
        correlation_id: str,
        limit: int = 50,
        before_message_id: str | None = None,
        after_message_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TelegramToolResult:
        key = _idempotency(
            idempotency_key,
            scope=TELEGRAM_READ_MESSAGES,
            workspace_id=workspace_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            target=str(conversation_id),
        )
        allowed = await self._authorize(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_READ_MESSAGES,
            correlation_id=correlation_id,
            idempotency_key=key,
        )
        if not allowed:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_READ_MESSAGES,
                status="blocked",
                reason_code="missing_tool_grant",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )

        conversation = await self._conversation(workspace_id, conversation_id)
        block_reason = _telegram_conversation_block_reason(conversation)
        if block_reason:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_READ_MESSAGES,
                status="blocked",
                reason_code=block_reason,
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )
            await self._audit(result)
            return result

        if not self._adapter.capabilities().fetch_history:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_READ_MESSAGES,
                status="unsupported",
                reason_code="telegram_history_unsupported",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )
            await self._audit(result)
            return result

        try:
            remote_messages = await self._adapter.fetch_history(
                workspace_id=workspace_id,
                conversation_id=_conversation_ref(conversation),
                before_message_id=before_message_id,
                after_message_id=after_message_id,
                limit=max(1, min(int(limit), 200)),
            )
        except UnsupportedChannelCapability:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_READ_MESSAGES,
                status="unsupported",
                reason_code="telegram_history_unsupported",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )
            await self._audit(result)
            return result

        await self._grants.record_use(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_READ_MESSAGES,
        )
        result = self._result(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_READ_MESSAGES,
            status="executed",
            reason_code="messages_read",
            correlation_id=correlation_id,
            idempotency_key=key,
            conversation_id=conversation_id,
            messages=[
                TelegramToolMessage(
                    conversation_id=item.conversation_id,
                    message_id=item.message_id,
                    sender_id=item.sender_id,
                    sender_name=item.sender_name,
                    text=item.text,
                    sent_at=item.sent_at,
                    is_outgoing=item.is_outgoing,
                    media_type=item.media_type,
                    media_metadata=item.media_metadata,
                )
                for item in remote_messages
            ],
            payload={"count": len(remote_messages)},
        )
        await self._audit(result)
        return result

    async def send_message(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        conversation_id: int,
        text: str,
        correlation_id: str,
        action_record_id: int | None = None,
        idempotency_key: str | None = None,
        reply_to_message_ref: str | None = None,
        delivery_delay_seconds: float | None = None,
        typing_indicator: bool = True,
        online_tail_seconds: float = 0.0,
    ) -> TelegramToolResult:
        cleaned_text = str(text or "").strip()
        key = _idempotency(
            idempotency_key,
            scope=TELEGRAM_SEND_MESSAGE,
            workspace_id=workspace_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            target=str(conversation_id),
        )
        if not cleaned_text:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="blocked",
                reason_code="empty_message",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )

        allowed = await self._authorize(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SEND_MESSAGE,
            correlation_id=correlation_id,
            idempotency_key=key,
        )
        if not allowed:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="blocked",
                reason_code="missing_tool_grant",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )

        conversation = await self._conversation(workspace_id, conversation_id)
        block_reason = _send_message_conversation_block_reason(conversation)
        if block_reason:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="blocked",
                reason_code=block_reason,
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )
            await self._audit(result)
            return result

        existing = await self._session.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.client_message_uuid == key,
            )
        )
        if existing is not None and _message_delivery_is_confirmed(existing):
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="replayed",
                reason_code="idempotent_replay",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
                message_id=existing.id,
                external_message_id=existing.external_message_id,
                delivery_state=existing.delivery_state,
            )
            await self._audit(result)
            return result

        if existing is not None and _message_delivery_is_unknown(existing):
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="failed",
                reason_code="delivery_unknown",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
                message_id=existing.id,
                external_message_id=existing.external_message_id,
                delivery_state=existing.delivery_state,
            )
            await self._audit(result)
            return result

        if self._delivery is None:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="failed",
                reason_code="delivery_runtime_unavailable",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )
            await self._audit(result)
            return result

        reply_to_message_id = await self._resolve_reply_to_message_id(
            conversation_id=conversation_id,
            reply_to_message_ref=reply_to_message_ref,
        )
        message = existing
        if message is None:
            message = await create_seller_placeholder_message(
                self._session,
                conversation=conversation,
                content=cleaned_text,
                client_message_uuid=key,
                reply_to_msg_id=reply_to_message_id,
            )
        else:
            message.delivery_state = "pending"
            message.external_message_id = None
            message.content = cleaned_text
            message.reply_to_msg_id = reply_to_message_id
            self._session.add(message)
            await self._session.flush()
        delivery_result = await self._delivery.deliver_message(
            conversation_id,
            cleaned_text,
            db=self._session,
            workspace_id=workspace_id,
            action_record_id=action_record_id,
            client_idempotency_key=key,
            message_id=message.id,
            reply_to_message_id=reply_to_message_id,
            delay_override_seconds=delivery_delay_seconds,
            typing_indicator=typing_indicator,
            online_tail_seconds=online_tail_seconds,
        )
        if getattr(delivery_result, "external_message_id", None):
            message.external_message_id = str(delivery_result.external_message_id)
        if getattr(delivery_result, "state", None):
            message.delivery_state = str(delivery_result.state)
        elif getattr(delivery_result, "success", False):
            message.delivery_state = "confirmed"
        else:
            message.delivery_state = "failed"
        await self._session.commit()

        await self._grants.record_use(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SEND_MESSAGE,
        )
        result = self._result(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SEND_MESSAGE,
            status="executed" if getattr(delivery_result, "success", False) else "failed",
            reason_code=(
                "delivery_confirmed"
                if getattr(delivery_result, "success", False)
                else "delivery_not_confirmed"
            ),
            correlation_id=correlation_id,
            idempotency_key=key,
            conversation_id=conversation_id,
            message_id=message.id,
            external_message_id=message.external_message_id,
            delivery_state=message.delivery_state,
            payload={
                "text_length": len(cleaned_text),
                **(
                    {"error": str(delivery_result.error)}
                    if getattr(delivery_result, "error", None)
                    else {}
                ),
            },
        )
        await self._audit(result)
        return result

    async def send_media(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        conversation_id: int,
        media_ref: str,
        caption: str | None = None,
        correlation_id: str,
        action_record_id: int | None = None,
        idempotency_key: str | None = None,
        reply_to_message_ref: str | None = None,
        delivery_delay_seconds: float | None = None,
        typing_indicator: bool = True,
        online_tail_seconds: float = 0.0,
    ) -> TelegramToolResult:
        cleaned_ref = str(media_ref or "").strip()
        cleaned_caption = str(caption or "").strip()
        key = _idempotency(
            idempotency_key,
            scope=TELEGRAM_SEND_MESSAGE,
            workspace_id=workspace_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            target=f"{conversation_id}:{cleaned_ref}",
        )
        if not cleaned_ref:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="blocked",
                reason_code="empty_media_ref",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )

        allowed = await self._authorize(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SEND_MESSAGE,
            correlation_id=correlation_id,
            idempotency_key=key,
        )
        if not allowed:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="blocked",
                reason_code="missing_tool_grant",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )

        conversation = await self._conversation(workspace_id, conversation_id)
        block_reason = _send_message_conversation_block_reason(conversation)
        if block_reason:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="blocked",
                reason_code=block_reason,
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )
            await self._audit(result)
            return result

        existing = await self._session.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.client_message_uuid == key,
            )
        )
        if existing is not None and _message_delivery_is_confirmed(existing):
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="replayed",
                reason_code="idempotent_replay",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
                message_id=existing.id,
                external_message_id=existing.external_message_id,
                delivery_state=existing.delivery_state,
                payload={"media_ref": cleaned_ref},
            )
            await self._audit(result)
            return result

        if existing is not None and _message_delivery_is_unknown(existing):
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="failed",
                reason_code="delivery_unknown",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
                message_id=existing.id,
                external_message_id=existing.external_message_id,
                delivery_state=existing.delivery_state,
                payload={"media_ref": cleaned_ref},
            )
            await self._audit(result)
            return result

        if self._delivery is None:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="failed",
                reason_code="delivery_runtime_unavailable",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )
            await self._audit(result)
            return result

        media = await self._resolve_outbound_media(workspace_id=workspace_id, media_ref=cleaned_ref)
        if media is None:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_MESSAGE,
                status="blocked",
                reason_code="media_not_sendable",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
                payload={"media_ref": cleaned_ref},
            )
            await self._audit(result)
            return result

        if not cleaned_caption:
            _vault_caption = str(getattr(media, "caption", None) or "").strip()
            if _vault_caption:
                cleaned_caption = _vault_caption

        reply_to_message_id = await self._resolve_reply_to_message_id(
            conversation_id=conversation_id,
            reply_to_message_ref=reply_to_message_ref,
        )
        content = cleaned_caption or f"[media] {cleaned_ref}"
        message = existing
        media_metadata = {
            "media_ref": cleaned_ref,
            "url": media.url,
            "asset_id": media.asset_id,
            "mime_type": media.mime_type,
            "file_name": media.file_name,
        }
        if message is None:
            message = await create_seller_placeholder_message(
                self._session,
                conversation=conversation,
                content=content,
                client_message_uuid=key,
                media_type=media.media_type,
                media_metadata=media_metadata,
                reply_to_msg_id=reply_to_message_id,
            )
        else:
            message.delivery_state = "pending"
            message.external_message_id = None
            message.content = content
            message.media_type = media.media_type
            message.media_url = media.url
            message.media_metadata = media_metadata
            message.reply_to_msg_id = reply_to_message_id
            self._session.add(message)
            await self._session.flush()
        message.media_url = media.url
        self._session.add(message)
        await self._session.flush()

        delivery_result = await self._delivery.deliver_media(
            conversation_id,
            media,
            caption=cleaned_caption or None,
            db=self._session,
            workspace_id=workspace_id,
            action_record_id=action_record_id,
            client_idempotency_key=key,
            message_id=message.id,
            reply_to_message_id=reply_to_message_id,
            delay_override_seconds=delivery_delay_seconds,
            typing_indicator=typing_indicator,
            online_tail_seconds=online_tail_seconds,
        )
        if getattr(delivery_result, "external_message_id", None):
            message.external_message_id = str(delivery_result.external_message_id)
        if getattr(delivery_result, "state", None):
            message.delivery_state = str(delivery_result.state)
        elif getattr(delivery_result, "success", False):
            message.delivery_state = "confirmed"
        else:
            message.delivery_state = "failed"
        await self._session.commit()

        await self._grants.record_use(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SEND_MESSAGE,
        )
        result = self._result(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SEND_MESSAGE,
            status="executed" if getattr(delivery_result, "success", False) else "failed",
            reason_code=(
                "delivery_confirmed"
                if getattr(delivery_result, "success", False)
                else "delivery_not_confirmed"
            ),
            correlation_id=correlation_id,
            idempotency_key=key,
            conversation_id=conversation_id,
            message_id=message.id,
            external_message_id=message.external_message_id,
            delivery_state=message.delivery_state,
            payload={
                "media_ref": cleaned_ref,
                "media_url": media.url,
                "media_type": media.media_type,
                **(
                    {"error": str(delivery_result.error)}
                    if getattr(delivery_result, "error", None)
                    else {}
                ),
            },
        )
        await self._audit(result)
        return result

    async def send_reaction(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        conversation_id: int,
        reaction: str,
        correlation_id: str,
        target_message_ref: str | None = None,
        action_record_id: int | None = None,
        idempotency_key: str | None = None,
    ) -> TelegramToolResult:
        _ = action_record_id
        cleaned_reaction = str(reaction or "").strip()
        key = _idempotency(
            idempotency_key,
            scope=TELEGRAM_SEND_REACTION,
            workspace_id=workspace_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            target=f"{conversation_id}:{target_message_ref or ''}:{cleaned_reaction}",
        )
        if not cleaned_reaction:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_REACTION,
                status="blocked",
                reason_code="empty_reaction",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )

        allowed = await self._authorize(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SEND_REACTION,
            correlation_id=correlation_id,
            idempotency_key=key,
        )
        if not allowed:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_REACTION,
                status="blocked",
                reason_code="missing_tool_grant",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )

        conversation = await self._conversation(workspace_id, conversation_id)
        block_reason = _telegram_conversation_block_reason(conversation)
        if block_reason:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_REACTION,
                status="blocked",
                reason_code=block_reason,
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )
            await self._audit(result)
            return result

        remote_message_id = await self._resolve_reply_to_message_id(
            conversation_id=conversation_id,
            reply_to_message_ref=target_message_ref,
        )
        if remote_message_id is None:
            logger.warning(
                "reaction target unresolvable: workspace=%s conversation=%s ref=%r",
                workspace_id,
                conversation_id,
                target_message_ref,
            )
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_REACTION,
                status="blocked",
                reason_code="target_message_not_found",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
                payload={"target_message_ref": target_message_ref},
            )
            await self._audit(result)
            return result

        if not getattr(self._adapter.capabilities(), "send_reaction", False):
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_REACTION,
                status="unsupported",
                reason_code="telegram_reaction_unsupported",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
                external_message_id=str(remote_message_id),
            )
            await self._audit(result)
            return result

        try:
            reaction_result = await self._adapter.send_reaction(
                workspace_id=workspace_id,
                conversation_id=_conversation_ref(conversation),
                message_id=str(remote_message_id),
                reaction=cleaned_reaction,
                idempotency_key=key,
            )
        except UnsupportedChannelCapability:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_REACTION,
                status="unsupported",
                reason_code="telegram_reaction_unsupported",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
                external_message_id=str(remote_message_id),
            )
            await self._audit(result)
            return result
        except (httpx.HTTPError, ChannelSyncRateLimitError) as exc:
            # A reaction is cosmetic. A transient sidecar transport error (e.g. a
            # 502 on /react) must NEVER raise out of here — that would abort turn
            # finalization after the text bubble already delivered and leave the
            # HermesRun stuck running (#418). Surface it as a structured failed
            # result, just like the delivery layer does for send_message.
            logger.warning(
                "reaction delivery failed (transient): workspace=%s conversation=%s error=%s",
                workspace_id,
                conversation_id,
                exc,
            )
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SEND_REACTION,
                status="failed",
                reason_code="reaction_delivery_failed",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
                external_message_id=str(remote_message_id),
                delivery_state="failed",
                payload={
                    "reaction": cleaned_reaction,
                    "target_message_ref": target_message_ref,
                    "error": str(exc),
                },
            )
            await self._audit(result)
            return result

        await self._grants.record_use(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SEND_REACTION,
        )
        result = self._result(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SEND_REACTION,
            status="executed",
            reason_code="reaction_sent",
            correlation_id=correlation_id,
            idempotency_key=key,
            conversation_id=conversation_id,
            external_message_id=reaction_result.external_message_id,
            delivery_state="confirmed",
            payload={
                "reaction": cleaned_reaction,
                "target_message_ref": target_message_ref,
                "target_message_id": remote_message_id,
            },
        )
        await self._audit(result)
        return result

    async def _resolve_reply_to_message_id(
        self,
        *,
        conversation_id: int,
        reply_to_message_ref: str | None,
    ) -> int | None:
        ref = str(reply_to_message_ref or "").strip()
        if not ref:
            return None
        local_id: int | None = None
        from_local_ref = False
        if ref.startswith("message:"):
            try:
                local_id = int(ref.split(":", 1)[1])
            except ValueError:
                return None
        if local_id is not None:
            message = await self._session.get(Message, local_id)
            if message is not None and message.conversation_id == conversation_id:
                return _remote_message_int(message)
            # Dangling local ref: retry the tail as a channel-level id below
            # (external/telegram lookup) instead of silently dropping the
            # target. Never fall back to the RAW tail as a remote id — a local
            # PK is not a Telegram message id, that would react to the wrong
            # message.
            from_local_ref = True
            ref = ref.split(":", 1)[1]

        numeric_ref: int | None = None
        try:
            numeric_ref = int(ref)
        except ValueError:
            numeric_ref = None
        # Single or_() query: union_all() of entity selects drops ORM mapping
        # (scalar returns a raw id int), which broke _remote_message_int.
        conditions = [Message.external_message_id == ref]
        if numeric_ref is not None:
            conditions.append(Message.telegram_message_id == numeric_ref)
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id, or_(*conditions))
            .limit(1)
        )
        result = await self._session.execute(query)
        message = result.scalar_one_or_none()
        if message is not None:
            return _remote_message_int(message)
        return None if from_local_ref else numeric_ref

    async def _resolve_outbound_media(
        self,
        *,
        workspace_id: int,
        media_ref: str,
    ) -> ChannelOutboundMedia | None:
        ref = str(media_ref or "").strip()
        if not ref:
            return None

        catalog_media = await self._session.scalar(
            select(CatalogMediaRecord).where(
                CatalogMediaRecord.workspace_id == workspace_id,
                CatalogMediaRecord.media_ref == ref,
                CatalogMediaRecord.authority_state == "approved",
            )
        )
        if catalog_media is not None:
            media = _outbound_media_from_mapping(
                media_ref=ref,
                value={
                    "url": catalog_media.url,
                    "media_type": catalog_media.media_kind,
                    "content_type": catalog_media.metadata_.get("content_type")
                    if isinstance(catalog_media.metadata_, dict)
                    else None,
                    "file_name": catalog_media.metadata_.get("file_name")
                    if isinstance(catalog_media.metadata_, dict)
                    else None,
                },
            )
            if media is not None:
                return media

        source_fact = await self._session.scalar(
            select(BusinessBrainFactRecord).where(
                BusinessBrainFactRecord.workspace_id == workspace_id,
                BusinessBrainFactRecord.status == "active",
                or_(
                    BusinessBrainFactRecord.fact_id == ref,
                    BusinessBrainFactRecord.fact_id == f"business_source_media:{ref}",
                    BusinessBrainFactRecord.entity_ref == f"workspace:source_media:{ref}",
                ),
            )
        )
        if source_fact is not None:
            return _outbound_media_from_mapping(media_ref=ref, value=source_fact.value)

        # Third source: the owner's media vault (spike #439). A reusable asset
        # curated once and addressed by handle; sent by the seller via send_media.
        vault = await self._session.scalar(
            select(MediaVaultRecord).where(
                MediaVaultRecord.workspace_id == workspace_id,
                MediaVaultRecord.handle == ref,
            )
        )
        if vault is not None:
            if vault.vault_message_id is not None and vault.vault_peer:
                # Document-pointer assets read their caption LIVE from the channel
                # post (the sidecar fills it from the getMessages it already does),
                # so we deliberately do NOT carry the stored snapshot caption here.
                # The channel post stays the single source of truth.
                return ChannelOutboundMedia(
                    url=f"vault://{vault.vault_peer}/{vault.vault_message_id}",
                    media_type=vault.media_type,
                    mime_type=vault.mime_type,
                    file_name=vault.file_name,
                    asset_id=ref,
                    vault_peer=vault.vault_peer,
                    vault_message_id=int(vault.vault_message_id),
                )
            if vault.cdn_url:
                return ChannelOutboundMedia(
                    url=vault.cdn_url,
                    media_type=vault.media_type,
                    mime_type=vault.mime_type,
                    file_name=vault.file_name,
                    asset_id=ref,
                    caption=vault.caption,
                )
        return None

    async def edit_message(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        local_message_id: int,
        text: str,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> TelegramToolResult:
        cleaned_text = str(text or "").strip()
        key = _idempotency(
            idempotency_key,
            scope=TELEGRAM_EDIT_MESSAGE,
            workspace_id=workspace_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            target=str(local_message_id),
        )
        if not cleaned_text:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_EDIT_MESSAGE,
                status="blocked",
                reason_code="empty_message",
                correlation_id=correlation_id,
                idempotency_key=key,
                message_id=local_message_id,
            )

        allowed = await self._authorize(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_EDIT_MESSAGE,
            correlation_id=correlation_id,
            idempotency_key=key,
        )
        if not allowed:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_EDIT_MESSAGE,
                status="blocked",
                reason_code="missing_tool_grant",
                correlation_id=correlation_id,
                idempotency_key=key,
                message_id=local_message_id,
            )

        message, conversation = await self._message_with_conversation(
            workspace_id=workspace_id,
            message_id=local_message_id,
        )
        block_reason = _editable_message_block_reason(message, conversation)
        if block_reason:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_EDIT_MESSAGE,
                status="blocked",
                reason_code=block_reason,
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation.id if conversation else None,
                message_id=local_message_id,
            )
            await self._audit(result)
            return result

        if message.content == cleaned_text:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_EDIT_MESSAGE,
                status="replayed",
                reason_code="message_already_has_text",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation.id,
                message_id=message.id,
                external_message_id=message.external_message_id,
                delivery_state=message.delivery_state,
            )
            await self._audit(result)
            return result

        if not getattr(self._adapter.capabilities(), "edit_message", False):
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_EDIT_MESSAGE,
                status="unsupported",
                reason_code="telegram_edit_unsupported",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation.id,
                message_id=message.id,
                external_message_id=message.external_message_id,
            )
            await self._audit(result)
            return result

        try:
            edit_result = await self._adapter.edit_message(  # type: ignore[attr-defined]
                workspace_id=workspace_id,
                conversation_id=_conversation_ref(conversation),
                message_id=_remote_message_id(message),
                text=cleaned_text,
                idempotency_key=key,
            )
        except UnsupportedChannelCapability:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_EDIT_MESSAGE,
                status="unsupported",
                reason_code="telegram_edit_unsupported",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation.id,
                message_id=message.id,
                external_message_id=message.external_message_id,
            )
            await self._audit(result)
            return result

        message.content = cleaned_text
        message.edited_at = datetime.now(UTC)
        message.external_message_id = (
            edit_result.external_message_id or message.external_message_id
        )
        await self._session.commit()
        await self._grants.record_use(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_EDIT_MESSAGE,
        )
        result = self._result(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_EDIT_MESSAGE,
            status="executed",
            reason_code="message_edited",
            correlation_id=correlation_id,
            idempotency_key=key,
            conversation_id=conversation.id,
            message_id=message.id,
            external_message_id=message.external_message_id,
            delivery_state=message.delivery_state,
            payload={"text_length": len(cleaned_text)},
        )
        await self._audit(result)
        return result

    async def fetch_media(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        local_message_id: int,
        correlation_id: str,
        thumb: bool = False,
        idempotency_key: str | None = None,
    ) -> TelegramToolResult:
        key = _idempotency(
            idempotency_key,
            scope=TELEGRAM_FETCH_MEDIA,
            workspace_id=workspace_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            target=str(local_message_id),
        )
        allowed = await self._authorize(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_FETCH_MEDIA,
            correlation_id=correlation_id,
            idempotency_key=key,
        )
        if not allowed:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_FETCH_MEDIA,
                status="blocked",
                reason_code="missing_tool_grant",
                correlation_id=correlation_id,
                idempotency_key=key,
                message_id=local_message_id,
            )

        message, conversation = await self._message_with_conversation(
            workspace_id=workspace_id,
            message_id=local_message_id,
        )
        if message is None or conversation is None:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_FETCH_MEDIA,
                status="blocked",
                reason_code="message_not_found",
                correlation_id=correlation_id,
                idempotency_key=key,
                message_id=local_message_id,
            )
            await self._audit(result)
            return result
        if not _remote_message_id(message):
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_FETCH_MEDIA,
                status="blocked",
                reason_code="message_has_no_remote_id",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation.id,
                message_id=message.id,
            )
            await self._audit(result)
            return result
        if not self._adapter.capabilities().fetch_media_blob:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_FETCH_MEDIA,
                status="unsupported",
                reason_code="telegram_media_unsupported",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation.id,
                message_id=message.id,
            )
            await self._audit(result)
            return result

        blob = await self._adapter.fetch_media_blob(
            workspace_id=workspace_id,
            media=ChannelMediaRef(
                channel="telegram_dm",
                conversation_id=_conversation_ref(conversation),
                message_id=_remote_message_id(message),
            ),
            thumb=thumb,
        )
        await self._grants.record_use(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_FETCH_MEDIA,
        )
        result = self._result(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_FETCH_MEDIA,
            status="executed",
            reason_code="media_fetched",
            correlation_id=correlation_id,
            idempotency_key=key,
            conversation_id=conversation.id,
            message_id=message.id,
            payload={"mime_type": blob.mime_type, "byte_count": len(blob.data)},
        )
        await self._audit(result)
        return result

    async def sync_history(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        conversation_id: int,
        correlation_id: str,
        limit: int = 50,
        before_message_id: str | None = None,
        after_message_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TelegramToolResult:
        key = _idempotency(
            idempotency_key,
            scope=TELEGRAM_SYNC_HISTORY,
            workspace_id=workspace_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            target=str(conversation_id),
        )
        allowed = await self._authorize(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SYNC_HISTORY,
            correlation_id=correlation_id,
            idempotency_key=key,
        )
        if not allowed:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SYNC_HISTORY,
                status="blocked",
                reason_code="missing_tool_grant",
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )

        conversation = await self._conversation(workspace_id, conversation_id)
        block_reason = _telegram_conversation_block_reason(conversation)
        if block_reason:
            result = self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_SYNC_HISTORY,
                status="blocked",
                reason_code=block_reason,
                correlation_id=correlation_id,
                idempotency_key=key,
                conversation_id=conversation_id,
            )
            await self._audit(result)
            return result

        sync_runtime = self._sync
        if sync_runtime is None:
            from app.services.channel_conversation_sync import ChannelConversationSync

            sync_runtime = ChannelConversationSync()
            self._sync = sync_runtime

        sync_result = await sync_runtime.sync_conversation(
            session=self._session,
            workspace_id=workspace_id,
            conversation=conversation,
            limit=max(1, min(int(limit), 200)),
            after_external_message_id=after_message_id,
            before_external_message_id=before_message_id,
        )
        await self._grants.record_use(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SYNC_HISTORY,
        )
        result = self._result(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_SYNC_HISTORY,
            status="executed",
            reason_code="history_synced",
            correlation_id=correlation_id,
            idempotency_key=key,
            conversation_id=conversation_id,
            payload={
                "requested": sync_result.requested,
                "persisted": sync_result.persisted,
                "duplicates": sync_result.duplicates,
            },
        )
        await self._audit(result)
        return result

    async def watch_channel(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        channel_ref: str,
        correlation_id: str,
        action_proposal_type: str = "catalog.propose_update",
        permission_mode: str = "ask_always",
        idempotency_key: str | None = None,
    ) -> TelegramToolResult:
        key = _idempotency(
            idempotency_key,
            scope=TELEGRAM_WATCH_CHANNEL,
            workspace_id=workspace_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            target=channel_ref,
        )
        allowed = await self._authorize(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_WATCH_CHANNEL,
            correlation_id=correlation_id,
            idempotency_key=key,
        )
        if not allowed:
            return self._result(
                workspace_id=workspace_id,
                agent_id=agent_id,
                scope=TELEGRAM_WATCH_CHANNEL,
                status="blocked",
                reason_code="missing_tool_grant",
                correlation_id=correlation_id,
                idempotency_key=key,
            )
        trigger = await TriggerService(self._session).create(
            workspace_id=workspace_id,
            payload=TriggerInput(
                owner_agent_id=agent_id,
                event_source="source_changed",
                action_proposal_type=action_proposal_type,
                matching_scope={
                    "source_kind": "telegram_channel",
                    "channel_ref": channel_ref,
                    "required_tool_scope": TELEGRAM_WATCH_CHANNEL,
                },
                permission_mode=permission_mode,  # type: ignore[arg-type]
                notes=f"Watch Telegram source {channel_ref} for agent {agent_id}.",
            ),
        )
        await self._grants.record_use(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_WATCH_CHANNEL,
        )
        result = self._result(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=TELEGRAM_WATCH_CHANNEL,
            status="executed",
            reason_code="channel_watch_configured",
            correlation_id=correlation_id,
            idempotency_key=key,
            trigger_id=trigger.id,
            payload={"channel_ref": channel_ref, "action_proposal_type": action_proposal_type},
        )
        await self._audit(result)
        return result

    async def _authorize(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        scope: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> bool:
        allowed = await self._grants.check_grant(
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=scope,
        )
        if not allowed:
            await self._audit(
                self._result(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    scope=scope,
                    status="blocked",
                    reason_code="missing_tool_grant",
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
            )
        return allowed

    async def _conversation(
        self, workspace_id: int, conversation_id: int
    ) -> Conversation | None:
        return await self._session.scalar(
            select(Conversation).where(
                Conversation.workspace_id == workspace_id,
                Conversation.id == conversation_id,
            )
        )

    async def _message_with_conversation(
        self, *, workspace_id: int, message_id: int
    ) -> tuple[Message | None, Conversation | None]:
        row = (
            await self._session.execute(
                select(Message, Conversation)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(Conversation.workspace_id == workspace_id, Message.id == message_id)
            )
        ).first()
        if row is None:
            return None, None
        message, conversation = row
        return message, conversation

    def _result(self, **kwargs: Any) -> TelegramToolResult:
        return TelegramToolResult(**kwargs)

    async def _audit(self, result: TelegramToolResult) -> None:
        await self._repository.append_event(
            CommercialEvent(
                event_id=f"event:{result.idempotency_key}",
                workspace_id=result.workspace_id,
                source_type="telegram_tool_runtime",
                source_ref=f"agent:{result.agent_id}:{result.scope}",
                actor_type="agent",
                correlation_id=result.correlation_id,
                idempotency_key=f"event:{result.idempotency_key}",
                payload=result.model_dump(mode="json"),
            )
        )


def _telegram_conversation_block_reason(conversation: Conversation | None) -> str | None:
    if conversation is None:
        return "conversation_not_found"
    channel = (conversation.channel or "telegram_dm").strip().lower()
    if channel == "dm":
        channel = "telegram_dm"
    if channel != "telegram_dm":
        return "conversation_not_telegram"
    if conversation.telegram_chat_id is None and not conversation.external_chat_id:
        return "telegram_chat_missing"
    return None


def _send_message_conversation_block_reason(conversation: Conversation | None) -> str | None:
    if conversation is None:
        return "conversation_not_found"
    channel = (conversation.channel or "telegram_dm").strip().lower()
    if channel == "dm":
        channel = "telegram_dm"
    if channel == "sandbox":
        return None
    if channel == "instagram_dm":
        # Instagram sends route through DeliveryService -> InstagramChannelAdapter
        # (Graph API). The 24h-window guard lives in DeliveryService.
        if not conversation.external_chat_id:
            return "external_chat_missing"
        return None
    return _telegram_conversation_block_reason(conversation)


def _editable_message_block_reason(
    message: Message | None, conversation: Conversation | None
) -> str | None:
    if message is None or conversation is None:
        return "message_not_found"
    conversation_reason = _telegram_conversation_block_reason(conversation)
    if conversation_reason:
        return conversation_reason
    if message.sender_type not in {SenderType.SELLER.value, SenderType.AI.value}:
        return "not_oqim_owned_message"
    if not _remote_message_id(message):
        return "message_has_no_remote_id"
    return None


def _conversation_ref(conversation: Conversation) -> str:
    return str(conversation.external_chat_id or conversation.telegram_chat_id)


def _remote_message_id(message: Message) -> str:
    return str(message.external_message_id or message.telegram_message_id or "")


def _remote_message_int(message: Message) -> int | None:
    if message.telegram_message_id is not None:
        return int(message.telegram_message_id)
    if message.external_message_id:
        try:
            return int(str(message.external_message_id))
        except ValueError:
            return None
    return None


def _message_delivery_is_confirmed(message: Message) -> bool:
    state = str(message.delivery_state or "").strip().lower()
    return state in CONFIRMED_MESSAGE_DELIVERY_STATES or bool(message.external_message_id)


def _message_delivery_is_unknown(message: Message) -> bool:
    state = str(message.delivery_state or "").strip().lower()
    return state in UNKNOWN_MESSAGE_DELIVERY_STATES


def _outbound_media_from_mapping(
    *,
    media_ref: str,
    value: dict[str, Any],
) -> ChannelOutboundMedia | None:
    url = str(value.get("url") or "").strip()
    if not url.startswith(("https://", "http://")):
        return None
    mime_type = str(
        value.get("content_type")
        or value.get("mime_type")
        or value.get("mimeType")
        or ""
    ).strip() or None
    file_name = str(
        value.get("file_name")
        or value.get("fileName")
        or value.get("filename")
        or ""
    ).strip() or None
    return ChannelOutboundMedia(
        url=url,
        media_type=_outbound_media_type(
            str(value.get("media_type") or value.get("media_kind") or ""),
            mime_type=mime_type,
            url=url,
        ),
        mime_type=mime_type,
        file_name=file_name,
        asset_id=media_ref,
    )


def _outbound_media_type(raw_type: str, *, mime_type: str | None, url: str) -> str:
    lowered = raw_type.strip().lower()
    mime = str(mime_type or "").lower()
    url_lower = url.lower()
    if lowered in {"image", "photo", "png", "jpg", "jpeg", "webp"}:
        return "photo"
    if lowered in {"video", "gif", "audio", "voice", "document"}:
        return lowered
    if mime.startswith("image/") or url_lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return "photo"
    if mime.startswith("video/") or url_lower.endswith((".mp4", ".mov", ".webm")):
        return "video"
    if mime.startswith("audio/") or url_lower.endswith((".mp3", ".m4a", ".ogg", ".wav")):
        return "audio"
    return "document"


def _idempotency(
    raw: str | None,
    *,
    scope: str,
    workspace_id: int,
    agent_id: int,
    correlation_id: str,
    target: str,
) -> str:
    if raw:
        return _bounded(raw)
    seed = f"{scope}:{workspace_id}:{agent_id}:{correlation_id}:{target}:{uuid.uuid5(uuid.NAMESPACE_URL, target)}"
    return _bounded(f"{scope}:{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:40]}")


def _bounded(value: str, limit: int = 120) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return f"{text[: limit - 33]}:{digest}"
