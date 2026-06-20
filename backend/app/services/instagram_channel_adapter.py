"""Instagram Graph API channel adapter (Instagram Login path).

Stateless REST adapter: Meta pushes webhooks to /api/webhook/instagram;
sends go to graph.instagram.com with the workspace's long-lived token.
No sidecar — unlike Telegram, Meta owns the connection.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.channel_adapter_contract import (
    ChannelCapabilities,
    ChannelConversationSummary,
    ChannelDeliveryStatus,
    ChannelInboundMessage,
    ChannelMediaBlob,
    ChannelMediaRef,
    ChannelMediaStream,
    ChannelOutboundMedia,
    ChannelSendResult,
    UnsupportedChannelCapability,
)
from app.services.channel_sidecar_client import extract_retry_after_seconds
from app.services.channel_sync_runtime import ChannelSyncRateLimitError

logger = get_logger("services.instagram_channel_adapter")

GRAPH_VERSION = "v23.0"

# Meta attachment type -> our normalized media_type vocabulary.
_ATTACHMENT_MEDIA_TYPE = {
    "image": "photo",
    "video": "video",
    "audio": "voice",
    "file": "document",
    "story_mention": "photo",
    "share": "photo",
}


@dataclass(slots=True)
class InstagramChannelAdapter:
    account_id: str = ""
    access_token: str | None = None
    channel: str = "instagram_dm"
    http_client_factory: Callable[..., Any] = httpx.AsyncClient

    def _messages_url(self) -> str:
        base = get_settings().instagram_graph_base.rstrip("/")
        return f"{base}/{GRAPH_VERSION}/me/messages"

    def _require_token(self) -> str:
        if not self.access_token:
            raise UnsupportedChannelCapability(
                "instagram adapter requires a workspace access token for sends"
            )
        return self.access_token

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            receive_events=True,
            list_conversations=False,
            send_message=True,
            edit_message=False,
            send_reaction=False,
            send_media=True,
            mark_read=False,
            fetch_history=False,
            fetch_media_blob=False,
            fetch_media_stream=False,
            fetch_custom_emoji=False,
            delivery_status=False,
            typing_indicator=False,
        )

    async def receive_events(self, payload: dict[str, Any]) -> list[ChannelInboundMessage]:
        """Parse one webhook `entry` (wrapped with workspaceId by the route)."""
        workspace_id = int(payload["workspaceId"])
        entry = payload.get("entry") or {}
        account_id = str(entry.get("id") or self.account_id or "")
        messages: list[ChannelInboundMessage] = []
        for event in entry.get("messaging") or []:
            message = event.get("message")
            if not isinstance(message, dict):
                continue  # read receipts / postbacks — not messages
            mid = str(message.get("mid") or "")
            if not mid:
                continue
            is_echo = bool(message.get("is_echo", False))
            sender_id = str((event.get("sender") or {}).get("id") or "")
            recipient_id = str((event.get("recipient") or {}).get("id") or "")
            counterpart = recipient_id if is_echo else sender_id
            if not counterpart:
                continue
            media_type, media_metadata = _first_attachment(message)
            reply_to = message.get("reply_to") if isinstance(message.get("reply_to"), dict) else {}
            story = reply_to.get("story") if isinstance(reply_to.get("story"), dict) else None
            if story:
                media_metadata = {
                    **(media_metadata or {}),
                    "instagram_story_reply": {
                        "id": str(story.get("id") or ""),
                        "url": str(story.get("url") or ""),
                    },
                }
            timestamp_ms = float(event.get("timestamp") or 0)
            messages.append(
                ChannelInboundMessage(
                    workspace_id=workspace_id,
                    channel=self.channel,
                    account_id=account_id,
                    conversation_id=counterpart,
                    message_id=mid,
                    sender_id=sender_id,
                    sender_name="",
                    text=message.get("text") or None,
                    sent_at=timestamp_ms / 1000.0,
                    is_outgoing=is_echo,
                    media_type=media_type,
                    media_metadata=media_metadata,
                    reply_to_message_id=(
                        str(reply_to.get("mid")) if reply_to.get("mid") else None
                    ),
                )
            )
        return messages

    async def send_message(
        self,
        *,
        workspace_id: int,
        conversation_id: str,
        text: str,
        idempotency_key: str,
        reply_to_message_id: int | None = None,
    ) -> ChannelSendResult:
        return await self._post_message(
            recipient={"id": conversation_id},
            message={"text": text},
        )

    async def send_private_reply(
        self,
        *,
        workspace_id: int,
        comment_id: str,
        text: str,
        idempotency_key: str,
    ) -> ChannelSendResult:
        """Instagram private reply: the ONLY sanctioned business-initiated DM
        (one per comment, ~7-day window)."""
        return await self._post_message(
            recipient={"comment_id": comment_id},
            message={"text": text},
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
                "instagram send_media requires an outbound media payload"
            )
        attachment_type = {"photo": "image", "video": "video", "voice": "audio"}.get(
            media.media_type, "image"
        )
        result = await self._post_message(
            recipient={"id": conversation_id},
            message={
                "attachment": {
                    "type": attachment_type,
                    "payload": {"url": media.url},
                }
            },
        )
        if caption:
            # Instagram has no caption on attachment sends — follow with text.
            # Caption failure must NOT raise: the media already reached the
            # customer, and a caller retry would re-send it (no idempotency
            # on the Graph API). Degrade to a logged warning instead.
            try:
                await self._post_message(
                    recipient={"id": conversation_id},
                    message={"text": caption},
                )
            except Exception:
                logger.warning(
                    "instagram caption follow-up failed for conversation=%s; "
                    "media delivered without caption",
                    conversation_id,
                    exc_info=True,
                )
        return result

    async def _post_message(self, *, recipient: dict, message: dict) -> ChannelSendResult:
        token = self._require_token()
        async with self.http_client_factory(timeout=15.0) as client:
            response = await client.post(
                self._messages_url(),
                json={"recipient": recipient, "message": message},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 429:
                raise ChannelSyncRateLimitError(
                    retry_after_seconds=extract_retry_after_seconds(response),
                    channel=self.channel,
                    operation="send",
                )
            response.raise_for_status()
            data = response.json()
            external_message_id = str(data.get("message_id") or "")
            return ChannelSendResult(
                external_message_id=external_message_id,
                status=ChannelDeliveryStatus(
                    status="sent",
                    external_message_id=external_message_id,
                ),
            )

    # --- capabilities declared False: explicit unsupported -----------------

    async def list_conversations(self, **kwargs: Any) -> list[ChannelConversationSummary]:
        raise UnsupportedChannelCapability("instagram adapter does not list conversations")

    async def edit_message(self, **kwargs: Any) -> ChannelSendResult:
        raise UnsupportedChannelCapability("instagram adapter does not edit messages")

    async def send_reaction(self, **kwargs: Any) -> ChannelSendResult:
        raise UnsupportedChannelCapability("instagram adapter does not send reactions")

    async def mark_read(self, **kwargs: Any) -> None:
        raise UnsupportedChannelCapability("instagram adapter does not support mark_read")

    async def fetch_history(self, **kwargs: Any) -> list[ChannelInboundMessage]:
        raise UnsupportedChannelCapability("instagram adapter does not fetch history")

    async def fetch_media_blob(self, **kwargs: Any) -> ChannelMediaBlob:
        raise UnsupportedChannelCapability("instagram adapter does not fetch media blobs")

    async def open_media_stream(self, **kwargs: Any) -> ChannelMediaStream:
        raise UnsupportedChannelCapability("instagram adapter does not stream media")

    async def fetch_custom_emoji_preview(self, **kwargs: Any) -> ChannelMediaBlob:
        raise UnsupportedChannelCapability("instagram adapter has no custom emoji")

    async def delivery_status(self, **kwargs: Any) -> ChannelDeliveryStatus:
        raise UnsupportedChannelCapability(
            "instagram delivery status is provided by DeliveryService"
        )


def _first_attachment(message: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    attachments = message.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return None, None
    first = attachments[0] if isinstance(attachments[0], dict) else {}
    attachment_type = str(first.get("type") or "")
    url = str((first.get("payload") or {}).get("url") or "")
    media_type = _ATTACHMENT_MEDIA_TYPE.get(attachment_type)
    if media_type is None:
        return None, None
    return media_type, {"instagram_attachment_type": attachment_type, "url": url}
