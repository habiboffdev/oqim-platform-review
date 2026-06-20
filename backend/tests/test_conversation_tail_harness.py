from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.message import Message
from app.models.workspace import Workspace
from app.modules.conversation_core.service import upsert_customer_and_conversation
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationSyncState,
    get_customer_conversation_state,
    set_customer_conversation_state,
)


pytestmark = pytest.mark.asyncio


async def test_conversation_tail_projection_is_shared_by_list_detail_and_messages(
    client: AsyncClient,
    db_session: AsyncSession,
    conversation: Conversation,
    auth_headers: dict,
):
    """Latest preview, unread, and cursors must come from canonical projection state."""
    base_ts = datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc)
    conversation.channel = "telegram_dm"
    conversation.external_chat_id = str(conversation.telegram_chat_id)
    conversation.message_sequence = 2
    conversation.message_revision = 4
    conversation.last_message_at = base_ts + timedelta(minutes=1)

    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="stale local preview",
                telegram_message_id=10,
                telegram_timestamp=base_ts,
                created_at=base_ts,
                conversation_seq=1,
                is_read=False,
            ),
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="fresh projected tail",
                telegram_message_id=11,
                telegram_timestamp=base_ts + timedelta(minutes=1),
                created_at=base_ts + timedelta(minutes=1),
                conversation_seq=2,
                is_read=True,
            ),
        ]
    )

    state = get_customer_conversation_state(conversation)
    state.sync = ConversationSyncState(
        dialog=ConversationDialogState(
            telegram_unread_count=7,
            last_message_text="fresh projected tail",
            last_message_date=(base_ts + timedelta(minutes=1)).isoformat(),
        )
    )
    set_customer_conversation_state(conversation, state)
    db_session.add(conversation)
    await db_session.flush()

    list_res = await client.get("/api/conversations", headers=auth_headers)
    detail_res = await client.get(
        f"/api/conversations/{conversation.id}",
        headers=auth_headers,
    )
    messages_res = await client.get(
        f"/api/conversations/{conversation.id}/messages?limit=50",
        headers=auth_headers,
    )

    assert list_res.status_code == 200
    assert detail_res.status_code == 200
    assert messages_res.status_code == 200

    list_item = list_res.json()["items"][0]
    detail = detail_res.json()
    messages = messages_res.json()

    assert list_item["last_message_text"] == "fresh projected tail"
    assert detail["last_message_text"] == "fresh projected tail"
    assert messages["items"][-1]["content"] == "fresh projected tail"

    assert list_item["unread_count"] == 7
    assert detail["unread_count"] == 7

    assert list_item["latest_conversation_seq"] == 2
    assert detail["latest_conversation_seq"] == 2
    assert messages["latest_conversation_seq"] == 2
    assert list_item["latest_conversation_revision"] == 4
    assert detail["latest_conversation_revision"] == 4
    assert messages["latest_conversation_revision"] == 4


async def test_media_only_tail_preview_is_shared_by_list_detail_and_messages(
    client: AsyncClient,
    db_session: AsyncSession,
    conversation: Conversation,
    auth_headers: dict,
):
    """A media-only Telegram tail is a real tail, not an empty-message state."""
    base_ts = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)
    conversation.channel = "telegram_dm"
    conversation.external_chat_id = str(conversation.telegram_chat_id)
    conversation.message_sequence = 1
    conversation.message_revision = 1
    conversation.last_message_at = base_ts
    db_session.add(conversation)
    db_session.add(
        Message(
            conversation_id=conversation.id,
            sender_type="customer",
            content="",
            media_type="document",
            telegram_message_id=20,
            telegram_timestamp=base_ts,
            created_at=base_ts,
            conversation_seq=1,
            is_read=True,
        )
    )
    await db_session.flush()

    list_res = await client.get("/api/conversations", headers=auth_headers)
    detail_res = await client.get(
        f"/api/conversations/{conversation.id}",
        headers=auth_headers,
    )
    messages_res = await client.get(
        f"/api/conversations/{conversation.id}/messages?limit=50",
        headers=auth_headers,
    )

    assert list_res.status_code == 200
    assert detail_res.status_code == 200
    assert messages_res.status_code == 200

    list_item = list_res.json()["items"][0]
    detail = detail_res.json()
    messages = messages_res.json()

    assert list_item["last_message_text"] == "Fayl"
    assert list_item["tail"]["latest_message_text"] == "Fayl"
    assert detail["last_message_text"] == "Fayl"
    assert detail["tail"]["latest_message_text"] == "Fayl"
    assert messages["items"][-1]["content"] == ""
    assert messages["items"][-1]["media_type"] == "document"
    assert messages["tail"]["latest_message_text"] == "Fayl"


