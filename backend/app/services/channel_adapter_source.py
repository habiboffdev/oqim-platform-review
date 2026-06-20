"""ChannelSourcePort implementation backed by ChannelAdapter capabilities."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.channel_adapter_contract import (
    ChannelAdapter,
    ChannelMediaRef,
    UnsupportedChannelCapability,
    get_channel_adapter,
)
from app.services.channel_sync_models import (
    ChannelConversationRef,
    ChannelConversationShell,
    ChannelMessageRecord,
    MediaBlob,
)


class AdapterBackedChannelSource:
    """Bridge existing sync persistence to the channel adapter contract."""

    def __init__(
        self,
        *,
        channel: str,
        adapter: ChannelAdapter | None = None,
    ) -> None:
        self._channel = channel
        self._adapter = adapter or get_channel_adapter(channel)

    async def list_conversations(
        self,
        *,
        workspace_id: int,
        channel: str,
        limit: int | None = None,
    ) -> list[ChannelConversationShell]:
        if channel != self._channel:
            return []
        if not self._adapter.capabilities().list_conversations:
            raise UnsupportedChannelCapability(f"{channel} does not support dialog fetch")
        conversations = await self._adapter.list_conversations(
            workspace_id=workspace_id,
            limit=limit,
        )
        return [
            ChannelConversationShell(
                external_chat_id=conversation.external_chat_id,
                title=conversation.title,
                unread_count=conversation.unread_count,
                top_message_id=conversation.top_message_id,
                last_message_text=conversation.last_message_text,
                last_message_date=(
                    _event_time_to_datetime(conversation.last_message_date)
                    if conversation.last_message_date is not None
                    else None
                ),
                last_message_is_outgoing=conversation.last_message_is_outgoing,
            )
            for conversation in conversations
        ]

    async def fetch_messages(
        self,
        *,
        workspace_id: int,
        conversation: ChannelConversationRef,
        limit: int,
        after_external_message_id: str | None = None,
        before_external_message_id: str | None = None,
    ) -> list[ChannelMessageRecord]:
        if not self._adapter.capabilities().fetch_history:
            raise UnsupportedChannelCapability(
                f"{conversation.channel} does not support history fetch"
            )
        messages = await self._adapter.fetch_history(
            workspace_id=workspace_id,
            conversation_id=conversation.external_chat_id,
            limit=limit,
            before_message_id=before_external_message_id,
            after_message_id=after_external_message_id,
        )
        return [
            ChannelMessageRecord(
                external_message_id=message.message_id,
                sender_external_id=message.sender_id,
                text=message.text or "",
                sent_at=_event_time_to_datetime(message.sent_at),
                is_outgoing=message.is_outgoing,
                media_type=message.media_type,
                media_metadata=_strip_adapter_only_media_metadata(message.media_metadata),
                text_entities=message.text_entities,
                reply_to_external_message_id=message.reply_to_message_id,
                grouped_id=_safe_int((message.media_metadata or {}).get("grouped_id")),
            )
            for message in messages
        ]

    async def fetch_media(
        self,
        *,
        workspace_id: int,
        conversation: ChannelConversationRef,
        external_message_id: str,
    ) -> MediaBlob | None:
        if self._adapter.capabilities().fetch_media_blob:
            try:
                blob = await self._adapter.fetch_media_blob(
                    workspace_id=workspace_id,
                    media=ChannelMediaRef(
                        channel=conversation.channel,
                        conversation_id=conversation.external_chat_id,
                        message_id=external_message_id,
                    ),
                    thumb=False,
                )
            except UnsupportedChannelCapability:
                return None
            return MediaBlob(data=blob.data, mime_type=blob.mime_type)
        raise UnsupportedChannelCapability(
            f"{conversation.channel} does not support media fetch"
        )


def _event_time_to_datetime(value: float) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _safe_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _strip_adapter_only_media_metadata(value: dict | None) -> dict | None:
    if not value:
        return value
    cleaned = dict(value)
    cleaned.pop("grouped_id", None)
    return cleaned
