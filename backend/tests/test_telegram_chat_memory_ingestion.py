import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.media_runtime import MediaRuntime
from app.models.message import Message
from app.models.workspace import Workspace

pytestmark = pytest.mark.asyncio


async def _create_sidecar_tables(session: AsyncSession) -> None:
    statements = [
        """
            CREATE TABLE IF NOT EXISTS telegram_sidecar_peers (
              workspace_id BIGINT NOT NULL,
              peer_id TEXT NOT NULL,
              peer_kind TEXT NOT NULL,
              access_hash TEXT,
              display_name TEXT,
              username TEXT,
              phone TEXT,
              flags JSONB NOT NULL DEFAULT '{}'::jsonb,
              source TEXT NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY (workspace_id, peer_id, peer_kind)
            )
        """,
        """
            CREATE TABLE IF NOT EXISTS telegram_sidecar_messages (
              workspace_id BIGINT NOT NULL,
              chat_id TEXT NOT NULL,
              message_id TEXT NOT NULL,
              sender_id TEXT,
              message_date BIGINT,
              text TEXT NOT NULL DEFAULT '',
              is_outgoing BOOLEAN NOT NULL DEFAULT false,
              media_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
              source TEXT NOT NULL,
              raw JSONB NOT NULL DEFAULT '{}'::jsonb,
              received_at DOUBLE PRECISION,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY (workspace_id, chat_id, message_id)
            )
        """,
        """
            CREATE TABLE IF NOT EXISTS telegram_sidecar_media_refs (
              workspace_id BIGINT NOT NULL,
              chat_id TEXT NOT NULL,
              message_id TEXT NOT NULL,
              media_key TEXT NOT NULL,
              media_kind TEXT NOT NULL,
              document_id TEXT,
              photo_id TEXT,
              mime_type TEXT,
              size BIGINT,
              source TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              queued_at DOUBLE PRECISION,
              hydrated_at DOUBLE PRECISION,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY (workspace_id, chat_id, message_id, media_key)
            )
        """,
    ]
    for statement in statements:
        await session.execute(text(statement))


async def _insert_sidecar_peer(
    session: AsyncSession,
    *,
    workspace_id: int,
    chat_id: str,
    display_name: str,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO telegram_sidecar_peers (
              workspace_id, peer_id, peer_kind, display_name, source
            )
            VALUES (:workspace_id, :peer_id, 'user', :display_name, 'dialog_sync')
            """
        ),
        {
            "workspace_id": workspace_id,
            "peer_id": chat_id,
            "display_name": display_name,
        },
    )


async def _insert_sidecar_message(
    session: AsyncSession,
    *,
    workspace_id: int,
    chat_id: str,
    message_id: str,
    sender_id: str,
    message_date: int,
    text_value: str,
    is_outgoing: bool = False,
    media_ref: dict | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO telegram_sidecar_messages (
              workspace_id, chat_id, message_id, sender_id, message_date, text,
              is_outgoing, media_ref, source, raw, received_at
            )
            VALUES (
              :workspace_id, :chat_id, :message_id, :sender_id, :message_date,
              :text_value, :is_outgoing, CAST(:media_ref AS jsonb),
              'history_sync', CAST(:raw AS jsonb), :received_at
            )
            """
        ),
        {
            "workspace_id": workspace_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "sender_id": sender_id,
            "message_date": message_date,
            "text_value": text_value,
            "is_outgoing": is_outgoing,
            "media_ref": json.dumps(media_ref or {}),
            "raw": json.dumps({"source": "test"}),
            "received_at": float(message_date),
        },
    )