async def test_latest_preview_uses_adapter_order_when_timestamps_tie(
    client: AsyncClient,
    db_session: AsyncSession,
    conversation: Conversation,
    auth_headers: dict,
):
    """Telegram timestamps are second-granular, so tie-break by adapter/message order."""
    base_ts = datetime(2026, 4, 28, 9, 30, tzinfo=timezone.utc)
    conversation.channel = "telegram_dm"
    conversation.external_chat_id = str(conversation.telegram_chat_id)
    conversation.last_message_at = base_ts
    db_session.add(conversation)
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="same-second older telegram id",
                telegram_message_id=700,
                external_message_id="700",
                telegram_timestamp=base_ts,
                created_at=base_ts,
                is_read=True,
            ),
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="same-second real tail",
                telegram_message_id=701,
                external_message_id="701",
                telegram_timestamp=base_ts,
                created_at=base_ts,
                is_read=True,
            ),
        ]
    )
    await db_session.flush()

    list_res = await client.get("/api/conversations", headers=auth_headers)
    detail_res = await client.get(
        f"/api/conversations/{conversation.id}",
        headers=auth_headers,
    )

    assert list_res.status_code == 200
    assert detail_res.status_code == 200
    assert list_res.json()["items"][0]["last_message_text"] == "same-second real tail"
    assert detail_res.json()["last_message_text"] == "same-second real tail"


async def test_legacy_dm_conversation_is_reused_instead_of_duplicated(
    db_session: AsyncSession,
    workspace: Workspace,
):
    customer = Customer(
        workspace_id=workspace.id,
        display_name="Dadam",
        channel="telegram_dm",
        external_id="123456",
    )
    db_session.add(customer)
    await db_session.flush()
    legacy_conversation = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        channel="dm",
        external_chat_id="123456",
        telegram_chat_id=None,
        last_message_at=datetime(2026, 4, 18, tzinfo=timezone.utc),
    )
    db_session.add(legacy_conversation)
    await db_session.flush()

    upserted_customer, conversation = await upsert_customer_and_conversation(
        db_session,
        workspace_id=workspace.id,
        telegram_chat_id=123456,
        external_chat_id="123456",
        display_name="Dadam",
        channel="telegram_dm",
    )
    await db_session.flush()

    assert upserted_customer.id == customer.id
    assert upserted_customer.telegram_id == 123456
    assert conversation.id == legacy_conversation.id
    assert conversation.channel == "telegram_dm"
    assert conversation.telegram_chat_id == 123456

    rows = (
        await db_session.execute(
            select(Conversation).where(
                Conversation.workspace_id == workspace.id,
                Conversation.external_chat_id == "123456",
            )
        )
    ).scalars().all()
    assert [row.id for row in rows] == [legacy_conversation.id]


async def test_missing_middle_window_is_returned_as_explicit_history_gap(
    client: AsyncClient,
    db_session: AsyncSession,
    conversation: Conversation,
    auth_headers: dict,
):
    base_ts = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)
    conversation.channel = "telegram_dm"
    conversation.external_chat_id = str(conversation.telegram_chat_id)
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="older edge",
                telegram_message_id=100,
                external_message_id="100",
                telegram_timestamp=base_ts,
                created_at=base_ts,
            ),
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="newer edge",
                telegram_message_id=140,
                external_message_id="140",
                telegram_timestamp=base_ts + timedelta(minutes=1),
                created_at=base_ts + timedelta(minutes=1),
            ),
        ]
    )
    await db_session.flush()

    res = await client.get(
        f"/api/conversations/{conversation.id}/messages?limit=50",
        headers=auth_headers,
    )

    assert res.status_code == 200
    body = res.json()
    assert [item["telegram_message_id"] for item in body["items"]] == [100, 140]
    assert body["history_gap"] == {
        "reason": "visible_telegram_id_gap",
        "before_external_message_id": "140",
        "after_external_message_id": None,
    }


async def test_older_history_pages_by_explicit_cursor_without_cross_chat_blending(
    client: AsyncClient,
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    auth_headers: dict,
):
    base_ts = datetime(2026, 4, 28, 11, 0, tzinfo=timezone.utc)
    conversation.channel = "telegram_dm"
    conversation.external_chat_id = str(conversation.telegram_chat_id)

    other_conversation = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        channel="telegram_dm",
        telegram_chat_id=987654321,
        external_chat_id="987654321",
        last_message_at=base_ts,
    )
    db_session.add(other_conversation)
    await db_session.flush()

    for index, telegram_id in enumerate([201, 202, 203]):
        message = Message(
            conversation_id=conversation.id,
            sender_type="customer",
            content=f"own-{telegram_id}",
            telegram_message_id=telegram_id,
            telegram_timestamp=base_ts + timedelta(minutes=index),
            created_at=base_ts + timedelta(minutes=index),
        )
        db_session.add(message)
    db_session.add(
        Message(
            conversation_id=other_conversation.id,
            sender_type="customer",
            content="other-chat-should-not-appear",
            telegram_message_id=999,
            telegram_timestamp=base_ts - timedelta(minutes=1),
            created_at=base_ts - timedelta(minutes=1),
        )
    )
    await db_session.flush()

    first_page = await client.get(
        f"/api/conversations/{conversation.id}/messages?limit=2",
        headers=auth_headers,
    )
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert [item["content"] for item in first_body["items"]] == ["own-202", "own-203"]
    assert first_body["has_older"] is True

    before_id = first_body["items"][0]["id"]
    older_page = await client.get(
        f"/api/conversations/{conversation.id}/messages?limit=2&before_id={before_id}",
        headers=auth_headers,
    )
    assert older_page.status_code == 200
    older_body = older_page.json()
    assert [item["content"] for item in older_body["items"]] == ["own-201"]
    assert "other-chat-should-not-appear" not in {
        item["content"] for item in older_body["items"]
    }
