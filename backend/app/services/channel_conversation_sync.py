from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.event_spine import MsgInbound
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.conversation_core.service import (
    allocate_conversation_sequence_block,
    project_message_to_dialog_state,
    upsert_customer_and_conversation,
)
from app.services.channel_media_access import ChannelMediaAccess, MediaHydrationResult
from app.services.channel_history_repair import (
    apply_duplicate_repair,
    duplicate_needs_repair,
)
from app.services.channel_adapter_contract import ChannelHistorySourceUnavailable
from app.services.channel_adapter_source import AdapterBackedChannelSource
from app.services.channel_sync_runtime import (
    ChannelSyncRateLimitError,
    ChannelSyncRuntime,
    get_channel_sync_runtime,
)
from app.services.channel_sync_models import (
    ChannelConversationRef,
    ChannelConversationShell,
    ChannelMessageRecord,
    ChannelSourcePort,
    MediaBlob as MediaBlob,
)
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationSyncState,
    get_customer_conversation_state,
    has_exhausted_older_history,
    set_customer_conversation_state,
)
from app.services.conversation_hydration_runtime import (
    enqueue_conversation_hydration,
    conversation_needs_hydration,
    latest_local_message_for_conversation,
)
from app.services.inbound_pipeline import process_inbound_message
from app.services.media_urls import canonicalize_message_media_url
from app.services.channel_sync_watermarks import (
    ConversationSyncWatermark,
    get_sync_watermark,
    mark_boundary_complete,
    update_sync_watermark,
)

logger = get_logger("services.channel_conversation_sync")


@dataclass(slots=True)
class BootstrapInboxResult:
    synced_count: int


@dataclass(slots=True)
class ConversationSyncResult:
    requested: int
    persisted: int
    duplicates: int


@dataclass(slots=True)
class PrefetchHistoryResult:
    prefetched_conversations: int
    persisted_messages: int
    deferred: bool = False


@dataclass(slots=True)
class QueueStaleDialogHydrationsResult:
    queued_conversations: int


