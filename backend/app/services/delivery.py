"""Unified message delivery via GramJS sidecar.

Handles optional typing/pacing, send retry, delivery state, and WebSocket
broadcasting for runtime action sends.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.correlation import current_correlation_id
from app.core.event_spine import (
    DeliveryConfirmed,
    DeliveryFailed,
    DeliveryUnknown,
    MsgMediaSent,
    MsgSent,
)
from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message
from app.models.workspace import Workspace
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.services.channel_adapter_contract import (
    ChannelOutboundMedia,
    PermanentChannelSendError,
    TelegramChannelAdapter,
    UnsupportedChannelCapability,
)
from app.services.channel_sync_runtime import ChannelSyncRateLimitError
from app.services.delivery_runtime import (
    DELIVERY_CONFIRMED,
    DELIVERY_FAILED,
    DELIVERY_MAX_ATTEMPTS,
    DELIVERY_REQUESTED,
    DELIVERY_SENDING,
    DELIVERY_UNKNOWN,
    record_delivery_state,
)
from app.services.instagram_channel_adapter import InstagramChannelAdapter
from app.services.instagram_messaging_policy import (
    instagram_window_is_open,
    queue_instagram_owner_notification,
)

logger = get_logger("services.delivery")

# Default delay range (ms) when Business Brain has no learned delay_range.
_DEFAULT_DELAY_MIN_MS = 1500
_DEFAULT_DELAY_MAX_MS = 3000

# Timeouts
_SIDE_TIMEOUT = 5.0  # seconds (typing indicator)

# Retry backoff for /send (exponential: 1s, 3s, 9s)
_BACKOFF_SECONDS = [1, 3, 9]
_MAX_RETRIES = DELIVERY_MAX_ATTEMPTS


def _valid_delay_range(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    min_ms = value.get("min_ms")
    max_ms = value.get("max_ms")
    if not isinstance(min_ms, int | float) or not isinstance(max_ms, int | float):
        return None
    if min_ms < 0 or max_ms < min_ms:
        return None
    return {"min_ms": min_ms, "max_ms": max_ms}


@dataclass(slots=True)
class DeliveryResult:
    """Outcome of a delivery attempt."""

    success: bool
    external_message_id: str | None = None
    error: str | None = None
    state: str | None = None
    retry_after_seconds: float | None = None  # set on sidecar 429 (FloodWait)


class DeliveryService:
    """Unified message delivery via GramJS sidecar.

    Lifecycle: created once in app lifespan, injected via FastAPI Depends.
    """

    def __init__(
        self,
        sidecar_url: str,
        sidecar_api_key: str,
        *,
        event_spine: Any | None = None,
    ) -> None:
        self._sidecar_url = sidecar_url.rstrip("/")
        self._sidecar_api_key = sidecar_api_key
        self._event_spine = event_spine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
    ) -> DeliveryResult:
        """Send a message to Telegram with human-like behavior.

        Steps:
        1. Resolve telegram_chat_id from conversation
        2. Mark customer messages as read (POST /read) -- fire-and-forget
        3. Optionally send typing indicator (POST /typing) -- fire-and-forget
        4. Wait explicit pacing delay or Business Brain voice delay
        5. Send message with 3-retry backoff (POST /send)

        Callers are responsible for updating action records and broadcasting WS
        events based on the returned DeliveryResult.
        """
        # Step 1: resolve chat ID
        conv = await self._resolve_conversation(conversation_id, db)
        if conv is None:
            error_msg = f"Conversation {conversation_id} not found"
            logger.error(error_msg)
            return DeliveryResult(success=False, error=error_msg)

        if conv.channel == "sandbox":
            return await self._deliver_sandbox_message(
                conv,
                text,
                db=db,
                workspace_id=workspace_id,
                action_record_id=action_record_id,
                client_idempotency_key=client_idempotency_key,
                message_id=message_id,
            )

        resolved = await self._adapter_and_chat(conv, db)
        if isinstance(resolved, DeliveryResult):
            logger.error(
                "delivery resolve failed for conversation %s: %s",
                conversation_id, resolved.error,
            )
            return resolved
        adapter, chat_id = resolved
        is_telegram = isinstance(adapter, TelegramChannelAdapter)

        ws_id = workspace_id or conv.workspace_id
        send_idempotency_key = client_idempotency_key or f"send:{uuid.uuid4().hex}"

        if conv.channel == "instagram_dm":
            gate = await self._instagram_window_gate(
                conv, chat_id,
                db=db,
                workspace_id=ws_id,
                conversation_id=conversation_id,
                message_id=message_id,
                action_record_id=action_record_id,
                client_idempotency_key=send_idempotency_key,
            )
            if gate is not None:
                return gate

        await record_delivery_state(
            db,
            workspace_id=ws_id,
            conversation_id=conversation_id,
            message_id=message_id,
            action_record_id=action_record_id,
            channel=conv.channel or "telegram_dm",
            channel_conversation_id=chat_id,
            client_idempotency_key=send_idempotency_key,
            state=DELIVERY_REQUESTED,
        )
        await db.commit()

        # Step 2: mark as read (fire-and-forget, Telegram only)
        if is_telegram:
            await self._fire_and_forget(self._mark_read(chat_id, ws_id))

        # Step 3: typing indicator (fire-and-forget, Telegram only)
        if typing_indicator and is_telegram:
            await self._fire_and_forget(self._send_typing(chat_id, ws_id))

        # Step 4: human delay
        delay = (
            max(0.0, float(delay_override_seconds))
            if delay_override_seconds is not None
            else await self._get_delay(ws_id, db)
        )
        await asyncio.sleep(delay)

        try:
            # Step 5: send with retry
            return await self._send_with_retry(
                chat_id, text, ws_id,
                adapter=adapter,
                db=db,
                conversation_id=conversation_id,
                action_record_id=action_record_id,
                client_idempotency_key=send_idempotency_key,
                message_id=message_id,
                channel=conv.channel or "telegram_dm",
                reply_to_message_id=reply_to_message_id,
            )
        finally:
            if is_telegram:
                await self._fire_and_forget(self._send_typing(chat_id, ws_id, typing=False))
                await self._online_tail(workspace_id=ws_id, seconds=online_tail_seconds)

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
    ) -> DeliveryResult:
        """Send approved outbound media through the channel adapter.

        Media sends intentionally record delivery_runtime state only for now.
        Canonical media-send events and echo reconciliation are a separate
        runtime proof because MsgSent currently represents text sends.
        """
        conv = await self._resolve_conversation(conversation_id, db)
        if conv is None:
            error_msg = f"Conversation {conversation_id} not found"
            logger.error(error_msg)
            return DeliveryResult(success=False, error=error_msg)

        resolved = await self._adapter_and_chat(conv, db)
        if isinstance(resolved, DeliveryResult):
            logger.error(
                "delivery resolve failed for conversation %s: %s",
                conversation_id, resolved.error,
            )
            return resolved
        adapter, chat_id = resolved
        is_telegram = isinstance(adapter, TelegramChannelAdapter)

        ws_id = workspace_id or conv.workspace_id
        send_idempotency_key = client_idempotency_key or f"send:{uuid.uuid4().hex}"

        if conv.channel == "instagram_dm":
            gate = await self._instagram_window_gate(
                conv, chat_id,
                db=db,
                workspace_id=ws_id,
                conversation_id=conversation_id,
                message_id=message_id,
                action_record_id=action_record_id,
                client_idempotency_key=send_idempotency_key,
            )
            if gate is not None:
                return gate

        await record_delivery_state(
            db,
            workspace_id=ws_id,
            conversation_id=conversation_id,
            message_id=message_id,
            action_record_id=action_record_id,
            channel=conv.channel or "telegram_dm",
            channel_conversation_id=chat_id,
            client_idempotency_key=send_idempotency_key,
            state=DELIVERY_REQUESTED,
        )
        await db.commit()

        if is_telegram:
            await self._fire_and_forget(self._mark_read(chat_id, ws_id))
        if typing_indicator and is_telegram:
            await self._fire_and_forget(self._send_typing(chat_id, ws_id))

        delay = (
            max(0.0, float(delay_override_seconds))
            if delay_override_seconds is not None
            else await self._get_delay(ws_id, db)
        )
        await asyncio.sleep(delay)

        try:
            return await self._send_media_with_retry(
                chat_id,
                media,
                ws_id,
                adapter=adapter,
                caption=caption,
                db=db,
                conversation_id=conversation_id,
                action_record_id=action_record_id,
                client_idempotency_key=send_idempotency_key,
                message_id=message_id,
                channel=conv.channel or "telegram_dm",
                reply_to_message_id=reply_to_message_id,
            )
        finally:
            if is_telegram:
                await self._fire_and_forget(self._send_typing(chat_id, ws_id, typing=False))
                await self._online_tail(workspace_id=ws_id, seconds=online_tail_seconds)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._sidecar_api_key:
            h["X-Sidecar-Key"] = self._sidecar_api_key
        return h

    async def _resolve_conversation(
        self, conversation_id: int, db: AsyncSession,
    ) -> Conversation | None:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def _deliver_sandbox_message(
        self,
        conversation: Conversation,
        text: str,
        *,
        db: AsyncSession,
        workspace_id: int | None,
        action_record_id: int | None,
        client_idempotency_key: str | None,
        message_id: int | None,
    ) -> DeliveryResult:
        """Confirm sandbox sends locally; sandbox has no Telegram chat id."""
        from app.modules.conversation_core.service import create_seller_placeholder_message

        ws_id = workspace_id or conversation.workspace_id
        send_idempotency_key = client_idempotency_key or f"send:{uuid.uuid4().hex}"
        channel_conversation_id = conversation.external_chat_id or f"sandbox:{conversation.id}"
        seller_message = await db.get(Message, message_id) if message_id is not None else None
        if seller_message is None:
            seller_message = await create_seller_placeholder_message(
                db,
                conversation=conversation,
                content=text,
                client_message_uuid=send_idempotency_key,
                delivery_state=DELIVERY_CONFIRMED,
            )
        else:
            seller_message.content = text
            seller_message.client_message_uuid = send_idempotency_key
            seller_message.delivery_state = DELIVERY_CONFIRMED
        external_id = f"sandbox:{seller_message.id}"
        seller_message.external_message_id = external_id
        await record_delivery_state(
            db,
            workspace_id=ws_id,
            conversation_id=conversation.id,
            message_id=seller_message.id if message_id is None else message_id,
            action_record_id=action_record_id,
            channel=conversation.channel or "sandbox",
            channel_conversation_id=channel_conversation_id,
            client_idempotency_key=send_idempotency_key,
            state=DELIVERY_CONFIRMED,
            external_message_id=external_id,
        )
        await db.commit()
        return DeliveryResult(
            success=True,
            external_message_id=external_id,
            state="confirmed",
        )

    async def _adapter_and_chat(
        self, conv: Conversation, db: AsyncSession,
    ) -> tuple[Any, str] | DeliveryResult:
        """Resolve (adapter, channel_chat_id) for a conversation, or an error result."""
        channel = conv.channel or "telegram_dm"
        if channel == "instagram_dm":
            workspace = await db.get(Workspace, conv.workspace_id)
            token = workspace.instagram_access_token if workspace else None
            if not token:
                return DeliveryResult(success=False, error="instagram_not_connected", state="failed")
            if not conv.external_chat_id:
                return DeliveryResult(success=False, error="no_external_chat_id", state="failed")
            adapter = InstagramChannelAdapter(
                account_id=str(workspace.instagram_page_id or ""),
                access_token=token,
            )
            return adapter, str(conv.external_chat_id)
        if not conv.telegram_chat_id:
            return DeliveryResult(
                success=False,
                error=f"No telegram_chat_id for conversation {conv.id}",
                state="failed",
            )
        adapter = TelegramChannelAdapter(
            sidecar_url=self._sidecar_url,
            sidecar_api_key=self._sidecar_api_key,
        )
        return adapter, str(conv.telegram_chat_id)

    async def _instagram_window_gate(
        self,
        conv: Conversation,
        chat_id: str,
        *,
        db: AsyncSession,
        workspace_id: int,
        conversation_id: int,
        message_id: int | None,
        action_record_id: int | None,
        client_idempotency_key: str,
    ) -> DeliveryResult | None:
        """Return a failed DeliveryResult if the Instagram 24h window is closed.

        Returns None when the window is open (caller should proceed).
        """
        if not await instagram_window_is_open(db, conversation_id):
            await record_delivery_state(
                db,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                message_id=message_id,
                action_record_id=action_record_id,
                channel="instagram_dm",
                channel_conversation_id=chat_id,
                client_idempotency_key=client_idempotency_key,
                state=DELIVERY_FAILED,
                error="instagram_window_closed",
            )
            customer = await db.get(Customer, conv.customer_id)
            customer_label = customer.display_name if customer else None
            await queue_instagram_owner_notification(
                db,
                workspace_id=workspace_id,
                title="Instagram javob oynasi yopildi",
                summary=(
                    "Mijozning oxirgi xabaridan 24 soat o'tdi — Instagram qoidasi "
                    "bo'yicha endi avtomatik javob yuborib bo'lmaydi."
                ),
                recommended_action="Mijoz yana yozsa, agent avtomatik davom etadi.",
                idempotency_key=(
                    f"ig_window_closed:{workspace_id}:{conversation_id}:"
                    f"{datetime.now(UTC).strftime('%Y%m%d%H')}"
                ),
                conversation_id=conversation_id,
                customer_label=customer_label,
            )
            await db.commit()
            return DeliveryResult(success=False, error="instagram_window_closed", state="failed")
        return None

    async def _mark_read(self, chat_id: str, workspace_id: int) -> None:
        adapter = TelegramChannelAdapter(
            sidecar_url=self._sidecar_url,
            sidecar_api_key=self._sidecar_api_key,
        )
        await adapter.mark_read(
            workspace_id=workspace_id,
            conversation_id=chat_id,
            message_id="0",
        )

    async def _online_tail(self, *, workspace_id: int, seconds: float) -> None:
        tail_seconds = max(0.0, min(float(seconds or 0.0), 2.0))
        if tail_seconds <= 0:
            return
        await self._fire_and_forget(self._send_online(workspace_id))
        await asyncio.sleep(tail_seconds)

    async def _send_online(self, workspace_id: int) -> None:
        async with httpx.AsyncClient(timeout=_SIDE_TIMEOUT) as client:
            response = await client.post(
                f"{self._sidecar_url}/online",
                json={"workspaceId": workspace_id, "allowOnlinePresence": True},
                headers=self._headers(),
            )
        response.raise_for_status()

    async def _send_typing(self, chat_id: str, workspace_id: int, *, typing: bool = True) -> None:
        async with httpx.AsyncClient(timeout=_SIDE_TIMEOUT) as client:
            response = await client.post(
                f"{self._sidecar_url}/typing",
                json={"chatId": chat_id, "workspaceId": workspace_id, "typing": typing},
                headers=self._headers(),
            )
        response.raise_for_status()
        payload: dict[str, Any] = {}
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        ok = payload.get("ok") is not False
        if ok:
            logger.info(
                "Sidecar typing state sent for chat=%s workspace=%s typing=%s",
                chat_id,
                workspace_id,
                typing,
            )
        else:
            logger.warning(
                "Sidecar typing state returned warning for chat=%s workspace=%s typing=%s warning=%s",
                chat_id,
                workspace_id,
                typing,
                payload.get("warning") or payload.get("error") or "unknown",
            )

    async def _get_delay(self, workspace_id: int, db: AsyncSession) -> float:
        """Load delay range from Business Brain voice projection, then default."""
        delay_range = await self._get_business_brain_delay_range(workspace_id, db)
        if delay_range is not None:
            min_ms = delay_range.get("min_ms", _DEFAULT_DELAY_MIN_MS)
            max_ms = delay_range.get("max_ms", _DEFAULT_DELAY_MAX_MS)
            return random.uniform(min_ms, max_ms) / 1000.0
        return random.uniform(_DEFAULT_DELAY_MIN_MS, _DEFAULT_DELAY_MAX_MS) / 1000.0

    async def _get_business_brain_delay_range(
        self,
        workspace_id: int,
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        try:
            projection = await CommercialSpineRepository(db).get_projection(
                workspace_id=workspace_id,
                projection_ref="voice_profile:seller_voice",
            )
            if projection is None or projection.degraded:
                return None
            traits = projection.state.get("traits")
            if not isinstance(traits, list):
                return None
            for trait in reversed(traits):
                if not isinstance(trait, dict):
                    continue
                delay_range = _valid_delay_range(trait.get("delay_range"))
                if delay_range is not None:
                    return delay_range
        except Exception:
            logger.warning(
                "Failed to load Business Brain delay_range for workspace=%d",
                workspace_id,
                exc_info=True,
            )
        return None

    async def _send_media_with_retry(
        self,
        chat_id: str,
        media: ChannelOutboundMedia,
        workspace_id: int,
        *,
        adapter: Any,
        caption: str | None = None,
        db: AsyncSession | None = None,
        conversation_id: int | None = None,
        action_record_id: int | None = None,
        client_idempotency_key: str | None = None,
        message_id: int | None = None,
        channel: str = "telegram_dm",
        reply_to_message_id: int | None = None,
    ) -> DeliveryResult:
        """Send outbound media via channel adapter with retry/backoff."""
        last_error = ""
        send_idempotency_key = client_idempotency_key or f"send:{uuid.uuid4().hex}"
        for attempt in range(_MAX_RETRIES):
            if db is not None and conversation_id is not None:
                await record_delivery_state(
                    db,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    action_record_id=action_record_id,
                    channel=channel,
                    channel_conversation_id=chat_id,
                    client_idempotency_key=send_idempotency_key,
                    state=DELIVERY_SENDING,
                )
                await db.commit()

            if attempt == 0 and self._event_spine is not None and conversation_id is not None:
                self._event_spine.publish(
                    MsgMediaSent.build(
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        media_type=media.media_type,
                        media_url=media.url,
                        media_asset_id=media.asset_id,
                        caption=caption,
                        action_record_id=action_record_id,
                        client_idempotency_key=send_idempotency_key,
                        channel_conversation_id=chat_id,
                        correlation_id=current_correlation_id(),
                    )
                )

            try:
                send_result = await adapter.send_media(
                    workspace_id=workspace_id,
                    conversation_id=chat_id,
                    media=media,
                    caption=caption,
                    idempotency_key=send_idempotency_key,
                    reply_to_message_id=reply_to_message_id,
                )
                external_id = send_result.external_message_id
                logger.info(
                    "Sidecar media send succeeded for chat=%s (attempt %d/3, external_id=%s)",
                    chat_id, attempt + 1, external_id,
                )
                await self._record_confirmed_after_provider_accept(
                    db=db,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    chat_id=chat_id,
                    external_id=external_id,
                    action_record_id=action_record_id,
                    client_idempotency_key=send_idempotency_key,
                    message_id=message_id,
                    channel=channel,
                    media=True,
                )

                return DeliveryResult(
                    success=True,
                    external_message_id=external_id,
                    state="confirmed",
                )
            except httpx.TimeoutException as exc:
                last_error = str(exc)
                logger.warning(
                    "Sidecar media send timed out for chat=%s with idempotency_key=%s; delivery state unknown",
                    chat_id,
                    send_idempotency_key,
                )
                if self._event_spine is not None and conversation_id is not None:
                    self._event_spine.publish(
                        DeliveryUnknown.build(
                            workspace_id=workspace_id,
                            conversation_id=conversation_id,
                            action_record_id=action_record_id,
                            client_idempotency_key=send_idempotency_key,
                            reason=last_error or "sidecar_timeout",
                            channel_conversation_id=chat_id,
                            correlation_id=current_correlation_id(),
                        )
                    )
                if db is not None and conversation_id is not None:
                    await record_delivery_state(
                        db,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        action_record_id=action_record_id,
                        channel=channel,
                        channel_conversation_id=chat_id,
                        client_idempotency_key=send_idempotency_key,
                        state=DELIVERY_UNKNOWN,
                        error=last_error or "sidecar_timeout",
                    )
                    await db.commit()
                return DeliveryResult(success=False, error=last_error, state="unknown")
            except ChannelSyncRateLimitError as exc:
                last_error = f"rate_limited retry_after={exc.retry_after_seconds:.2f}s"
                logger.warning(
                    "Sidecar media send rate limited for chat=%s with idempotency_key=%s; stopping retries for %.2fs",
                    chat_id,
                    send_idempotency_key,
                    exc.retry_after_seconds,
                )
                await self._record_failed_delivery(
                    db=db,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    chat_id=chat_id,
                    action_record_id=action_record_id,
                    client_idempotency_key=send_idempotency_key,
                    message_id=message_id,
                    channel=channel,
                    error=last_error,
                )
                return DeliveryResult(
                    success=False,
                    error=last_error,
                    state="failed",
                    retry_after_seconds=exc.retry_after_seconds,
                )
            except PermanentChannelSendError as exc:
                last_error = str(exc) or "permanent_send_failure"
                logger.warning(
                    "Sidecar media send permanently failed for chat=%s (not retrying): %s",
                    chat_id,
                    exc,
                )
                await self._record_failed_delivery(
                    db=db,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    chat_id=chat_id,
                    action_record_id=action_record_id,
                    client_idempotency_key=send_idempotency_key,
                    message_id=message_id,
                    channel=channel,
                    error=last_error,
                )
                return DeliveryResult(success=False, error=last_error, state="failed")
            except UnsupportedChannelCapability as exc:
                last_error = str(exc)
                logger.warning("Sidecar media send unsupported for chat=%s: %s", chat_id, exc)
                await self._record_failed_delivery(
                    db=db,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    chat_id=chat_id,
                    action_record_id=action_record_id,
                    client_idempotency_key=send_idempotency_key,
                    message_id=message_id,
                    channel=channel,
                    error=last_error,
                )
                return DeliveryResult(success=False, error=last_error, state="failed")
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_error = str(exc)
                logger.warning(
                    "Sidecar media send attempt %d/3 failed for chat=%s: %s",
                    attempt + 1, chat_id, exc,
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])

        logger.error("Sidecar media send failed after 3 retries for chat=%s: %s", chat_id, last_error)
        await self._record_failed_delivery(
            db=db,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            chat_id=chat_id,
            action_record_id=action_record_id,
            client_idempotency_key=send_idempotency_key,
            message_id=message_id,
            channel=channel,
            error=last_error or "delivery_failed",
        )
        return DeliveryResult(success=False, error=last_error, state="failed")

    async def _send_with_retry(
        self,
        chat_id: str,
        text: str,
        workspace_id: int,
        *,
        adapter: Any,
        db: AsyncSession | None = None,
        conversation_id: int | None = None,
        action_record_id: int | None = None,
        client_idempotency_key: str | None = None,
        message_id: int | None = None,
        channel: str = "telegram_dm",
        reply_to_message_id: int | None = None,
    ) -> DeliveryResult:
        """Send message via channel adapter with 3-retry exponential backoff."""
        last_error: str = ""
        send_idempotency_key = client_idempotency_key or f"send:{uuid.uuid4().hex}"
        for attempt in range(_MAX_RETRIES):
            if db is not None and conversation_id is not None:
                await record_delivery_state(
                    db,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    action_record_id=action_record_id,
                    channel=channel,
                    channel_conversation_id=chat_id,
                    client_idempotency_key=send_idempotency_key,
                    state=DELIVERY_SENDING,
                )
                await db.commit()

            # Phase 1 event spine: publish MsgSent BEFORE first adapter attempt.
            if attempt == 0 and self._event_spine is not None and conversation_id is not None:
                self._event_spine.publish(
                    MsgSent.build(
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        text=text,
                        action_record_id=action_record_id,
                        client_idempotency_key=send_idempotency_key,
                        channel_conversation_id=chat_id,
                        correlation_id=current_correlation_id(),
                    )
                )
            try:
                send_result = await adapter.send_message(
                    workspace_id=workspace_id,
                    conversation_id=chat_id,
                    text=text,
                    idempotency_key=send_idempotency_key,
                    reply_to_message_id=reply_to_message_id,
                )
                external_id = send_result.external_message_id
                logger.info(
                    "Sidecar send succeeded for chat=%s (attempt %d/3, external_id=%s)",
                    chat_id, attempt + 1, external_id,
                )

                await self._record_confirmed_after_provider_accept(
                    db=db,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    chat_id=chat_id,
                    external_id=external_id,
                    action_record_id=action_record_id,
                    client_idempotency_key=send_idempotency_key,
                    message_id=message_id,
                    channel=channel,
                    media=False,
                )

                return DeliveryResult(
                    success=True,
                    external_message_id=external_id,
                    state="confirmed",
                )
            except httpx.TimeoutException as exc:
                last_error = str(exc)
                logger.warning(
                    "Sidecar send timed out for chat=%s with idempotency_key=%s; delivery state unknown",
                    chat_id,
                    send_idempotency_key,
                )
                if self._event_spine is not None and conversation_id is not None:
                    self._event_spine.publish(
                        DeliveryUnknown.build(
                            workspace_id=workspace_id,
                            conversation_id=conversation_id,
                            action_record_id=action_record_id,
                            client_idempotency_key=send_idempotency_key,
                            reason=last_error or "sidecar_timeout",
                            channel_conversation_id=chat_id,
                            correlation_id=current_correlation_id(),
                        )
                    )
                if db is not None and conversation_id is not None:
                    await record_delivery_state(
                        db,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        action_record_id=action_record_id,
                        channel=channel,
                        channel_conversation_id=chat_id,
                        client_idempotency_key=send_idempotency_key,
                        state=DELIVERY_UNKNOWN,
                        error=last_error or "sidecar_timeout",
                    )
                    await db.commit()
                return DeliveryResult(success=False, error=last_error, state="unknown")
            except ChannelSyncRateLimitError as exc:
                last_error = f"rate_limited retry_after={exc.retry_after_seconds:.2f}s"
                logger.warning(
                    "Sidecar send rate limited for chat=%s with idempotency_key=%s; stopping retries for %.2fs",
                    chat_id,
                    send_idempotency_key,
                    exc.retry_after_seconds,
                )
                await self._record_failed_delivery(
                    db=db,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    chat_id=chat_id,
                    action_record_id=action_record_id,
                    client_idempotency_key=send_idempotency_key,
                    message_id=message_id,
                    channel=channel,
                    error=last_error,
                )
                return DeliveryResult(
                    success=False,
                    error=last_error,
                    state="failed",
                    retry_after_seconds=exc.retry_after_seconds,
                )
            except UnsupportedChannelCapability as exc:
                last_error = str(exc)
                logger.warning("Channel send unsupported for chat=%s: %s", chat_id, exc)
                await self._record_failed_delivery(
                    db=db,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    chat_id=chat_id,
                    action_record_id=action_record_id,
                    client_idempotency_key=send_idempotency_key,
                    message_id=message_id,
                    channel=channel,
                    error=last_error,
                )
                return DeliveryResult(success=False, error=last_error, state="failed")
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_error = str(exc)
                logger.warning(
                    "Sidecar send attempt %d/3 failed for chat=%s: %s",
                    attempt + 1, chat_id, exc,
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt])

        logger.error("Sidecar send failed after 3 retries for chat=%s: %s", chat_id, last_error)
        await self._record_failed_delivery(
            db=db,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            chat_id=chat_id,
            action_record_id=action_record_id,
            client_idempotency_key=send_idempotency_key,
            message_id=message_id,
            channel=channel,
            error=last_error or "delivery_failed",
        )
        return DeliveryResult(success=False, error=last_error, state="failed")

    async def _record_failed_delivery(
        self,
        *,
        db: AsyncSession | None,
        workspace_id: int,
        conversation_id: int | None,
        chat_id: str,
        action_record_id: int | None,
        client_idempotency_key: str,
        message_id: int | None,
        channel: str,
        error: str,
    ) -> None:
        """Publish DeliveryFailed + record DELIVERY_FAILED state + commit."""
        if self._event_spine is not None and conversation_id is not None:
            self._event_spine.publish(
                DeliveryFailed.build(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    action_record_id=action_record_id,
                    client_idempotency_key=client_idempotency_key,
                    error=error,
                    channel_conversation_id=chat_id,
                    correlation_id=current_correlation_id(),
                )
            )
        if db is not None and conversation_id is not None:
            await record_delivery_state(
                db,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                message_id=message_id,
                action_record_id=action_record_id,
                channel=channel,
                channel_conversation_id=chat_id,
                client_idempotency_key=client_idempotency_key,
                state=DELIVERY_FAILED,
                error=error,
            )
            await db.commit()

    async def _record_confirmed_after_provider_accept(
        self,
        *,
        db: AsyncSession | None,
        workspace_id: int,
        conversation_id: int | None,
        chat_id: str,
        external_id: str | None,
        action_record_id: int | None,
        client_idempotency_key: str,
        message_id: int | None,
        channel: str,
        media: bool,
    ) -> None:
        """Best-effort bookkeeping after Telegram or an adapter accepts a send."""
        delivery_kind = "media" if media else "message"
        if self._event_spine is not None and conversation_id is not None:
            try:
                self._event_spine.publish(
                    DeliveryConfirmed.build(
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        action_record_id=action_record_id,
                        external_message_id=external_id,
                        client_idempotency_key=client_idempotency_key,
                        channel_conversation_id=chat_id,
                        correlation_id=current_correlation_id(),
                    )
                )
            except Exception:
                logger.warning(
                    "DeliveryConfirmed publish failed after provider accepted %s send for chat=%s idempotency_key=%s",
                    delivery_kind,
                    chat_id,
                    client_idempotency_key,
                    exc_info=True,
                )

        if db is None or conversation_id is None:
            return

        try:
            await record_delivery_state(
                db,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                message_id=message_id,
                action_record_id=action_record_id,
                channel=channel,
                channel_conversation_id=chat_id,
                client_idempotency_key=client_idempotency_key,
                state=DELIVERY_CONFIRMED,
                external_message_id=external_id,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.warning(
                "Delivery runtime confirmation failed after provider accepted %s send for chat=%s idempotency_key=%s",
                delivery_kind,
                chat_id,
                client_idempotency_key,
                exc_info=True,
            )

    @staticmethod
    async def _fire_and_forget(coro) -> None:
        """Run a coroutine, swallowing any exception (fire-and-forget)."""
        try:
            await coro
        except Exception as exc:
            logger.warning("Fire-and-forget failed: %s", exc)
