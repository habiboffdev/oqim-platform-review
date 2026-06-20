from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_spine import EventSpine
from app.models.conversation import Conversation
from app.models.conversation_hydration_runtime import ConversationHydrationRuntime
from app.models.customer import Customer
from app.models.message import Message
from app.models.workspace import Workspace
from app.services.channel_conversation_sync import (
    ChannelConversationShell,
    ChannelConversationSync,
    ChannelMessageRecord,
)
from app.services.channel_adapter_contract import ChannelHistorySourceUnavailable
from app.services.channel_sync_runtime import ChannelSyncRateLimitError
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationSyncState,
    get_customer_conversation_state,
    set_customer_conversation_state,
)
from app.services.event_spine_persist_consumer import EventSpinePersistConsumer


@asynccontextmanager
async def _session_context(session: AsyncSession):
    yield session


class _FakeSource:
    def __init__(
        self,
        *,
        shells: list[ChannelConversationShell] | None = None,
        messages: list[ChannelMessageRecord] | None = None,
    ):
        self._shells = shells or []
        self._messages = messages or []

    async def list_conversations(
        self,
        *,
        workspace_id: int,
        channel: str,
        limit: int | None = None,
    ):
        if limit is not None:
            return self._shells[:limit]
        return self._shells

    async def fetch_messages(
        self,
        *,
        workspace_id: int,
        conversation,
        limit: int,
        after_external_message_id: str | None = None,
        before_external_message_id: str | None = None,
    ):
        filtered = list(self._messages)
        if after_external_message_id is not None:
            filtered = [
                msg for msg in filtered
                if int(msg.external_message_id) > int(after_external_message_id)
            ]
        if before_external_message_id is not None:
            filtered = [
                msg for msg in filtered
                if int(msg.external_message_id) < int(before_external_message_id)
            ]
        return filtered[:limit]

    async def fetch_media(
        self,
        *,
        workspace_id: int,
        conversation,
        external_message_id: str,
    ):
        return None


class _FakeMediaSource(_FakeSource):
    def __init__(self, *, blob: bytes, mime_type: str):
        super().__init__(shells=[], messages=[])
        self._blob = blob
        self._mime_type = mime_type

    async def fetch_media(
        self,
        *,
        workspace_id: int,
        conversation,
        external_message_id: str,
    ):
        from app.services.channel_conversation_sync import MediaBlob

        return MediaBlob(data=self._blob, mime_type=self._mime_type)


class _LimitRecordingSource(_FakeSource):
    def __init__(self, *, shells: list[ChannelConversationShell]):
        super().__init__(shells=shells)
        self.received_limits: list[int | None] = []

    async def list_conversations(
        self,
        *,
        workspace_id: int,
        channel: str,
        limit: int | None = None,
    ):
        self.received_limits.append(limit)
        return await super().list_conversations(
            workspace_id=workspace_id,
            channel=channel,
            limit=limit,
        )


class _RateLimitedSource(_FakeSource):
    async def list_conversations(
        self,
        *,
        workspace_id: int,
        channel: str,
        limit: int | None = None,
    ):
        raise ChannelSyncRateLimitError(
            retry_after_seconds=10,
            channel=channel,
            operation="dialogs",
        )

    async def fetch_messages(
        self,
        *,
        workspace_id: int,
        conversation,
        limit: int,
        after_external_message_id: str | None = None,
        before_external_message_id: str | None = None,
    ):
        raise ChannelSyncRateLimitError(
            retry_after_seconds=10,
            channel=conversation.channel,
            operation="messages",
        )

    async def fetch_media(
        self,
        *,
        workspace_id: int,
        conversation,
        external_message_id: str,
    ):
        raise ChannelSyncRateLimitError(
            retry_after_seconds=10,
            channel=conversation.channel,
            operation="media",
        )


class _UnavailableSource(_FakeSource):
    async def list_conversations(
        self, *, workspace_id: int, channel: str, limit: int | None = None
    ):
        raise ChannelHistorySourceUnavailable("history source unavailable")

    async def fetch_messages(
        self,
        *,
        workspace_id: int,
        conversation,
        limit: int,
        after_external_message_id: str | None = None,
        before_external_message_id: str | None = None,
    ):
        raise ChannelHistorySourceUnavailable("history source unavailable")


