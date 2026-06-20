"""Channel adapter contract for Telegram, Instagram, WhatsApp, and future DMs."""

from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from app.core.config import get_settings
from app.core.event_spine import MsgInbound
from app.services.channel_sidecar_client import extract_retry_after_seconds
from app.services.channel_sync_runtime import ChannelSyncRateLimitError
from app.services.media_types import normalize_media_type


ChannelName = Literal["telegram_dm", "instagram_dm", "whatsapp_dm"]


class UnsupportedChannelCapability(RuntimeError):
    """Raised when a channel explicitly does not support a requested action."""


class ChannelHistorySourceUnavailable(UnsupportedChannelCapability):
    """Raised when upstream history transport cannot be reached right now."""


class ChannelMediaUnavailable(UnsupportedChannelCapability):
    """Raised when a channel has no bytes for the requested media."""


class ChannelMediaSourceUnavailable(UnsupportedChannelCapability):
    """Raised when the upstream media transport cannot be reached."""


class ChannelMediaRangeNotSatisfiable(UnsupportedChannelCapability):
    """Raised when a media byte-range request is invalid for the asset."""


class PermanentChannelSendError(Exception):
    """A send that must not be retried (e.g. the source vault document is gone)."""


def stable_channel_int(value: str) -> int:
    if value.isdigit():
        return int(value)
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:15]
    return int(digest, 16)


def normalize_channel_name(channel: str) -> str:
    normalized = str(channel or "").strip().lower()
    if normalized == "dm":
        return "telegram_dm"
    return normalized


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    receive_events: bool = True
    list_conversations: bool = True
    send_message: bool = True
    edit_message: bool = False
    send_reaction: bool = False
    send_media: bool = False
    mark_read: bool = False
    fetch_history: bool = True
    fetch_media_blob: bool = False
    fetch_media_stream: bool = False
    fetch_custom_emoji: bool = False
    delivery_status: bool = False
    typing_indicator: bool = False


@dataclass(frozen=True, slots=True)
class ChannelDeliveryStatus:
    status: Literal["accepted", "sent", "delivered", "failed", "unsupported"]
    external_message_id: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelSendResult:
    external_message_id: str
    status: ChannelDeliveryStatus


@dataclass(frozen=True, slots=True)
class ChannelMediaRef:
    channel: str
    conversation_id: str
    message_id: str
    media_id: str | None = None
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelOutboundMedia:
    url: str
    media_type: str = "photo"
    mime_type: str | None = None
    file_name: str | None = None
    asset_id: str | None = None
    vault_peer: str | None = None
    vault_message_id: int | None = None
    caption: str | None = None

    def to_sidecar_payload(self) -> dict[str, object]:
        if self.vault_message_id is not None and self.vault_peer:
            payload: dict[str, object] = {
                "document": {
                    "vaultPeer": self.vault_peer,
                    "vaultMessageId": int(self.vault_message_id),
                },
                "mediaType": self.media_type.strip().lower() or "video",
            }
            if self.mime_type:
                payload["mimeType"] = self.mime_type
            if self.file_name:
                payload["fileName"] = self.file_name
            if self.asset_id:
                payload["assetId"] = self.asset_id
            return payload
        url = self.url.strip()
        if not url.startswith(("https://", "http://")):
            raise UnsupportedChannelCapability(
                "outbound media url must be an http(s) URL"
            )
        payload = {
            "url": url,
            "mediaType": self.media_type.strip().lower() or "photo",
        }
        if self.mime_type:
            payload["mimeType"] = self.mime_type
        if self.file_name:
            payload["fileName"] = self.file_name
        if self.asset_id:
            payload["assetId"] = self.asset_id
        return payload


@dataclass(frozen=True, slots=True)
class ChannelMediaBlob:
    data: bytes
    mime_type: str


@dataclass(frozen=True, slots=True)
class ChannelMediaStream:
    media_type: str
    stream: AsyncIterator[bytes]
    status_code: int = 200
    content_length: int | None = None
    content_range: str | None = None
    accept_ranges: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelConversationSummary:
    external_chat_id: str
    title: str
    unread_count: int = 0
    top_message_id: int | None = None
    last_message_text: str | None = None
    last_message_date: float | None = None
    last_message_is_outgoing: bool = False


