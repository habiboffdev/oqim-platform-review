"""Sync endpoint tests for the active reconnect contract."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.workspace import Workspace
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationSyncState,
    get_customer_conversation_state,
    set_customer_conversation_state,
)


async def _create_customer(
    db: AsyncSession,
    workspace: Workspace,
    *,
    name: str = "Test Customer",
) -> Customer:
    customer = Customer(workspace_id=workspace.id, display_name=name)
    db.add(customer)
    await db.flush()
    return customer


async def _create_conversation(
    db: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    *,
    telegram_chat_id: int = 100,
) -> Conversation:
    conv = Conversation(
        workspace_id=workspace.id,
        customer_id=customer.id,
        telegram_chat_id=telegram_chat_id,
    )
    db.add(conv)
    await db.flush()
    return conv


class TestSyncSession:
    async def test_returns_projection_delta_contract(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        auth_headers: dict,
    ):
        customer = await _create_customer(db_session, workspace)
        conv = await _create_conversation(db_session, workspace, customer, telegram_chat_id=12345)
        conv.message_sequence = 12
        conv.message_revision = 12
        state = get_customer_conversation_state(conv)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(
                telegram_unread_count=3,
                last_message_text="Route reconnect preview",
            )
        )
        set_customer_conversation_state(conv, state)
        await db_session.flush()

        res = await client.post(
            "/api/sync/session",
            json={
                "server_sequence": 30,
                "last_sequence": 20,
                "active_conversation_id": conv.id,
                "last_seen_conversation_seq": 10,
                "last_seen_conversation_revision": 10,
            },
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["kind"] == "delta"
        assert data["action"] == "refresh_scoped_runtime_delta"
        assert data["conversation_state"] == {
            "last_message_text": "Route reconnect preview",
            "last_message_at": None,
            "unread_count": 3,
            "latest_conversation_seq": 12,
            "latest_conversation_revision": 12,
        }
        assert {
            "name": "messages",
            "mode": "delta",
            "conversation_id": conv.id,
            "after_conversation_seq": 10,
            "latest_conversation_seq": 12,
            "latest_conversation_revision": 12,
        } in data["projections"]