class TestChannelConversationSync:
    async def test_bootstrap_inbox_creates_only_shells(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    shells=[
                        ChannelConversationShell(
                            external_chat_id="5924086090",
                            title="Mirzosharif",
                            unread_count=1,
                            last_message_text="salom",
                            last_message_date=datetime(2026, 4, 15, tzinfo=timezone.utc),
                        ),
                        ChannelConversationShell(
                            external_chat_id="6307195018",
                            title="Nurl@n",
                        ),
                    ]
                )
            }
        )

        result = await sync.bootstrap_inbox(
            session=db_session,
            workspace_id=workspace.id,
            channel="telegram_dm",
            visible_limit=50,
        )

        assert result.synced_count == 2

        conversations = (
            await db_session.execute(
                select(Conversation)
                .where(Conversation.workspace_id == workspace.id)
                .order_by(Conversation.telegram_chat_id.asc())
            )
        ).scalars().all()
        assert [conv.telegram_chat_id for conv in conversations] == [5924086090, 6307195018]
        assert conversations[0].summary == "salom"

        customers = (
            await db_session.execute(
                select(Customer)
                .where(Customer.workspace_id == workspace.id)
                .order_by(Customer.telegram_id.asc())
            )
        ).scalars().all()
        assert [customer.display_name for customer in customers] == ["Mirzosharif", "Nurl@n"]

        message_count = await db_session.scalar(
            select(func.count(Message.id))
            .join(Conversation)
            .where(Conversation.workspace_id == workspace.id)
        )
        assert message_count == 0

    async def test_bootstrap_inbox_passes_visible_limit_to_dialog_source(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        source = _LimitRecordingSource(
            shells=[
                ChannelConversationShell(
                    external_chat_id=str(700000 + index),
                    title=f"Customer {index}",
                )
                for index in range(20)
            ]
        )
        sync = ChannelConversationSync(sources={"telegram_dm": source})

        result = await sync.bootstrap_inbox(
            session=db_session,
            workspace_id=workspace.id,
            channel="telegram_dm",
            visible_limit=12,
        )

        assert source.received_limits == [12]
        assert result.synced_count == 12

    async def test_dialog_sync_queues_bounded_stale_shell_hydration_jobs(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        now = datetime(2026, 4, 15, tzinfo=timezone.utc)
        fresh_shells = [
            ChannelConversationShell(
                external_chat_id=str(7777000 + index),
                title=f"Already Local {index}",
                unread_count=0,
                top_message_id=3200 + index,
                last_message_text=f"fresh {index}",
                last_message_date=now.replace(hour=13, minute=index),
            )
            for index in range(5)
        ]
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    shells=[
                        *fresh_shells,
                        ChannelConversationShell(
                            external_chat_id="7777999",
                            title="Stale Visible Tail",
                            top_message_id=1161,
                            last_message_text="ikkinchi",
                            last_message_date=now.replace(hour=12),
                        ),
                    ]
                )
            }
        )

        await sync.bootstrap_inbox(
            session=db_session,
            workspace_id=workspace.id,
            channel="telegram_dm",
            visible_limit=50,
        )
        fresh_conversations = (
            await db_session.execute(
                select(Conversation)
                .where(
                    Conversation.workspace_id == workspace.id,
                    Conversation.external_chat_id.in_([
                        shell.external_chat_id for shell in fresh_shells
                    ]),
                )
            )
        ).scalars().all()
        for conversation in fresh_conversations:
            db_session.add(
                Message(
                    conversation_id=conversation.id,
                    channel="telegram_dm",
                    sender_type="customer",
                    content=conversation.summary or "",
                    telegram_message_id=3200 + conversation.id,
                    external_message_id=f"local-{conversation.id}",
                    telegram_timestamp=conversation.last_message_at,
                    is_read=True,
                )
            )
        await db_session.flush()

        result = await sync.queue_stale_dialog_hydrations(
            session=db_session,
            workspace_id=workspace.id,
            channel="telegram_dm",
            max_conversations=1,
            request_limit=50,
        )

        assert result.queued_conversations == 1
        runtimes = (
            await db_session.execute(
                select(ConversationHydrationRuntime)
                .where(ConversationHydrationRuntime.workspace_id == workspace.id)
                .order_by(ConversationHydrationRuntime.id.asc())
            )
        ).scalars().all()
        assert len(runtimes) == 1
        assert runtimes[0].state == "queued"
        assert runtimes[0].reason == "dialog_sync_tail"
        assert runtimes[0].requested_limit == 50
        stale_conversation = await db_session.scalar(
            select(Conversation).where(
                Conversation.workspace_id == workspace.id,
                Conversation.external_chat_id == "7777999",
            )
        )
        assert stale_conversation is not None
        assert runtimes[0].conversation_id == stale_conversation.id

        message_count = await db_session.scalar(
            select(func.count(Message.id))
            .join(Conversation)
            .where(Conversation.workspace_id == workspace.id)
        )
        assert message_count == 5

    async def test_bootstrap_inbox_accepts_adapter_external_conversation_ids(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(
            sources={
                "instagram_dm": _FakeSource(
                    shells=[
                        ChannelConversationShell(
                            external_chat_id="ig-thread-alpha",
                            title="Instagram Lead",
                            unread_count=3,
                            last_message_text="price?",
                            last_message_date=datetime(2026, 4, 16, tzinfo=timezone.utc),
                        ),
                    ]
                )
            }
        )

        result = await sync.bootstrap_inbox(
            session=db_session,
            workspace_id=workspace.id,
            channel="instagram_dm",
            visible_limit=50,
        )

        assert result.synced_count == 1
        conversation = await db_session.scalar(
            select(Conversation).where(
                Conversation.workspace_id == workspace.id,
                Conversation.channel == "instagram_dm",
            )
        )
        customer = await db_session.scalar(
            select(Customer).where(
                Customer.workspace_id == workspace.id,
                Customer.channel == "instagram_dm",
            )
        )
        assert conversation is not None
        assert customer is not None
        assert conversation.telegram_chat_id is None
        assert conversation.external_chat_id == "ig-thread-alpha"
        assert conversation.summary == "price?"
        assert customer.telegram_id is None
        assert customer.external_id == "ig-thread-alpha"

    async def test_sync_conversation_persists_latest_page(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="101",
                            sender_external_id="9001",
                            text="Salom",
                            sent_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
                            is_outgoing=False,
                        ),
                        ChannelMessageRecord(
                            external_message_id="102",
                            sender_external_id=str(conversation.telegram_chat_id),
                            text="Ha, eshitaman",
                            sent_at=datetime(2026, 4, 15, 10, 1, tzinfo=timezone.utc),
                            is_outgoing=True,
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.requested == 2
        assert result.persisted == 2
        assert result.duplicates == 0

        messages = (
            await db_session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.telegram_message_id.asc())
            )
        ).scalars().all()
        assert [message.telegram_message_id for message in messages] == [101, 102]
        assert [message.sender_type for message in messages] == ["customer", "seller"]

    async def test_sync_conversation_can_persist_history_without_actions(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="201",
                            sender_external_id="9001",
                            text="Tez sync",
                            sent_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
                            is_outgoing=False,
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.persisted == 1
        saved = await db_session.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.telegram_message_id == 201,
            )
        )
        assert saved is not None

    async def test_sync_conversation_appends_historical_event_before_persisting(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        event_append = AsyncMock(return_value="1-0")
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="202",
                            sender_external_id="9001",
                            text="History first",
                            sent_at=datetime(2026, 4, 15, 10, 5, tzinfo=timezone.utc),
                            is_outgoing=False,
                            media_type="photo",
                            media_metadata={"mime_type": "image/jpeg"},
                        ),
                    ]
                )
            },
            event_append=event_append,
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.persisted == 1
        event_append.assert_awaited_once()
        event = event_append.await_args.args[0]
        assert event.type == "msg.inbound"
        assert event.is_historical is True
        assert event.idempotency_key == f"tg:{conversation.telegram_chat_id}:202"
        assert event.media_type == "photo"

        saved = await db_session.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.telegram_message_id == 202,
            )
        )
        assert saved is not None

    async def test_hydrated_history_replays_from_event_spine_after_projection_wipe(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
        fake_redis,
    ):
        sent_at = datetime(2026, 4, 15, 10, 5, tzinfo=timezone.utc)
        spine = EventSpine(fake_redis)
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="211",
                            sender_external_id="9001",
                            text="History one",
                            sent_at=sent_at,
                            is_outgoing=False,
                        ),
                        ChannelMessageRecord(
                            external_message_id="212",
                            sender_external_id=str(conversation.telegram_chat_id),
                            text="History two",
                            sent_at=datetime(2026, 4, 15, 10, 6, tzinfo=timezone.utc),
                            is_outgoing=True,
                            reply_to_external_message_id="211",
                        ),
                    ]
                )
            },
            event_append=spine.append,
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.persisted == 2
        stream_events = await spine.replay_conversation(
            workspace_id=workspace.id,
            channel="telegram_dm",
            channel_conversation_id=str(conversation.telegram_chat_id),
        )
        assert [event.channel_message_id for event in stream_events] == ["211", "212"]
        assert all(event.is_historical for event in stream_events)

        await db_session.execute(delete(Message).where(Message.conversation_id == conversation.id))
        conversation.message_sequence = 0
        conversation.message_revision = 0
        conversation.last_message_at = None
        conversation.crm_state = None
        db_session.add(conversation)
        await db_session.commit()

        replay_consumer = EventSpinePersistConsumer(
            redis=fake_redis,
            db_factory=lambda: _session_context(db_session),
            workspace_ids_provider=lambda: [workspace.id],
            mode="authoritative",
        )
        with patch(
            "app.services.inbound_pipeline.process_persisted_message_event",
            side_effect=AssertionError("history replay must not call legacy intake"),
        ):
            replay = await replay_consumer.replay_conversation(
                workspace_id=workspace.id,
                channel="telegram_dm",
                channel_conversation_id=str(conversation.telegram_chat_id),
            )

        messages = (
            await db_session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.conversation_seq.asc())
            )
        ).scalars().all()
        await db_session.refresh(conversation)

        assert replay.events_seen == 2
        assert replay.events_applied == 2
        assert replay.events_missing_projection == 0
        assert replay.events_unsupported == 0
        assert [message.content for message in messages] == ["History one", "History two"]
        assert [message.telegram_message_id for message in messages] == [211, 212]
        assert [message.sender_type for message in messages] == ["customer", "seller"]
        assert messages[1].reply_to_msg_id == 211
        assert all(message.is_read for message in messages)
        state = get_customer_conversation_state(conversation)
        assert state.sync is not None
        assert state.sync.dialog is not None
        assert state.sync.dialog.last_message_text == "History two"
        assert state.sync.dialog.top_message_id == 212
        assert conversation.message_sequence == 2
        assert conversation.message_revision == 2

    async def test_sync_conversation_does_not_persist_when_history_event_append_fails(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        event_append = AsyncMock(side_effect=RuntimeError("redis down"))
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="203",
                            sender_external_id="9001",
                            text="No orphan",
                            sent_at=datetime(2026, 4, 15, 10, 10, tzinfo=timezone.utc),
                            is_outgoing=False,
                        ),
                    ]
                )
            },
            event_append=event_append,
        )

        try:
            await sync.sync_conversation(
                session=db_session,
                workspace_id=workspace.id,
                conversation=conversation,
                limit=50,
            )
        except RuntimeError as exc:
            assert str(exc) == "redis down"
        else:
            raise AssertionError("history sync should fail closed when EventSpine append fails")

        await db_session.rollback()
        saved = await db_session.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.telegram_message_id == 203,
            )
        )
        assert saved is None

    async def test_sync_conversation_persists_non_telegram_history_through_adapter_source(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        customer = Customer(
            workspace_id=workspace.id,
            display_name="Instagram Lead",
            channel="instagram_dm",
            external_id="ig-user-1",
        )
        db_session.add(customer)
        await db_session.flush()
        conversation = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="instagram_dm",
            external_chat_id="ig-thread-alpha",
        )
        db_session.add(conversation)
        await db_session.flush()
        sync = ChannelConversationSync(
            sources={
                "instagram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="ig-msg-a",
                            sender_external_id="ig-user-1",
                            text="price?",
                            sent_at=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc),
                            is_outgoing=False,
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.requested == 1
        assert result.persisted == 1
        message = await db_session.scalar(
            select(Message).where(Message.conversation_id == conversation.id)
        )
        assert message is not None
        assert message.channel == "instagram_dm"
        assert message.external_message_id == "ig-msg-a"
        assert message.telegram_message_id is None
        assert message.sender_type == "customer"

    async def test_sync_conversation_older_page_does_not_regress_latest_timestamp(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        conversation.last_message_at = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="91",
                            sender_external_id="9001",
                            text="Old page",
                            sent_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
                            is_outgoing=False,
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
            before_external_message_id="100",
        )

        assert result.persisted == 1
        await db_session.refresh(conversation)
        assert conversation.last_message_at == datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)

    async def test_history_sync_projects_newer_message_into_dialog_tail(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        state = get_customer_conversation_state(conversation)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(
                telegram_unread_count=4,
                last_message_text="stale shell tail",
                last_message_date="2026-04-15T10:00:00+00:00",
            )
        )
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="301",
                            sender_external_id="9001",
                            text="new projected history tail",
                            sent_at=datetime(2026, 4, 15, 10, 1, tzinfo=timezone.utc),
                            is_outgoing=False,
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.persisted == 1
        await db_session.refresh(conversation)
        projected = get_customer_conversation_state(conversation).sync.dialog
        assert projected.last_message_text == "new projected history tail"
        assert projected.last_message_date == "2026-04-15T10:01:00+00:00"
        assert projected.telegram_unread_count == 4

    async def test_history_sync_projects_media_only_tail_with_media_label(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="914",
                            sender_external_id="9001",
                            text="",
                            sent_at=datetime(2026, 5, 1, 14, 10, tzinfo=timezone.utc),
                            is_outgoing=False,
                            media_type="document",
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.persisted == 1
        await db_session.refresh(conversation)
        projected = get_customer_conversation_state(conversation).sync.dialog
        assert projected.last_message_text == "Fayl"

    async def test_dialog_shell_does_not_regress_newer_message_tail(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        conversation.channel = "telegram_dm"
        conversation.external_chat_id = str(conversation.telegram_chat_id)
        conversation.last_message_at = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        state = get_customer_conversation_state(conversation)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(
                telegram_unread_count=0,
                title="Current title",
                top_message_id=500,
                last_message_text="fresh local tail",
                last_message_date="2026-04-20T12:00:00+00:00",
            )
        )
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    shells=[
                        ChannelConversationShell(
                            external_chat_id=str(conversation.telegram_chat_id),
                            title="Current title",
                            unread_count=3,
                            top_message_id=450,
                            last_message_text="older shell tail",
                            last_message_date=datetime(2026, 4, 20, 11, 0, tzinfo=timezone.utc),
                        )
                    ]
                )
            }
        )

        result = await sync.bootstrap_inbox(
            session=db_session,
            workspace_id=workspace.id,
            channel="telegram_dm",
        )

        assert result.synced_count == 1
        await db_session.refresh(conversation)
        projected = get_customer_conversation_state(conversation).sync.dialog
        assert conversation.last_message_at == datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        assert conversation.summary != "older shell tail"
        assert projected.last_message_text == "fresh local tail"
        assert projected.last_message_date == "2026-04-20T12:00:00+00:00"
        assert projected.telegram_unread_count == 3

    async def test_sync_conversation_persists_media_metadata_and_grouped_id(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="111",
                            sender_external_id="9001",
                            text="",
                            sent_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
                            is_outgoing=False,
                            media_type="document",
                            media_metadata={
                                "file_name": "price-list.pdf",
                                "mime_type": "application/pdf",
                                "file_size": 2048,
                                "has_thumbnail": True,
                            },
                            grouped_id=777,
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.persisted == 1

        message = await db_session.scalar(
            select(Message).where(Message.conversation_id == conversation.id)
        )
        assert message is not None
        assert message.media_type == "document"
        assert message.grouped_id == 777
        assert message.media_metadata == {
            "file_name": "price-list.pdf",
            "mime_type": "application/pdf",
            "file_size": 2048,
            "has_thumbnail": True,
        }

    async def test_sync_conversation_repairs_duplicate_media_metadata(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        existing = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="legacy file",
            media_type="document",
            telegram_message_id=222,
            external_message_id="222",
            media_metadata=None,
            telegram_timestamp=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
            is_read=True,
        )
        db_session.add(existing)
        await db_session.commit()

        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="222",
                            sender_external_id="9001",
                            text="legacy file",
                            sent_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
                            is_outgoing=False,
                            media_type="document",
                            media_metadata={
                                "file_name": "invoice.pdf",
                                "mime_type": "application/pdf",
                            },
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.persisted == 0
        assert result.duplicates == 1

        repaired = await db_session.get(Message, existing.id)
        assert repaired is not None
        assert repaired.media_metadata == {
            "file_name": "invoice.pdf",
            "mime_type": "application/pdf",
        }

    async def test_sync_conversation_repairs_duplicate_text_entities(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        existing = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="😘",
            telegram_message_id=223,
            external_message_id="223",
            text_entities=None,
            telegram_timestamp=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
            is_read=True,
        )
        db_session.add(existing)
        await db_session.commit()

        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="223",
                            sender_external_id="9001",
                            text="😘",
                            sent_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
                            is_outgoing=False,
                            text_entities=[{
                                "type": "custom_emoji",
                                "offset": 0,
                                "length": 2,
                                "document_id": "5555",
                            }],
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.persisted == 0
        assert result.duplicates == 1

        repaired = await db_session.get(Message, existing.id)
        assert repaired is not None
        assert repaired.text_entities == [{
            "type": "custom_emoji",
            "offset": 0,
            "length": 2,
            "document_id": "5555",
        }]

    async def test_sync_conversation_repairs_duplicate_legacy_media_type(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        existing = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="",
            media_type="document",
            telegram_message_id=223,
            external_message_id="223",
            media_metadata=None,
            telegram_timestamp=datetime(2026, 4, 15, 10, 1, tzinfo=timezone.utc),
            is_read=True,
        )
        db_session.add(existing)
        await db_session.commit()

        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="223",
                            sender_external_id="9001",
                            text="",
                            sent_at=datetime(2026, 4, 15, 10, 1, tzinfo=timezone.utc),
                            is_outgoing=False,
                            media_type="sticker",
                            media_metadata={
                                "file_name": "sticker.webp",
                                "mime_type": "image/webp",
                                "emoji": "🤩",
                            },
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.persisted == 0
        assert result.duplicates == 1

        repaired = await db_session.get(Message, existing.id)
        assert repaired is not None
        assert repaired.media_type == "sticker"
        assert repaired.media_metadata == {
            "file_name": "sticker.webp",
            "mime_type": "image/webp",
            "emoji": "🤩",
        }

    async def test_sync_conversation_can_fetch_older_page(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(
            sources={
                "telegram_dm": _FakeSource(
                    messages=[
                        ChannelMessageRecord(
                            external_message_id="80",
                            sender_external_id="9001",
                            text="Older one",
                            sent_at=datetime(2026, 4, 15, 9, 58, tzinfo=timezone.utc),
                            is_outgoing=False,
                        ),
                        ChannelMessageRecord(
                            external_message_id="90",
                            sender_external_id="9001",
                            text="Older two",
                            sent_at=datetime(2026, 4, 15, 9, 59, tzinfo=timezone.utc),
                            is_outgoing=False,
                        ),
                        ChannelMessageRecord(
                            external_message_id="100",
                            sender_external_id="9001",
                            text="Current edge",
                            sent_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
                            is_outgoing=False,
                        ),
                    ]
                )
            }
        )

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=10,
            before_external_message_id="100",
        )

        assert result.persisted == 2
        messages = (
            await db_session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.telegram_message_id.asc())
            )
        ).scalars().all()
        assert [message.telegram_message_id for message in messages] == [80, 90]
        watermark = sync.get_sync_watermark(conversation)
        assert watermark.oldest_external_message_id == "80"
        assert watermark.latest_external_message_id == "90"
        assert watermark.oldest_complete is True

    async def test_sync_conversation_marks_oldest_complete_on_empty_older_fetch(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(sources={"telegram_dm": _FakeSource(messages=[])})

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
            before_external_message_id="500",
        )

        assert result.requested == 0
        watermark = sync.get_sync_watermark(conversation)
        assert watermark.oldest_external_message_id == "500"
        assert watermark.oldest_complete is True

    async def test_prefetch_recent_history_respects_budget_and_skips_complete(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        stale_customer = Customer(workspace_id=workspace.id, display_name="Stale")
        fresh_customer = Customer(workspace_id=workspace.id, display_name="Fresh")
        newer_customer = Customer(workspace_id=workspace.id, display_name="Newer")
        db_session.add_all([stale_customer, fresh_customer, newer_customer])
        await db_session.flush()

        complete_conv = Conversation(
            workspace_id=workspace.id,
            customer_id=stale_customer.id,
            channel="telegram_dm",
            telegram_chat_id=9001,
            external_chat_id="9001",
            last_message_at=datetime(2026, 4, 15, 9, 0, tzinfo=timezone.utc),
            crm_state={
                "sync": {
                    "watermarks": {
                        "latest_external_message_id": "200",
                        "latest_complete": True,
                    }
                }
            },
        )
        fresh_conv = Conversation(
            workspace_id=workspace.id,
            customer_id=fresh_customer.id,
            channel="telegram_dm",
            telegram_chat_id=9002,
            external_chat_id="9002",
            last_message_at=datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc),
        )
        newer_conv = Conversation(
            workspace_id=workspace.id,
            customer_id=newer_customer.id,
            channel="telegram_dm",
            telegram_chat_id=9003,
            external_chat_id="9003",
            last_message_at=datetime(2026, 4, 15, 11, 0, tzinfo=timezone.utc),
        )
        db_session.add_all([complete_conv, fresh_conv, newer_conv])
        await db_session.flush()

        sync = ChannelConversationSync(sources={"telegram_dm": _FakeSource(messages=[])})
        calls: list[int] = []

        async def _fake_sync(
            *,
            session,
            workspace_id,
            conversation,
            limit,
            after_external_message_id=None,
            before_external_message_id=None,
        ):
            calls.append(conversation.id)
            return type(
                "_Result",
                (),
                {"requested": 1, "persisted": 1, "duplicates": 0},
            )()

        with patch.object(sync, "sync_conversation", new=_fake_sync):
            result = await sync.prefetch_recent_history(
                session=db_session,
                workspace_id=workspace.id,
                channel="telegram_dm",
                max_conversations=2,
                page_limit=25,
            )

        assert calls == [newer_conv.id, fresh_conv.id]
        assert result.prefetched_conversations == 2
        assert result.persisted_messages == 2
        assert result.deferred is False

    async def test_prefetch_recent_history_stops_when_sync_is_deferred(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        customer = Customer(workspace_id=workspace.id, display_name="Fresh")
        db_session.add(customer)
        await db_session.flush()
        conversation = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=9010,
            external_chat_id="9010",
            last_message_at=datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc),
        )
        db_session.add(conversation)
        await db_session.flush()

        sync = ChannelConversationSync(sources={"telegram_dm": _FakeSource(messages=[])})

        async def _deferred(
            *,
            session,
            workspace_id,
            conversation,
            limit,
            after_external_message_id=None,
            before_external_message_id=None,
        ):
            return type(
                "_Result",
                (),
                {"requested": 0, "persisted": 0, "duplicates": 0},
            )()

        with patch.object(sync, "sync_conversation", new=_deferred):
            result = await sync.prefetch_recent_history(
                session=db_session,
                workspace_id=workspace.id,
                channel="telegram_dm",
                max_conversations=3,
                page_limit=25,
            )

        assert result.prefetched_conversations == 0
        assert result.persisted_messages == 0
        assert result.deferred is True

    async def test_hydrate_media_updates_photo_message(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="[photo] Mijoz rasm yubordi",
            media_type="photo",
            telegram_message_id=501,
            external_message_id="501",
            telegram_timestamp=datetime.now(timezone.utc),
            is_read=True,
        )
        db_session.add(message)
        await db_session.commit()

        sync = ChannelConversationSync(
            sources={"telegram_dm": _FakeMediaSource(blob=b"fake-image", mime_type="image/jpeg")}
        )

        with patch("app.modules.extraction_runtime.media_semantics.normalize_image_message") as mock_normalize:
            async def _normalize(_bytes, _mime, **_kwargs):
                from app.modules.extraction_runtime.media_semantics import NormalizedMessage

                return NormalizedMessage(
                    text="[photo] Red iPhone case",
                    confidence=0.9,
                    original_type="photo",
                )

            mock_normalize.side_effect = _normalize
            result = await sync.hydrate_media(
                session=db_session,
                workspace_id=workspace.id,
                conversation=conversation,
                message=message,
            )

        refreshed = await db_session.get(Message, message.id)
        assert refreshed.content == "[photo] Red iPhone case"
        assert refreshed.media_description == "[photo] Red iPhone case"
        assert refreshed.media_url == f"/api/media/{conversation.telegram_chat_id}/{message.telegram_message_id}"
        assert refreshed.media_metadata["hydrated"] is True
        assert result.media_bytes_b64 is not None
        assert result.media_mime_type == "image/jpeg"

    async def test_hydrate_media_updates_voice_message(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="[voice] Mijoz ovozli xabar yubordi",
            media_type="voice",
            telegram_message_id=601,
            external_message_id="601",
            telegram_timestamp=datetime.now(timezone.utc),
            is_read=True,
        )
        db_session.add(message)
        await db_session.commit()

        sync = ChannelConversationSync(
            sources={"telegram_dm": _FakeMediaSource(blob=b"fake-audio", mime_type="audio/ogg")}
        )

        with patch("app.modules.extraction_runtime.media_semantics.normalize_voice_message") as mock_normalize:
            async def _normalize(_bytes, _mime, **_kwargs):
                from app.modules.extraction_runtime.media_semantics import NormalizedMessage

                return NormalizedMessage(
                    text="Assalomu alaykum",
                    confidence=0.85,
                    original_type="voice",
                )

            mock_normalize.side_effect = _normalize
            result = await sync.hydrate_media(
                session=db_session,
                workspace_id=workspace.id,
                conversation=conversation,
                message=message,
            )

        refreshed = await db_session.get(Message, message.id)
        assert refreshed.content == "Assalomu alaykum"
        assert refreshed.transcription == "Assalomu alaykum"
        assert refreshed.transcription_confidence == 0.85
        assert refreshed.media_metadata["hydrated"] is True
        assert result.media_bytes_b64 is None

    async def test_bootstrap_inbox_returns_empty_when_rate_limited(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(sources={"telegram_dm": _RateLimitedSource()})

        result = await sync.bootstrap_inbox(
            session=db_session,
            workspace_id=workspace.id,
            channel="telegram_dm",
            visible_limit=50,
        )

        assert result.synced_count == 0

    async def test_sync_conversation_returns_empty_when_rate_limited(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(sources={"telegram_dm": _RateLimitedSource()})

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.requested == 0
        assert result.persisted == 0
        assert result.duplicates == 0

    async def test_bootstrap_inbox_returns_empty_when_source_unavailable(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(sources={"telegram_dm": _UnavailableSource()})

        result = await sync.bootstrap_inbox(
            session=db_session,
            workspace_id=workspace.id,
            channel="telegram_dm",
            visible_limit=50,
        )

        assert result.synced_count == 0

    async def test_sync_conversation_returns_empty_when_source_unavailable(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        sync = ChannelConversationSync(sources={"telegram_dm": _UnavailableSource()})

        result = await sync.sync_conversation(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            limit=50,
        )

        assert result.requested == 0
        assert result.persisted == 0
        assert result.duplicates == 0

    async def test_hydrate_media_returns_empty_when_rate_limited(
        self,
        db_session: AsyncSession,
        conversation: Conversation,
        workspace: Workspace,
    ):
        message = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="[photo] Mijoz rasm yubordi",
            media_type="photo",
            telegram_message_id=701,
            external_message_id="701",
            telegram_timestamp=datetime.now(timezone.utc),
            is_read=True,
        )
        db_session.add(message)
        await db_session.commit()

        sync = ChannelConversationSync(sources={"telegram_dm": _RateLimitedSource()})
        result = await sync.hydrate_media(
            session=db_session,
            workspace_id=workspace.id,
            conversation=conversation,
            message=message,
        )

        assert result.media_bytes_b64 is None
        assert result.media_mime_type is None
        assert result.normalized_text is None