@dataclass(frozen=True, slots=True)
class ChannelInboundMessage:
    workspace_id: int
    channel: str
    account_id: str
    conversation_id: str
    message_id: str
    sender_id: str
    sender_name: str
    text: str | None
    sent_at: float
    is_outgoing: bool = False
    media_type: str | None = None
    media_metadata: dict[str, Any] | None = None
    text_entities: list[dict[str, Any]] | None = None
    reply_to_message_id: str | None = None

    def to_bridge_payload(self) -> dict[str, Any]:
        return {
            "sellerUserId": self.account_id,
            "chatId": self.conversation_id,
            "senderId": self.sender_id,
            "senderName": self.sender_name,
            "messageId": self.message_id,
            "text": self.text or "",
            "date": self.sent_at,
            "isOutgoing": self.is_outgoing,
            "mediaType": self.media_type,
            "mediaMetadata": self.media_metadata,
            "textEntities": self.text_entities,
            "replyToMsgId": self.reply_to_message_id,
        }

    def to_event(self, *, correlation_id: str | None = None) -> MsgInbound:
        chat_id = stable_channel_int(self.conversation_id)
        message_id = stable_channel_int(self.message_id)
        sender_id = stable_channel_int(self.sender_id)
        return MsgInbound(
            workspace_id=self.workspace_id,
            correlation_id=correlation_id,
            channel=self.channel,
            channel_account_id=self.account_id,
            channel_conversation_id=self.conversation_id,
            channel_message_id=self.message_id,
            channel_sender_id=self.sender_id,
            sender_name=self.sender_name,
            idempotency_key=f"{self.channel}:{self.conversation_id}:{self.message_id}",
            telegram_chat_id=chat_id,
            telegram_message_id=message_id,
            sender_telegram_id=sender_id,
            is_outgoing=self.is_outgoing,
            text=self.text or None,
            media_type=self.media_type,
            media_metadata=self.media_metadata,
            text_entities=self.text_entities,
            reply_to_msg_id=(
                stable_channel_int(self.reply_to_message_id)
                if self.reply_to_message_id
                else None
            ),
            sent_at=self.sent_at,
            emitted_at=time.time(),
        )


class ChannelAdapter(Protocol):
    channel: str

    def capabilities(self) -> ChannelCapabilities: ...

    async def receive_events(self, payload: dict[str, Any]) -> list[ChannelInboundMessage]: ...

    async def list_conversations(
        self,
        *,
        workspace_id: int,
        limit: int | None = None,
    ) -> list[ChannelConversationSummary]: ...

    async def send_message(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        text: str,
        idempotency_key: str,
        reply_to_message_id: int | None = None,
    ) -> ChannelSendResult: ...

    async def edit_message(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
        text: str,
        idempotency_key: str,
    ) -> ChannelSendResult: ...

    async def send_reaction(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
        reaction: str,
        idempotency_key: str,
    ) -> ChannelSendResult: ...

    async def send_media(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        media: ChannelMediaRef | ChannelOutboundMedia,
        caption: str | None = None,
        idempotency_key: str,
        reply_to_message_id: int | None = None,
    ) -> ChannelSendResult: ...

    async def mark_read(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
    ) -> None: ...

    async def fetch_history(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        before_message_id: str | None = None,
        after_message_id: str | None = None,
        limit: int = 50,
    ) -> list[ChannelInboundMessage]: ...

    async def fetch_media_blob(
        self,
        *,
        workspace_id: int,
        media: ChannelMediaRef,
        thumb: bool = False,
    ) -> ChannelMediaBlob: ...

    async def open_media_stream(
        self,
        *,
        workspace_id: int,
        media: ChannelMediaRef,
        thumb: bool = False,
        byte_range: str | None = None,
    ) -> ChannelMediaStream: ...

    async def fetch_custom_emoji_preview(
        self,
        *,
        workspace_id: int,
        document_id: str,
    ) -> ChannelMediaBlob: ...

    async def delivery_status(
        self,
        *,
        workspace_id: int,
        external_message_id: str,
    ) -> ChannelDeliveryStatus: ...