class ChannelConversationSync:
    def __init__(
        self,
        sources: dict[str, ChannelSourcePort] | None = None,
        runtime: ChannelSyncRuntime | None = None,
        event_append: Callable[[MsgInbound], Awaitable[Any]] | None = None,
    ):
        self._sources = sources or {
            "telegram_dm": AdapterBackedChannelSource(channel="telegram_dm"),
            "instagram_dm": AdapterBackedChannelSource(channel="instagram_dm"),
        }
        self._runtime = runtime or get_channel_sync_runtime()
        self._event_append = event_append

    async def bootstrap_inbox(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        channel: str = "telegram_dm",
        visible_limit: int = 50,
    ) -> BootstrapInboxResult:
        source = self._source_for(channel)
        try:
            shells = await self._runtime.run(
                workspace_id=workspace_id,
                channel=channel,
                operation="dialogs",
                func=lambda: source.list_conversations(
                    workspace_id=workspace_id,
                    channel=channel,
                    limit=visible_limit if visible_limit > 0 else None,
                ),
            )
        except ChannelSyncRateLimitError as exc:
            logger.info(
                "Deferring dialog bootstrap for workspace=%d channel=%s retry_after=%.2fs",
                workspace_id,
                channel,
                exc.retry_after_seconds,
            )
            return BootstrapInboxResult(synced_count=0)
        except ChannelHistorySourceUnavailable as exc:
            logger.warning(
                "Deferring dialog bootstrap for workspace=%d channel=%s source_unavailable=%s",
                workspace_id,
                channel,
                exc,
            )
            return BootstrapInboxResult(synced_count=0)
        limited_shells = shells[:visible_limit] if visible_limit > 0 else shells
        synced_count = await self.apply_conversation_shells(
            session=session,
            workspace_id=workspace_id,
            channel=channel,
            shells=limited_shells,
        )
        return BootstrapInboxResult(synced_count=synced_count)

    async def apply_conversation_shells(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        channel: str,
        shells: list[ChannelConversationShell],
    ) -> int:
        self._ensure_supported_channel(channel)
        normalized_channel = self._normalize_channel(channel)

        synced_count = 0
        for shell in shells:
            telegram_chat_id = (
                _safe_int(shell.external_chat_id)
                if normalized_channel == "telegram_dm"
                else None
            )
            if normalized_channel == "telegram_dm" and telegram_chat_id is None:
                continue

            _customer, conversation = await upsert_customer_and_conversation(
                session,
                workspace_id=workspace_id,
                telegram_chat_id=telegram_chat_id,
                external_id=shell.external_chat_id,
                external_chat_id=shell.external_chat_id,
                display_name=shell.title,
                channel=normalized_channel,
            )
            shell_updates_tail = _is_incoming_tail_newer_or_equal(
                current=conversation.last_message_at,
                incoming=shell.last_message_date,
            )
            if shell.last_message_text and shell_updates_tail:
                conversation.summary = shell.last_message_text[:200]
            if shell.last_message_date:
                conversation.last_message_at = _newest_datetime(
                    conversation.last_message_at,
                    shell.last_message_date,
                )
            self._write_dialog_snapshot(conversation=conversation, shell=shell)
            session.add(conversation)
            synced_count += 1

        await session.commit()
        return synced_count

    async def prefetch_recent_history(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        channel: str = "telegram_dm",
        max_conversations: int = 3,
        page_limit: int = 25,
    ) -> PrefetchHistoryResult:
        if max_conversations <= 0 or page_limit <= 0:
            return PrefetchHistoryResult(prefetched_conversations=0, persisted_messages=0)

        self._ensure_supported_channel(channel)
        normalized_channel = self._normalize_channel(channel)
        result = await session.execute(
            select(Conversation)
            .where(
                Conversation.workspace_id == workspace_id,
                Conversation.channel.in_([channel, normalized_channel]),
            )
            .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
            .limit(max(50, max_conversations * 4))
        )
        candidates = list(result.scalars())

        prefetched_conversations = 0
        persisted_messages = 0

        for conversation in candidates:
            if prefetched_conversations >= max_conversations:
                break
            if not (conversation.external_chat_id or conversation.telegram_chat_id is not None):
                continue
            if self.get_sync_watermark(conversation).latest_complete:
                continue

            sync_result = await self.sync_conversation(
                session=session,
                workspace_id=workspace_id,
                conversation=conversation,
                limit=page_limit,
            )
            if (
                sync_result.requested == 0
                and sync_result.persisted == 0
                and sync_result.duplicates == 0
            ):
                return PrefetchHistoryResult(
                    prefetched_conversations=prefetched_conversations,
                    persisted_messages=persisted_messages,
                    deferred=True,
                )

            prefetched_conversations += 1
            persisted_messages += sync_result.persisted

        return PrefetchHistoryResult(
            prefetched_conversations=prefetched_conversations,
            persisted_messages=persisted_messages,
            deferred=False,
        )

    async def queue_stale_dialog_hydrations(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        channel: str = "telegram_dm",
        max_conversations: int = 3,
        request_limit: int = 50,
        reason: str = "dialog_sync_tail",
    ) -> QueueStaleDialogHydrationsResult:
        """Queue bounded history jobs when Telegram dialog state is ahead.

        Dialog sync is allowed to update list projections, but it must not fetch
        message history inline. Enqueueing here moves stale top chats into the
        existing worker/runtime plane so empty shells become observable jobs.
        """
        if max_conversations <= 0:
            return QueueStaleDialogHydrationsResult(queued_conversations=0)

        self._ensure_supported_channel(channel)
        normalized_channel = self._normalize_channel(channel)
        result = await session.execute(
            select(Conversation)
            .where(
                Conversation.workspace_id == workspace_id,
                Conversation.channel.in_([channel, normalized_channel]),
            )
            .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
            .limit(max(50, max_conversations * 4))
        )
        candidates = list(result.scalars())

        queued = 0
        for conversation in candidates:
            if queued >= max_conversations:
                break
            latest_local = await latest_local_message_for_conversation(
                session,
                conversation_id=conversation.id,
            )
            if not conversation_needs_hydration(
                conversation,
                latest_local_message=latest_local,
            ):
                continue
            runtime = await enqueue_conversation_hydration(
                session,
                workspace_id=workspace_id,
                conversation=conversation,
                reason=reason,
                requested_limit=request_limit,
            )
            if runtime is not None:
                queued += 1

        if queued:
            await session.commit()
        return QueueStaleDialogHydrationsResult(queued_conversations=queued)

    async def sync_conversation(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        conversation: Conversation,
        limit: int = 50,
        after_external_message_id: str | None = None,
        before_external_message_id: str | None = None,
    ) -> ConversationSyncResult:
        ref = self._conversation_ref(conversation)
        source = self._source_for(ref.channel)
        try:
            messages = await self._runtime.run(
                workspace_id=workspace_id,
                channel=ref.channel,
                operation="messages",
                func=lambda: source.fetch_messages(
                    workspace_id=workspace_id,
                    conversation=ref,
                    limit=limit,
                    after_external_message_id=after_external_message_id,
                    before_external_message_id=before_external_message_id,
                ),
            )
        except ChannelSyncRateLimitError as exc:
            logger.info(
                "Deferring history sync for workspace=%d conv=%d channel=%s retry_after=%.2fs",
                workspace_id,
                conversation.id,
                ref.channel,
                exc.retry_after_seconds,
            )
            return ConversationSyncResult(requested=0, persisted=0, duplicates=0)
        except ChannelHistorySourceUnavailable as exc:
            logger.warning(
                "Deferring history sync for workspace=%d conv=%d channel=%s source_unavailable=%s",
                workspace_id,
                conversation.id,
                ref.channel,
                exc,
            )
            return ConversationSyncResult(requested=0, persisted=0, duplicates=0)
        if not messages:
            if before_external_message_id:
                self._mark_boundary_complete(
                    conversation=conversation,
                    boundary="oldest",
                    external_message_id=before_external_message_id,
                )
                session.add(conversation)
                await session.commit()
            elif after_external_message_id:
                self._mark_boundary_complete(
                    conversation=conversation,
                    boundary="latest",
                    external_message_id=after_external_message_id,
                )
                session.add(conversation)
                await session.commit()
            return ConversationSyncResult(requested=0, persisted=0, duplicates=0)
        result = await self.persist_history_batch(
            session=session,
            workspace_id=workspace_id,
            conversation=conversation,
            messages=messages,
            batch_limit=limit,
            after_external_message_id=after_external_message_id,
            before_external_message_id=before_external_message_id,
        )
        return result

    async def persist_history_batch(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        conversation: Conversation,
        messages: list[ChannelMessageRecord],
        batch_limit: int | None = None,
        after_external_message_id: str | None = None,
        before_external_message_id: str | None = None,
    ) -> ConversationSyncResult:
        requested = len(messages)
        if not requested:
            return ConversationSyncResult(requested=0, persisted=0, duplicates=0)
        messages = sorted(messages, key=_history_item_sort_key)

        existing_result = await session.execute(
            select(
                Message.id,
                Message.telegram_message_id,
                Message.external_message_id,
                Message.media_type,
                Message.media_metadata,
                Message.text_entities,
                Message.grouped_id,
            ).where(
                Message.conversation_id == conversation.id,
            )
        )
        existing_by_external_id: dict[str, Message] = {}
        for (
            message_id,
            telegram_id,
            external_id,
            media_type,
            media_metadata,
            text_entities,
            grouped_id,
        ) in existing_result.all():
            lookup_id = str(telegram_id if telegram_id is not None else external_id)
            if not lookup_id:
                continue
            existing_by_external_id[lookup_id] = Message(
                id=message_id,
                media_type=media_type,
                media_metadata=media_metadata,
                text_entities=text_entities,
                grouped_id=grouped_id,
            )
        existing_ids = set(existing_by_external_id.keys())

        items_to_persist: list[ChannelMessageRecord] = []
        duplicates = 0

        for item in messages:
            if item.external_message_id in existing_ids:
                duplicates += 1
                existing_stub = existing_by_external_id.get(item.external_message_id)
                if existing_stub is not None and duplicate_needs_repair(
                    existing_stub=existing_stub,
                    incoming=item,
                ):
                    existing_message = await session.get(Message, existing_stub.id)
                    if existing_message is not None and apply_duplicate_repair(
                        existing_message=existing_message,
                        incoming=item,
                    ):
                        session.add(existing_message)
                continue

            items_to_persist.append(item)

        reserved_sequences = await allocate_conversation_sequence_block(
            session,
            conversation,
            len(items_to_persist),
        )

        persisted = 0
        persisted_messages: list[Message] = []
        for item, conversation_seq in zip(items_to_persist, reserved_sequences, strict=False):

            telegram_message_id = (
                _safe_int(item.external_message_id)
                if self._normalize_channel(conversation.channel) == "telegram_dm"
                else None
            )
            reply_to_msg_id = _safe_int(item.reply_to_external_message_id)

            await self._append_history_event(
                workspace_id=workspace_id,
                conversation=conversation,
                item=item,
                telegram_message_id=telegram_message_id,
                reply_to_msg_id=reply_to_msg_id,
            )

            message = Message(
                conversation_id=conversation.id,
                channel=conversation.channel,
                sender_type=(
                    SenderType.SELLER.value
                    if item.is_outgoing
                    else SenderType.CUSTOMER.value
                ),
                content=item.text,
                media_type=item.media_type,
                media_url=canonicalize_message_media_url(
                    media_url=None,
                    telegram_chat_id=conversation.telegram_chat_id,
                    telegram_message_id=telegram_message_id,
                    media_type=item.media_type,
                ),
                telegram_message_id=telegram_message_id,
                external_message_id=item.external_message_id,
                reply_to_msg_id=reply_to_msg_id,
                media_metadata=item.media_metadata,
                text_entities=item.text_entities if item.text_entities is not None else [],
                telegram_timestamp=item.sent_at,
                grouped_id=item.grouped_id,
                is_read=True,
                conversation_seq=conversation_seq,
            )
            project_message_to_dialog_state(
                conversation,
                message=message,
                message_ts=item.sent_at,
                is_outgoing=item.is_outgoing,
                is_read=True,
                text=item.text,
                telegram_message_id=telegram_message_id,
            )
            session.add(message)
            persisted_messages.append(message)
            existing_ids.add(item.external_message_id)
            persisted += 1

        if persisted > 0:
            latest_ts = max((item.sent_at for item in messages), default=None)
            if latest_ts is not None:
                conversation.last_message_at = _newest_datetime(
                    conversation.last_message_at,
                    latest_ts,
                )
            self._update_sync_watermark(
                conversation=conversation,
                messages=messages,
                limit=batch_limit or requested,
                after_external_message_id=after_external_message_id,
                before_external_message_id=before_external_message_id,
            )
            session.add(conversation)

        if persisted_messages:
            await session.flush()
        await session.commit()

        return ConversationSyncResult(
            requested=requested,
            persisted=persisted,
            duplicates=duplicates,
        )

    async def ingest_event(
        self,
        *,
        raw_payload: dict,
        workspace: Workspace,
        session: AsyncSession,
        conversation_turn_runner,
        channel: str = "telegram_dm",
    ) -> dict:
        return await process_inbound_message(
            raw_payload=raw_payload,
            workspace=workspace,
            session=session,
            conversation_turn_runner=conversation_turn_runner,
            channel=channel,
        )

    async def hydrate_media(
        self,
        *,
        session: AsyncSession,
        workspace_id: int,
        conversation: Conversation,
        message: Message,
    ) -> MediaHydrationResult:
        external_message_id = (
            str(message.telegram_message_id)
            if message.telegram_message_id is not None
            else (message.external_message_id or "")
        )
        ref = self._conversation_ref(conversation)
        source = self._source_for(ref.channel)

        async def _fetch_media() -> tuple[bytes, str] | None:
            blob = await self._runtime.run(
                workspace_id=workspace_id,
                channel=ref.channel,
                operation="media",
                func=lambda: source.fetch_media(
                    workspace_id=workspace_id,
                    conversation=ref,
                    external_message_id=external_message_id,
                ),
            )
            if blob is None:
                return None
            return blob.data, blob.mime_type

        return await ChannelMediaAccess().hydrate_for_ai(
            session=session,
            workspace_id=workspace_id,
            conversation=conversation,
            message=message,
            fetch_media=_fetch_media,
        )

    def get_sync_watermark(self, conversation: Conversation) -> ConversationSyncWatermark:
        return get_sync_watermark(conversation)

    def has_exhausted_older_history(
        self,
        *,
        conversation: Conversation,
        external_cursor: str | None,
    ) -> bool:
        return has_exhausted_older_history(
            conversation,
            external_cursor=external_cursor,
        )

    def _source_for(self, channel: str) -> ChannelSourcePort:
        normalized = self._normalize_channel(channel)
        source = self._sources.get(normalized)
        if source is None:
            raise NotImplementedError(f"No conversation source registered for channel={channel}")
        return source

    def _update_sync_watermark(
        self,
        *,
        conversation: Conversation,
        messages: list[ChannelMessageRecord],
        limit: int,
        after_external_message_id: str | None,
        before_external_message_id: str | None,
    ) -> None:
        update_sync_watermark(
            conversation=conversation,
            messages=messages,
            limit=limit,
            after_external_message_id=after_external_message_id,
            before_external_message_id=before_external_message_id,
        )

    def _mark_boundary_complete(
        self,
        *,
        conversation: Conversation,
        boundary: str,
        external_message_id: str,
    ) -> None:
        mark_boundary_complete(
            conversation=conversation,
            boundary=boundary,
            external_message_id=external_message_id,
        )

    @staticmethod
    def _write_dialog_snapshot(
        *,
        conversation: Conversation,
        shell: ChannelConversationShell,
    ) -> None:
        state = get_customer_conversation_state(conversation)
        sync_state = state.sync or ConversationSyncState()
        dialog = sync_state.dialog or ConversationDialogState()
        dialog.telegram_unread_count = int(shell.unread_count or 0)
        if shell.title:
            dialog.title = shell.title
        if _is_incoming_tail_newer_or_equal(
            current=_parse_datetime(dialog.last_message_date) or conversation.last_message_at,
            incoming=shell.last_message_date,
        ):
            dialog.top_message_id = shell.top_message_id
            dialog.last_message_text = shell.last_message_text
            dialog.last_message_is_outgoing = bool(shell.last_message_is_outgoing)
            dialog.last_message_date = (
                shell.last_message_date.isoformat() if shell.last_message_date else None
            )
        sync_state.dialog = dialog
        state.sync = sync_state
        set_customer_conversation_state(conversation, state)

    def _ensure_supported_channel(self, channel: str) -> None:
        self._source_for(channel)

    async def _append_history_event(
        self,
        *,
        workspace_id: int,
        conversation: Conversation,
        item: ChannelMessageRecord,
        telegram_message_id: int | None,
        reply_to_msg_id: int | None,
    ) -> None:
        """Append historical Telegram messages before local row persistence."""
        if self._event_append is None:
            return
        if self._normalize_channel(conversation.channel) != "telegram_dm":
            return
        if conversation.telegram_chat_id is None or telegram_message_id is None:
            return

        event = MsgInbound(
            workspace_id=workspace_id,
            channel="telegram_dm",
            channel_conversation_id=str(conversation.telegram_chat_id),
            channel_message_id=str(telegram_message_id),
            telegram_chat_id=int(conversation.telegram_chat_id),
            telegram_message_id=int(telegram_message_id),
            sender_telegram_id=_safe_int(item.sender_external_id) or 0,
            channel_sender_id=str(item.sender_external_id or ""),
            is_outgoing=bool(item.is_outgoing),
            text=item.text or None,
            media_type=item.media_type,
            media_metadata=item.media_metadata,
            text_entities=item.text_entities,
            reply_to_msg_id=reply_to_msg_id,
            grouped_id=item.grouped_id,
            sent_at=item.sent_at.timestamp(),
            is_historical=True,
            idempotency_key=f"tg:{conversation.telegram_chat_id}:{telegram_message_id}",
        )
        await self._event_append(event)

    @staticmethod
    def _conversation_ref(conversation: Conversation) -> ChannelConversationRef:
        external_chat_id = conversation.external_chat_id or (
            str(conversation.telegram_chat_id) if conversation.telegram_chat_id is not None else ""
        )
        if not external_chat_id:
            raise ValueError("Conversation has no external chat identifier")
        return ChannelConversationRef(
            channel=ChannelConversationSync._normalize_channel(conversation.channel),
            external_chat_id=external_chat_id,
        )

    @staticmethod
    def _normalize_channel(channel: str) -> str:
        normalized = str(channel or "telegram_dm").strip().lower()
        return "telegram_dm" if normalized == "dm" else normalized


def _newest_datetime(current: datetime | None, incoming: datetime) -> datetime:
    if current is None:
        return incoming
    current_aware = current if current.tzinfo else current.replace(tzinfo=timezone.utc)
    incoming_aware = incoming if incoming.tzinfo else incoming.replace(tzinfo=timezone.utc)
    return incoming if incoming_aware > current_aware else current


def _is_incoming_tail_newer_or_equal(
    *,
    current: datetime | None,
    incoming: datetime | None,
) -> bool:
    if incoming is None:
        return current is None
    if current is None:
        return True
    current_aware = current if current.tzinfo else current.replace(tzinfo=timezone.utc)
    incoming_aware = incoming if incoming.tzinfo else incoming.replace(tzinfo=timezone.utc)
    return incoming_aware >= current_aware


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _history_item_sort_key(item: ChannelMessageRecord) -> tuple[datetime, int, str]:
    external_numeric_id = _safe_int(item.external_message_id)
    return (
        item.sent_at,
        external_numeric_id if external_numeric_id is not None else 0,
        item.external_message_id,
    )