async def _insert_sidecar_media_ref(
    session: AsyncSession,
    *,
    workspace_id: int,
    chat_id: str,
    message_id: str,
    media_key: str,
    media_kind: str,
    photo_id: str | None = None,
    mime_type: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO telegram_sidecar_media_refs (
              workspace_id, chat_id, message_id, media_key, media_kind,
              photo_id, mime_type, size, source, status, queued_at
            )
            VALUES (
              :workspace_id, :chat_id, :message_id, :media_key, :media_kind,
              :photo_id, :mime_type, 128, 'history_sync', 'pending', 1780000000.0
            )
            """
        ),
        {
            "workspace_id": workspace_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "media_key": media_key,
            "media_kind": media_kind,
            "photo_id": photo_id,
            "mime_type": mime_type,
        },
    )


async def test_ingests_sidecar_raw_messages_into_backend_chat_projection(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    from app.services.telegram_chat_memory_ingestion import TelegramChatMemoryIngestionService

    await _create_sidecar_tables(db_session)
    chat_id = "445566"
    sender_id = "778899"
    sent_at = int(datetime(2026, 5, 30, 10, 0, tzinfo=UTC).timestamp())
    await _insert_sidecar_peer(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        display_name="Ali Xaridor",
    )
    await _insert_sidecar_message(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        message_id="8801",
        sender_id=sender_id,
        message_date=sent_at,
        text_value="Kecha qarz yozib qo'ying",
    )
    await _insert_sidecar_message(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        message_id="8802",
        sender_id=sender_id,
        message_date=sent_at + 1,
        text_value="",
        media_ref={
            "className": "MessageMediaPhoto",
            "photoId": "photo-8802",
            "mimeType": "image/jpeg",
            "size": 128,
        },
    )
    await _insert_sidecar_media_ref(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        message_id="8802",
        media_key="photo:photo-8802",
        media_kind="MessageMediaPhoto",
        photo_id="photo-8802",
        mime_type="image/jpeg",
    )

    result = await TelegramChatMemoryIngestionService().ingest_due_raw_messages(
        session=db_session,
        workspace_id=workspace.id,
        limit=10,
    )

    assert result.scanned == 2
    assert result.persisted == 2
    assert result.duplicates == 0
    assert result.conversations == 1
    assert result.media_runtime_queued == 1

    conversation = await db_session.scalar(
        select(Conversation).where(
            Conversation.workspace_id == workspace.id,
            Conversation.telegram_chat_id == int(chat_id),
        )
    )
    assert conversation is not None

    messages = list(
        (
            await db_session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.telegram_message_id.asc())
            )
        ).all()
    )
    assert [message.telegram_message_id for message in messages] == [8801, 8802]
    assert messages[0].content == "Kecha qarz yozib qo'ying"
    assert messages[1].media_type == "photo"
    assert messages[1].content == "[photo] Mijoz rasm yubordi"
    assert messages[1].media_metadata["hydration_status"] == "pending"
    assert messages[1].media_metadata["sidecar_media_refs"][0]["media_key"] == "photo:photo-8802"

    runtime = await db_session.scalar(
        select(MediaRuntime).where(MediaRuntime.message_id == messages[1].id)
    )
    assert runtime is not None
    assert runtime.hydration_status == "pending"
    assert runtime.action_state == "pending"

    idempotent = await TelegramChatMemoryIngestionService().ingest_due_raw_messages(
        session=db_session,
        workspace_id=workspace.id,
        limit=10,
    )

    assert idempotent.scanned == 0
    assert idempotent.persisted == 0
    assert idempotent.duplicates == 0
    count = await db_session.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id)
    )
    assert count == 2


async def test_ingestion_advances_past_projected_rows_with_small_limit(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    from app.services.telegram_chat_memory_ingestion import TelegramChatMemoryIngestionService

    await _create_sidecar_tables(db_session)
    chat_id = "445577"
    sender_id = "778800"
    sent_at = int(datetime(2026, 5, 30, 11, 0, tzinfo=UTC).timestamp())
    await _insert_sidecar_peer(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        display_name="Vali Xaridor",
    )
    await _insert_sidecar_message(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        message_id="9901",
        sender_id=sender_id,
        message_date=sent_at,
        text_value="Birinchi xabar",
    )
    await _insert_sidecar_message(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        message_id="9902",
        sender_id=sender_id,
        message_date=sent_at + 1,
        text_value="Ikkinchi xabar",
    )

    service = TelegramChatMemoryIngestionService()

    first = await service.ingest_due_raw_messages(
        session=db_session,
        workspace_id=workspace.id,
        limit=1,
    )
    second = await service.ingest_due_raw_messages(
        session=db_session,
        workspace_id=workspace.id,
        limit=1,
    )

    assert first.scanned == 1
    assert first.persisted == 1
    assert second.scanned == 1
    assert second.persisted == 1
    assert second.duplicates == 0

    conversation = await db_session.scalar(
        select(Conversation).where(
            Conversation.workspace_id == workspace.id,
            Conversation.telegram_chat_id == int(chat_id),
        )
    )
    assert conversation is not None
    messages = list(
        (
            await db_session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.telegram_message_id.asc())
            )
        ).all()
    )
    assert [message.telegram_message_id for message in messages] == [9901, 9902]


async def test_chat_memory_ingestion_worker_resumes_bounded_raw_projection(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    from app.services.telegram_chat_memory_ingestion_worker import (
        TelegramChatMemoryIngestionWorker,
    )

    await _create_sidecar_tables(db_session)
    chat_id = "445588"
    sender_id = "778811"
    sent_at = int(datetime(2026, 5, 30, 12, 0, tzinfo=UTC).timestamp())
    await _insert_sidecar_peer(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        display_name="Worker Xaridor",
    )
    await _insert_sidecar_message(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        message_id="9911",
        sender_id=sender_id,
        message_date=sent_at,
        text_value="Worker birinchi xabar",
    )
    await _insert_sidecar_message(
        db_session,
        workspace_id=workspace.id,
        chat_id=chat_id,
        message_id="9912",
        sender_id=sender_id,
        message_date=sent_at + 1,
        text_value="Worker ikkinchi xabar",
    )

    @asynccontextmanager
    async def db_factory():
        yield db_session

    worker = TelegramChatMemoryIngestionWorker(
        db_factory=db_factory,
        workspace_ids_provider=lambda: [workspace.id],
        batch_size=1,
    )

    first = await worker.run_once()
    second = await worker.run_once()
    third = await worker.run_once()

    assert first == 1
    assert second == 1
    assert third == 0

    conversation = await db_session.scalar(
        select(Conversation).where(
            Conversation.workspace_id == workspace.id,
            Conversation.telegram_chat_id == int(chat_id),
        )
    )
    assert conversation is not None
    messages = list(
        (
            await db_session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.telegram_message_id.asc())
            )
        ).all()
    )
    assert [message.telegram_message_id for message in messages] == [9911, 9912]