@dataclass(slots=True)
class TelegramChannelAdapter:
    channel: str = "telegram_dm"
    sidecar_url: str | None = None
    sidecar_api_key: str | None = None
    http_client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient

    def _sidecar_config(self) -> tuple[str, dict[str, str]]:
        settings = get_settings()
        sidecar_url = (self.sidecar_url or settings.sidecar_url).rstrip("/")
        sidecar_key = self.sidecar_api_key
        if sidecar_key is None:
            sidecar_key = settings.sidecar_api_key
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if sidecar_key:
            headers["X-Sidecar-Key"] = sidecar_key
        return sidecar_url, headers

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            send_message=True,
            edit_message=True,
            send_reaction=True,
            send_media=True,
            mark_read=True,
            fetch_history=True,
            fetch_media_blob=True,
            fetch_media_stream=True,
            fetch_custom_emoji=True,
            delivery_status=False,
            typing_indicator=True,
        )

    async def receive_events(self, payload: dict[str, Any]) -> list[ChannelInboundMessage]:
        return [
            ChannelInboundMessage(
                workspace_id=int(payload["workspaceId"]),
                channel=self.channel,
                account_id=str(payload.get("sellerUserId") or ""),
                conversation_id=str(payload["chatId"]),
                message_id=str(payload["messageId"]),
                sender_id=str(payload["senderId"]),
                sender_name=str(payload.get("senderName") or ""),
                text=payload.get("text") or None,
                sent_at=float(payload["date"]),
                is_outgoing=bool(payload.get("isOutgoing", False)),
                media_type=payload.get("mediaType"),
                media_metadata=payload.get("mediaMetadata")
                if isinstance(payload.get("mediaMetadata"), dict)
                else None,
                text_entities=payload.get("textEntities")
                if isinstance(payload.get("textEntities"), list)
                else None,
                reply_to_message_id=(
                    str(payload.get("replyToMsgId"))
                    if payload.get("replyToMsgId") is not None
                    else None
                ),
            )
        ]

    async def list_conversations(
        self,
        *,
        workspace_id: int,
        limit: int | None = None,
    ) -> list[ChannelConversationSummary]:
        params: dict[str, str | int] = {"workspaceId": workspace_id}
        if limit is not None:
            params["limit"] = limit

        sidecar_url, headers = self._sidecar_config()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{sidecar_url}/dialogs",
                    params=params,
                    headers=headers,
                )
                if response.status_code == 429:
                    raise ChannelSyncRateLimitError(
                        retry_after_seconds=extract_retry_after_seconds(response),
                        channel=self.channel,
                        operation="dialogs",
                    )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ChannelHistorySourceUnavailable(
                "telegram dialog source unavailable"
            ) from exc

        if not isinstance(payload, list):
            return []

        conversations: list[ChannelConversationSummary] = []
        for row in payload:
            chat_id = str(row.get("chatId", "")).strip()
            if not chat_id:
                continue
            conversations.append(
                ChannelConversationSummary(
                    external_chat_id=chat_id,
                    title=row.get("title") or chat_id,
                    unread_count=_safe_int(row.get("unreadCount")) or 0,
                    top_message_id=_safe_int(row.get("topMessageId")),
                    last_message_text=row.get("lastMessageText"),
                    last_message_date=_safe_float(row.get("lastMessageDate")),
                    last_message_is_outgoing=bool(row.get("lastMessageIsOutgoing", False)),
                )
            )
        return conversations

    async def send_message(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        text: str,
        idempotency_key: str,
        reply_to_message_id: int | None = None,
    ) -> ChannelSendResult:
        sidecar_url, headers = self._sidecar_config()
        payload = {
            "workspaceId": workspace_id,
            "chatId": conversation_id,
            "text": text,
            "idempotencyKey": idempotency_key,
        }
        if reply_to_message_id is not None:
            payload["replyToMsgId"] = int(reply_to_message_id)
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{sidecar_url}/send",
                json=payload,
                headers=headers,
            )
            if response.status_code == 429:
                raise ChannelSyncRateLimitError(
                    retry_after_seconds=extract_retry_after_seconds(response),
                    channel=self.channel,
                    operation="send",
                )
            response.raise_for_status()
            payload = response.json()
            external_message_id = str(payload.get("externalMessageId") or "")
            return ChannelSendResult(
                external_message_id=external_message_id,
                status=ChannelDeliveryStatus(
                    status="sent",
                    external_message_id=external_message_id,
                ),
            )

    async def send_media(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        media: ChannelMediaRef | ChannelOutboundMedia,
        caption: str | None = None,
        idempotency_key: str,
        reply_to_message_id: int | None = None,
    ) -> ChannelSendResult:
        if not isinstance(media, ChannelOutboundMedia):
            raise UnsupportedChannelCapability(
                "telegram send_media requires an outbound media payload"
            )
        sidecar_url, headers = self._sidecar_config()
        payload = {
            "workspaceId": workspace_id,
            "chatId": conversation_id,
            "caption": caption,
            "media": media.to_sidecar_payload(),
            "idempotencyKey": idempotency_key,
        }
        if reply_to_message_id is not None:
            payload["replyToMsgId"] = int(reply_to_message_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{sidecar_url}/send",
                json=payload,
                headers=headers,
            )
            if response.status_code == 429:
                raise ChannelSyncRateLimitError(
                    retry_after_seconds=extract_retry_after_seconds(response),
                    channel=self.channel,
                    operation="send",
                )
            if response.status_code == 422:
                detail = ""
                try:
                    detail = str(response.json().get("error") or "")
                except Exception:
                    detail = ""
                raise PermanentChannelSendError(detail or "permanent_send_failure")
            response.raise_for_status()
            payload = response.json()
            external_message_id = str(payload.get("externalMessageId") or "")
            return ChannelSendResult(
                external_message_id=external_message_id,
                status=ChannelDeliveryStatus(
                    status="sent",
                    external_message_id=external_message_id,
                ),
            )

    async def edit_message(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
        text: str,
        idempotency_key: str,
    ) -> ChannelSendResult:
        sidecar_url, headers = self._sidecar_config()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{sidecar_url}/edit",
                json={
                    "workspaceId": workspace_id,
                    "chatId": conversation_id,
                    "messageId": message_id,
                    "text": text,
                    "idempotencyKey": idempotency_key,
                },
                headers=headers,
            )
            if response.status_code == 429:
                raise ChannelSyncRateLimitError(
                    retry_after_seconds=extract_retry_after_seconds(response),
                    channel=self.channel,
                    operation="edit",
                )
            response.raise_for_status()
            payload = response.json()
            external_message_id = str(payload.get("externalMessageId") or message_id)
            return ChannelSendResult(
                external_message_id=external_message_id,
                status=ChannelDeliveryStatus(
                    status="sent",
                    external_message_id=external_message_id,
                ),
            )

    async def send_reaction(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
        reaction: str,
        idempotency_key: str,
    ) -> ChannelSendResult:
        sidecar_url, headers = self._sidecar_config()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{sidecar_url}/react",
                json={
                    "workspaceId": workspace_id,
                    "chatId": conversation_id,
                    "messageId": message_id,
                    "reaction": reaction,
                    "idempotencyKey": idempotency_key,
                },
                headers=headers,
            )
            if response.status_code == 429:
                raise ChannelSyncRateLimitError(
                    retry_after_seconds=extract_retry_after_seconds(response),
                    channel=self.channel,
                    operation="react",
                )
            response.raise_for_status()
            payload = response.json()
            external_message_id = str(payload.get("externalMessageId") or message_id)
            return ChannelSendResult(
                external_message_id=external_message_id,
                status=ChannelDeliveryStatus(
                    status="sent",
                    external_message_id=external_message_id,
                ),
            )

    async def mark_read(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
    ) -> None:
        sidecar_url, headers = self._sidecar_config()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{sidecar_url}/read",
                json={
                    "workspaceId": workspace_id,
                    "chatId": conversation_id,
                    "maxId": stable_channel_int(message_id),
                },
                headers=headers,
            )
            response.raise_for_status()

    async def fetch_history(self, **kwargs: Any) -> list[ChannelInboundMessage]:
        workspace_id = int(kwargs["workspace_id"])
        conversation_id = str(kwargs["conversation_id"])
        before_message_id = kwargs.get("before_message_id")
        after_message_id = kwargs.get("after_message_id")
        limit = int(kwargs.get("limit") or 50)

        params: dict[str, str | int] = {
            "workspaceId": workspace_id,
            "chatId": conversation_id,
            "limit": limit,
        }
        if before_message_id:
            params["beforeId"] = str(before_message_id)
        if after_message_id:
            params["afterId"] = str(after_message_id)

        sidecar_url, headers = self._sidecar_config()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{sidecar_url}/messages",
                    params=params,
                    headers=headers,
                )
                if response.status_code == 429:
                    raise ChannelSyncRateLimitError(
                        retry_after_seconds=extract_retry_after_seconds(response),
                        channel=self.channel,
                        operation="messages",
                    )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ChannelHistorySourceUnavailable(
                "telegram history source unavailable"
            ) from exc

        if not isinstance(payload, list):
            return []

        messages: list[ChannelInboundMessage] = []
        for row in payload:
            message_id = row.get("messageId")
            sender_id = row.get("senderId")
            if message_id is None or sender_id is None:
                continue
            media_metadata = (
                row.get("mediaMetadata") if isinstance(row.get("mediaMetadata"), dict) else None
            )
            adapter_media_metadata = (
                {
                    **(media_metadata or {}),
                    **(
                        {"grouped_id": row.get("groupedId")}
                        if row.get("groupedId") is not None
                        else {}
                    ),
                }
                if media_metadata is not None or row.get("groupedId") is not None
                else None
            )
            messages.append(
                ChannelInboundMessage(
                    workspace_id=workspace_id,
                    channel=self.channel,
                    account_id=str(row.get("sellerUserId") or ""),
                    conversation_id=conversation_id,
                    message_id=str(message_id),
                    sender_id=str(sender_id),
                    sender_name=str(row.get("senderName") or ""),
                    text=row.get("text") or "",
                    sent_at=float(row.get("date") or time.time()),
                    is_outgoing=bool(row.get("isOutgoing", False)),
                    media_type=normalize_media_type(row.get("mediaType"), media_metadata),
                    media_metadata=adapter_media_metadata,
                    text_entities=_normalize_text_entities(row.get("textEntities")),
                    reply_to_message_id=(
                        str(row["replyToMsgId"]) if row.get("replyToMsgId") is not None else None
                    ),
                )
            )
        return messages

    async def fetch_media_blob(
        self,
        *,
        workspace_id: int,
        media: ChannelMediaRef,
        thumb: bool = False,
    ) -> ChannelMediaBlob:
        sidecar_url, headers = self._sidecar_config()
        try:
            async with self.http_client_factory(timeout=30.0) as client:
                response = await client.post(
                    f"{sidecar_url}/download-media",
                    json={
                        "workspaceId": workspace_id,
                        "chatId": media.conversation_id,
                        "messageId": media.message_id,
                        "thumb": thumb,
                    },
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise ChannelMediaSourceUnavailable("telegram media source unavailable") from exc

        if response.status_code == 429:
            raise ChannelSyncRateLimitError(
                retry_after_seconds=extract_retry_after_seconds(response),
                channel=self.channel,
                operation="media",
            )
        if response.status_code != 200 or not response.content:
            raise ChannelMediaUnavailable("telegram media unavailable")
        return ChannelMediaBlob(
            data=response.content,
            mime_type=response.headers.get("content-type", "application/octet-stream"),
        )

    async def open_media_stream(
        self,
        *,
        workspace_id: int,
        media: ChannelMediaRef,
        thumb: bool = False,
        byte_range: str | None = None,
    ) -> ChannelMediaStream:
        sidecar_url, headers = self._sidecar_config()
        client = self.http_client_factory(timeout=30.0)
        try:
            payload: dict[str, str | int | bool] = {
                "workspaceId": workspace_id,
                "chatId": media.conversation_id,
                "messageId": media.message_id,
                "thumb": thumb,
            }
            if byte_range:
                payload["byteRange"] = byte_range
            request = client.build_request(
                "POST",
                f"{sidecar_url}/download-media",
                json=payload,
                headers=headers,
            )
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            raise ChannelMediaSourceUnavailable("telegram media source unavailable") from exc

        if response.status_code == 416:
            await response.aclose()
            await client.aclose()
            raise ChannelMediaRangeNotSatisfiable("telegram media range unavailable")
        if response.status_code == 429:
            retry_after = extract_retry_after_seconds(response)
            await response.aclose()
            await client.aclose()
            raise ChannelSyncRateLimitError(
                retry_after_seconds=retry_after,
                channel=self.channel,
                operation="media",
            )
        if response.status_code not in {200, 206}:
            await response.aclose()
            await client.aclose()
            raise ChannelMediaUnavailable("telegram media unavailable")

        iterator = response.aiter_bytes()
        try:
            first_chunk = await anext(iterator)
        except StopAsyncIteration as exc:
            await response.aclose()
            await client.aclose()
            raise ChannelMediaUnavailable("telegram media unavailable") from exc

        async def _stream() -> AsyncIterator[bytes]:
            try:
                yield first_chunk
                async for chunk in iterator:
                    if chunk:
                        yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return ChannelMediaStream(
            media_type=response.headers.get("content-type", "application/octet-stream"),
            status_code=response.status_code,
            content_length=(
                int(response.headers["content-length"])
                if response.headers.get("content-length")
                else None
            ),
            content_range=response.headers.get("content-range"),
            accept_ranges=response.headers.get("accept-ranges"),
            stream=_stream(),
        )

    async def fetch_custom_emoji_preview(
        self,
        *,
        workspace_id: int,
        document_id: str,
    ) -> ChannelMediaBlob:
        sidecar_url, headers = self._sidecar_config()
        try:
            async with self.http_client_factory(timeout=30.0) as client:
                response = await client.get(
                    f"{sidecar_url}/custom-emoji",
                    params={
                        "workspaceId": workspace_id,
                        "documentId": document_id,
                    },
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise ChannelMediaSourceUnavailable("telegram custom emoji source unavailable") from exc
        if response.status_code != 200 or not response.content:
            raise ChannelMediaUnavailable("telegram custom emoji unavailable")
        return ChannelMediaBlob(
            data=response.content,
            mime_type=response.headers.get("content-type", "application/octet-stream"),
        )

    async def delivery_status(self, **kwargs: Any) -> ChannelDeliveryStatus:
        raise UnsupportedChannelCapability("telegram delivery status is provided by DeliveryService")


@dataclass(slots=True)
class MockInstagramAdapter:
    account_id: str
    _history: dict[str, list[ChannelInboundMessage]] = field(default_factory=dict)
    _sent: dict[str, ChannelDeliveryStatus] = field(default_factory=dict)
    _read_message_ids: dict[str, set[str]] = field(default_factory=dict)
    channel: str = "instagram_dm"

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            send_media=False,
            mark_read=True,
            fetch_media_stream=False,
            delivery_status=True,
            typing_indicator=False,
        )

    def seed_inbound(self, message: ChannelInboundMessage) -> None:
        self._history.setdefault(message.conversation_id, []).append(message)

    async def list_conversations(
        self,
        *,
        workspace_id: int,
        limit: int | None = None,
    ) -> list[ChannelConversationSummary]:
        conversations: list[ChannelConversationSummary] = []
        for conversation_id, messages in self._history.items():
            last_message = messages[-1] if messages else None
            read_message_ids = self._read_message_ids.get(conversation_id, set())
            conversations.append(
                ChannelConversationSummary(
                    external_chat_id=conversation_id,
                    title=conversation_id,
                    unread_count=sum(
                        1
                        for message in messages
                        if not message.is_outgoing and message.message_id not in read_message_ids
                    ),
                    top_message_id=(
                        stable_channel_int(last_message.message_id)
                        if last_message is not None
                        else None
                    ),
                    last_message_text=last_message.text if last_message is not None else None,
                    last_message_date=last_message.sent_at if last_message is not None else None,
                    last_message_is_outgoing=(
                        last_message.is_outgoing if last_message is not None else False
                    ),
                )
            )
        return conversations[:limit] if limit is not None else conversations

    async def receive_events(self, payload: dict[str, Any]) -> list[ChannelInboundMessage]:
        message = ChannelInboundMessage(
            workspace_id=int(payload["workspaceId"]),
            channel=self.channel,
            account_id=self.account_id,
            conversation_id=str(payload["conversationId"]),
            message_id=str(payload["messageId"]),
            sender_id=str(payload["senderId"]),
            sender_name=str(payload.get("senderName") or ""),
            text=payload.get("text") or None,
            sent_at=float(payload["date"]),
            is_outgoing=bool(payload.get("isOutgoing", False)),
            media_type=payload.get("mediaType"),
            media_metadata=payload.get("mediaMetadata")
            if isinstance(payload.get("mediaMetadata"), dict)
            else None,
        )
        self.seed_inbound(message)
        return [message]

    async def send_message(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        text: str,
        idempotency_key: str,
        reply_to_message_id: int | None = None,
    ) -> ChannelSendResult:
        external_message_id = f"ig:{conversation_id}:{len(self._sent) + 1}"
        status = ChannelDeliveryStatus(
            status="sent",
            external_message_id=external_message_id,
        )
        self._sent[idempotency_key] = status
        return ChannelSendResult(
            external_message_id=external_message_id,
            status=status,
        )

    async def send_media(self, **kwargs: Any) -> ChannelSendResult:
        raise UnsupportedChannelCapability("mock Instagram adapter does not support media send")

    async def send_reaction(self, **kwargs: Any) -> ChannelSendResult:
        raise UnsupportedChannelCapability("mock Instagram adapter does not support reactions")

    async def mark_read(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        message_id: str,
    ) -> None:
        read_message_ids = self._read_message_ids.setdefault(conversation_id, set())
        for message in self._history.get(conversation_id, []):
            read_message_ids.add(message.message_id)
            if message.message_id == message_id:
                break

    async def fetch_history(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        before_message_id: str | None = None,
        after_message_id: str | None = None,
        limit: int = 50,
    ) -> list[ChannelInboundMessage]:
        messages = list(self._history.get(conversation_id, []))
        if before_message_id is not None:
            messages = [msg for msg in messages if msg.message_id < before_message_id]
        if after_message_id is not None:
            messages = [msg for msg in messages if msg.message_id > after_message_id]
        return messages[-limit:]

    async def fetch_media_blob(self, **kwargs: Any) -> ChannelMediaBlob:
        raise UnsupportedChannelCapability("mock Instagram adapter does not support media fetch")

    async def open_media_stream(self, **kwargs: Any) -> ChannelMediaStream:
        raise UnsupportedChannelCapability("mock Instagram adapter does not support media streaming")

    async def fetch_custom_emoji_preview(self, **kwargs: Any) -> ChannelMediaBlob:
        raise UnsupportedChannelCapability("mock Instagram adapter does not support custom emoji")

    async def delivery_status(
        self,
        *,
        workspace_id: int,
        external_message_id: str,
    ) -> ChannelDeliveryStatus:
        for status in self._sent.values():
            if status.external_message_id == external_message_id:
                return status
        return ChannelDeliveryStatus(status="failed", error="unknown_message")


def get_channel_adapter(
    channel: str,
    *,
    account_id: str | None = None,
    access_token: str | None = None,
) -> ChannelAdapter:
    normalized = normalize_channel_name(channel)
    if normalized == "telegram_dm":
        return TelegramChannelAdapter()
    if normalized == "instagram_dm":
        from app.services.instagram_channel_adapter import InstagramChannelAdapter

        return InstagramChannelAdapter(
            account_id=account_id or "",
            access_token=access_token,
        )
    raise UnsupportedChannelCapability(f"unsupported channel adapter: {normalized}")


def _safe_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_text_entities(raw_entities: object) -> list[dict] | None:
    from app.modules.message_intake.normalizer import normalize_text_entities

    return normalize_text_entities(raw_entities)
