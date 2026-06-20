from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(slots=True)
class ChannelConversationRef:
    channel: str
    external_chat_id: str


@dataclass(slots=True)
class ChannelConversationShell:
    external_chat_id: str
    title: str
    unread_count: int = 0
    top_message_id: int | None = None
    last_message_text: str | None = None
    last_message_date: datetime | None = None
    last_message_is_outgoing: bool = False


@dataclass(slots=True)
class ChannelMessageRecord:
    external_message_id: str
    sender_external_id: str
    text: str
    sent_at: datetime
    is_outgoing: bool
    media_type: str | None = None
    media_metadata: dict | None = None
    text_entities: list[dict] | None = None
    reply_to_external_message_id: str | None = None
    grouped_id: int | None = None
    edited_at: datetime | None = None
    edit_version: str | None = None
    supersedes_external_message_id: str | None = None


@dataclass(slots=True)
class MediaBlob:
    data: bytes
    mime_type: str


class ChannelSourcePort(Protocol):
    async def list_conversations(
        self,
        *,
        workspace_id: int,
        channel: str,
        limit: int | None = None,
    ) -> list[ChannelConversationShell]: ...

    async def fetch_messages(
        self,
        *,
        workspace_id: int,
        conversation: ChannelConversationRef,
        limit: int,
        after_external_message_id: str | None = None,
        before_external_message_id: str | None = None,
    ) -> list[ChannelMessageRecord]: ...

    async def fetch_media(
        self,
        *,
        workspace_id: int,
        conversation: ChannelConversationRef,
        external_message_id: str,
    ) -> MediaBlob | None: ...
