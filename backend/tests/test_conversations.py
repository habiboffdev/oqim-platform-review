"""
Conversations endpoint tests — list, detail, messages, send, update, mark-read.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_hydration_runtime import ConversationHydrationRuntime
from app.models.customer import Customer
from app.models.delivery_runtime import DeliveryRuntime
from app.models.message import Message
from app.models.message_insight import MessageInsight
from app.models.workspace import Workspace
from app.services.channel_conversation_sync import ConversationSyncResult
from app.services.conversation_hydration_worker import ConversationHydrationWorker
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationSyncState,
    ConversationSyncWatermarks,
    get_customer_conversation_state,
    set_customer_conversation_state,
)


class TestListConversations:
    async def test_list_empty(self, client: AsyncClient, workspace: Workspace, auth_headers: dict):
        res = await client.get("/api/conversations", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["items"] == []
        assert body["next_cursor"] is None

    async def test_list_returns_conversations(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        res = await client.get("/api/conversations", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        data = body["items"]
        assert len(data) == 1
        assert data[0]["id"] == conversation.id
        assert data[0]["pipeline_stage"] == "new"
        assert data[0]["override_mode"] == "auto"
        assert data[0]["customer_name"] == "Alisher Valiev"
        assert body["next_cursor"] is None

    async def test_list_includes_unread_count(
        self,
        client: AsyncClient,
        conversation: Conversation,
        message: Message,
        auth_headers: dict,
    ):
        """Unread customer messages should be counted."""
        res = await client.get("/api/conversations", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()["items"]
        assert len(data) == 1
        assert data[0]["unread_count"] == 1

    async def test_list_prefers_telegram_dialog_unread_from_state(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        message: Message,
        auth_headers: dict,
    ):
        state = get_customer_conversation_state(conversation)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(telegram_unread_count=7)
        )
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get("/api/conversations", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()["items"]
        assert len(data) == 1
        assert data[0]["unread_count"] == 7

    async def test_list_prefers_dialog_preview_from_canonical_state_when_newer(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        local_at = datetime(2026, 4, 20, 8, 0, tzinfo=UTC)
        projected_at = datetime(2026, 4, 20, 8, 5, tzinfo=UTC)
        db_session.add(
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="Old local preview",
                telegram_timestamp=local_at,
                created_at=local_at,
            )
        )
        state = get_customer_conversation_state(conversation)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(
                telegram_unread_count=2,
                last_message_text="Fresh Telegram preview",
                last_message_date=projected_at.isoformat(),
            )
        )
        conversation.summary = "Stale summary fallback"
        conversation.last_message_at = projected_at
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get("/api/conversations", headers=auth_headers)

        assert res.status_code == 200
        data = res.json()["items"]
        assert len(data) == 1
        assert data[0]["last_message_text"] == "Fresh Telegram preview"
        assert data[0]["unread_count"] == 2
        assert data[0]["tail"]["schema_version"] == "conversation_tail.v1"
        assert data[0]["tail"]["status"] == "stale"
        assert data[0]["tail"]["source"] == "dialog_projection"
        assert data[0]["tail"]["unread_source"] == "dialog_projection"
        assert data[0]["tail"]["gap"]["reason"] == "conversation_preview_ahead"

    async def test_list_uses_projected_dialog_timestamp_for_order_and_public_tail(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        local_old_at = datetime(2026, 4, 20, 8, 0, tzinfo=UTC)
        local_new_at = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
        projected_at = datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
        conv_dialog = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=88001,
            pipeline_stage="new",
            last_message_at=local_old_at,
        )
        conv_local = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=88002,
            pipeline_stage="new",
            last_message_at=local_new_at,
        )
        state = get_customer_conversation_state(conv_dialog)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(
                telegram_unread_count=3,
                last_message_text="Telegram projection owns newest tail",
                last_message_date=projected_at.isoformat(),
            )
        )
        set_customer_conversation_state(conv_dialog, state)
        db_session.add_all([conv_dialog, conv_local])
        await db_session.flush()
        db_session.add_all([
            Message(
                conversation_id=conv_dialog.id,
                sender_type="customer",
                content="Old local row",
                telegram_timestamp=local_old_at,
                created_at=local_old_at,
            ),
            Message(
                conversation_id=conv_local.id,
                sender_type="customer",
                content="Newer local row",
                telegram_timestamp=local_new_at,
                created_at=local_new_at,
            ),
        ])
        await db_session.flush()

        res = await client.get("/api/conversations?limit=2", headers=auth_headers)

        assert res.status_code == 200
        data = res.json()["items"]
        assert [item["id"] for item in data] == [conv_dialog.id, conv_local.id]
        assert data[0]["last_message_text"] == "Telegram projection owns newest tail"
        assert datetime.fromisoformat(data[0]["last_message_at"].replace("Z", "+00:00")) == projected_at
        assert data[0]["tail"]["latest_message_at"] == data[0]["last_message_at"]

    async def test_list_tail_projection_marks_local_tail_ok(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        local_at = datetime(2026, 4, 20, 8, 0, tzinfo=UTC)
        conversation.last_message_at = local_at
        conversation.message_sequence = 3
        conversation.message_revision = 3
        db_session.add(
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="Local latest",
                telegram_timestamp=local_at,
                created_at=local_at,
                conversation_seq=3,
            )
        )
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get("/api/conversations", headers=auth_headers)

        assert res.status_code == 200
        tail = res.json()["items"][0]["tail"]
        assert tail["status"] == "ok"
        assert tail["source"] == "local_message"
        assert tail["latest_message_text"] == "Local latest"
        assert tail["latest_conversation_seq"] == 3
        assert tail["latest_conversation_revision"] == 3
        assert tail["gap"] is None

    async def test_filter_by_stage(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        state = get_customer_conversation_state(conversation)
        state.pipeline_stage = "qualified"
        conversation.pipeline_stage = "new"
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get("/api/conversations?stage=qualified", headers=auth_headers)
        assert res.status_code == 200
        assert len(res.json()["items"]) == 1

        res = await client.get("/api/conversations?stage=new", headers=auth_headers)
        assert res.status_code == 200
        assert len(res.json()["items"]) == 0

    async def test_filter_by_stage_normalizes_legacy_aliases_in_sql_projection(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        state = get_customer_conversation_state(conversation)
        conversation.pipeline_stage = "cold"
        set_customer_conversation_state(conversation, state)
        conversation.crm_state["pipeline_stage"] = "talking"
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get("/api/conversations?stage=qualified", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["crm_stage"]["stage"] == "qualified"

        res = await client.get("/api/conversations?stage=talking", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["items"] == []

    async def test_filter_by_stage_no_match(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        res = await client.get("/api/conversations?stage=negotiation", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["items"] == []

    async def test_filter_by_stage_uses_canonical_default_when_state_missing(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        conversation.crm_state = None
        conversation.pipeline_stage = "qualified"
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get("/api/conversations?stage=qualified", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["items"] == []

        res = await client.get("/api/conversations?stage=new", headers=auth_headers)
        assert res.status_code == 200
        assert len(res.json()["items"]) == 1

    async def test_workspace_isolation(
        self, client: AsyncClient, conversation: Conversation, auth_headers_b: dict
    ):
        """Workspace B should NOT see workspace A's conversations."""
        res = await client.get("/api/conversations", headers=auth_headers_b)
        assert res.status_code == 200
        assert res.json()["items"] == []

    async def test_requires_auth(self, client: AsyncClient):
        res = await client.get("/api/conversations")
        assert res.status_code == 401

    async def test_ordered_by_last_message_at_desc(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """Conversations should be ordered by last_message_at descending."""
        now = datetime.now(UTC)
        conv_old = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            telegram_chat_id=111,
            pipeline_stage="new",
            last_message_at=now - timedelta(hours=2),
        )
        conv_new = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            telegram_chat_id=222,
            pipeline_stage="qualified",
            last_message_at=now,
        )
        db_session.add_all([conv_old, conv_new])
        await db_session.flush()

        res = await client.get("/api/conversations", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()["items"]
        assert len(data) == 2
        # Most recent first
        assert data[0]["id"] == conv_new.id
        assert data[1]["id"] == conv_old.id

    async def test_list_includes_crm_snapshot_from_canonical_state(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        state = get_customer_conversation_state(conversation)
        state.pipeline_stage = "qualified"
        state.last_intent = "product_question"
        state.urgency = True
        state.__pydantic_extra__ = {"lead_score": 4.2, "media_ready": False}
        state.field_provenance["media_ready"] = "system"
        conversation.pipeline_stage = "new"
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get("/api/conversations", headers=auth_headers)

        assert res.status_code == 200
        data = res.json()["items"]
        assert data[0]["pipeline_stage"] == "qualified"
        assert data[0]["crm_snapshot"]["pipeline_stage"] == "qualified"
        assert data[0]["crm_snapshot"]["lead_score"] == 4.2
        assert data[0]["crm_snapshot"]["last_intent"] == "product_question"
        assert data[0]["crm_snapshot"]["urgency"] is True
        assert data[0]["crm_snapshot"]["media_ready"] is False
        assert data[0]["crm_stage"]["schema_version"] == "crm_stage.v1"
        assert data[0]["crm_stage"]["stage"] == "qualified"
        assert data[0]["crm_stage"]["source"] == "crm_state"
        assert data[0]["crm_stage"]["confidence"] == 4.2

    async def test_pipeline_projection_groups_by_backend_crm_stage_contract(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        state = get_customer_conversation_state(conversation)
        state.last_intent = "asked_price"
        state.__pydantic_extra__ = {"lead_score": 0.82}
        conversation.pipeline_stage = "cold"
        set_customer_conversation_state(conversation, state)
        conversation.crm_state["pipeline_stage"] = "talking"
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get("/api/conversations/pipeline", headers=auth_headers)

        assert res.status_code == 200
        body = res.json()
        assert body["schema_version"] == "crm_pipeline.v1"
        qualified = next(stage for stage in body["stages"] if stage["stage"] == "qualified")
        assert qualified["count"] == 1
        card = qualified["cards"][0]
        assert card["conversation_id"] == conversation.id
        assert card["stage"]["stage"] == "qualified"
        assert card["stage"]["raw_stage"] == "talking"
        assert card["stage"]["normalized_from"] == "talking"
        assert card["stage"]["source"] == "crm_state"
        assert card["stage"]["confidence"] == 0.82

    async def test_list_includes_next_best_action_from_canonical_state(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        state = get_customer_conversation_state(conversation)
        state.reply = state.reply.model_copy(
            update={
                "latest_unresolved_customer_message_id": 123,
                "unresolved_customer_message_ids": [123],
                "seller_responded_after_latest_customer": False,
                "seller_response_message_id": None,
            },
        ) if state.reply else None
        if state.reply is None:
            from app.services.conversation_state import ConversationReplyState
            state.reply = ConversationReplyState(
                latest_unresolved_customer_message_id=123,
                unresolved_customer_message_ids=[123],
                seller_responded_after_latest_customer=False,
                seller_response_message_id=None,
            )
        state.__pydantic_extra__ = {"media_ready": False}
        state.field_provenance["media_ready"] = "system"
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get("/api/conversations", headers=auth_headers)

        assert res.status_code == 200
        data = res.json()["items"]
        assert data[0]["next_best_action"] == {
            "action": "reply_to_customer",
            "ready": False,
            "reason": "waiting_on_media_hydration",
        }


class TestConversationPagination:
    """Cursor-based pagination for GET /api/conversations."""

    async def _create_conversations(
        self, db_session: AsyncSession, workspace: Workspace, customer: Customer, count: int = 5
    ) -> list[Conversation]:
        """Create `count` conversations with distinct last_message_at timestamps."""
        base = datetime(2026, 3, 1, tzinfo=UTC)
        convs = []
        for i in range(count):
            conv = Conversation(
                workspace_id=workspace.id,
                customer_id=customer.id,
                telegram_chat_id=900000 + i,
                pipeline_stage="new" if i % 2 == 0 else "qualified",
                last_message_at=base + timedelta(hours=i + 1),
            )
            state = get_customer_conversation_state(conv)
            state.pipeline_stage = "new" if i % 2 == 0 else "qualified"
            set_customer_conversation_state(conv, state)
            db_session.add(conv)
            convs.append(conv)
        await db_session.flush()
        return convs

    async def test_pagination_default_limit(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """GET /api/conversations with no params returns at most 50 items and a next_cursor field."""
        await self._create_conversations(db_session, workspace, customer, count=5)
        res = await client.get("/api/conversations", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert "items" in body
        assert "next_cursor" in body
        assert len(body["items"]) == 5
        assert body["next_cursor"] is None  # 5 < 50 default, no more pages

    async def test_pagination_with_limit(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """GET /api/conversations?limit=2 returns exactly 2 items when more exist, with non-null next_cursor."""
        await self._create_conversations(db_session, workspace, customer, count=5)
        res = await client.get("/api/conversations?limit=2", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["items"]) == 2
        assert body["next_cursor"] is not None

    async def test_pagination_cursor_navigation(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """GET /api/conversations?limit=2&cursor={next_cursor} returns the next 2 items."""
        await self._create_conversations(db_session, workspace, customer, count=5)

        # Page 1
        res1 = await client.get("/api/conversations?limit=2", headers=auth_headers)
        body1 = res1.json()
        assert len(body1["items"]) == 2
        cursor = body1["next_cursor"]
        assert cursor is not None

        # Page 2
        res2 = await client.get(f"/api/conversations?limit=2&cursor={cursor}", headers=auth_headers)
        body2 = res2.json()
        assert len(body2["items"]) == 2
        # No overlap between pages
        page1_ids = {item["id"] for item in body1["items"]}
        page2_ids = {item["id"] for item in body2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

        # Page 3 (should have 1 remaining)
        cursor2 = body2["next_cursor"]
        assert cursor2 is not None
        res3 = await client.get(f"/api/conversations?limit=2&cursor={cursor2}", headers=auth_headers)
        body3 = res3.json()
        assert len(body3["items"]) == 1
        assert body3["next_cursor"] is None  # last page

    async def test_pagination_all_fit_in_one_page(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """When all items fit in one page, next_cursor is null."""
        await self._create_conversations(db_session, workspace, customer, count=3)
        res = await client.get("/api/conversations?limit=10", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["items"]) == 3
        assert body["next_cursor"] is None

    async def test_pagination_with_stage_filter(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """GET /api/conversations?stage=qualified&limit=10 applies filter AND pagination together."""
        await self._create_conversations(db_session, workspace, customer, count=5)
        # count=5 creates: indices 0,2,4 as "new" and 1,3 as "qualified"
        res = await client.get("/api/conversations?stage=qualified&limit=10", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert all(item["pipeline_stage"] == "qualified" for item in body["items"])
        assert len(body["items"]) == 2

    async def test_cursor_is_iso8601_timestamp(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """Cursor is based on last_message_at (ISO 8601 string), not offset."""
        await self._create_conversations(db_session, workspace, customer, count=5)
        res = await client.get("/api/conversations?limit=2", headers=auth_headers)
        body = res.json()
        cursor = body["next_cursor"]
        assert cursor is not None
        # Should parse as ISO 8601
        parsed = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        assert isinstance(parsed, datetime)


class TestGetConversation:
    async def test_get_detail(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        res = await client.get(
            f"/api/conversations/{conversation.id}", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == conversation.id
        assert data["customer_id"] == conversation.customer_id
        assert data["customer_name"] == "Alisher Valiev"
        assert data["pipeline_stage"] == "new"
        assert data["override_mode"] == "auto"
        assert data["needs_attention"] is False
        assert data["latest_action"] is None
        assert data["crm_snapshot"]["pipeline_stage"] == "new"
        assert data["crm_snapshot"]["needs_attention"] is False
        assert "created_at" in data

    async def test_get_includes_unread_count(
        self,
        client: AsyncClient,
        conversation: Conversation,
        message: Message,
        auth_headers: dict,
    ):
        res = await client.get(
            f"/api/conversations/{conversation.id}", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.json()["unread_count"] == 1

    async def test_get_detail_prefers_dialog_preview_from_canonical_state_when_newer(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        local_at = datetime(2026, 4, 20, 8, 0, tzinfo=UTC)
        projected_at = datetime(2026, 4, 20, 8, 5, tzinfo=UTC)
        db_session.add(
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="Old detail preview",
                telegram_timestamp=local_at,
                created_at=local_at,
            )
        )
        state = get_customer_conversation_state(conversation)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(
                last_message_text="Fresh detail projection",
                last_message_date=projected_at.isoformat(),
            )
        )
        conversation.last_message_at = projected_at
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        detail = await client.get(
            f"/api/conversations/{conversation.id}",
            headers=auth_headers,
        )
        by_chat = await client.get(
            f"/api/conversations/by-telegram-chat/{conversation.telegram_chat_id}",
            headers=auth_headers,
        )

        assert detail.status_code == 200
        assert by_chat.status_code == 200
        assert detail.json()["last_message_text"] == "Fresh detail projection"
        assert by_chat.json()["last_message_text"] == "Fresh detail projection"

    async def test_by_telegram_chat_uses_canonical_state_read_model(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        state = get_customer_conversation_state(conversation)
        state.pipeline_stage = "qualified"
        state.products_interested = ["iPhone 15 Pro"]
        state.last_intent = "price_inquiry"
        conversation.pipeline_stage = "new"
        conversation.products_mentioned = ["Legacy product"]
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/by-telegram-chat/{conversation.telegram_chat_id}",
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["pipeline_stage"] == "qualified"
        assert data["products_mentioned"] == ["iPhone 15 Pro"]
        assert data["crm_snapshot"]["pipeline_stage"] == "qualified"
        assert data["crm_snapshot"]["last_intent"] == "price_inquiry"

    async def test_get_crm_snapshot_reads_structured_state(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        state = get_customer_conversation_state(conversation)
        state.pipeline_stage = "qualified"
        state.lead_score = 4.5
        state.last_intent = "price_inquiry"
        state.products_interested = ["iPhone 15 Pro", "AirPods"]
        state.urgency = True
        state.last_updated = "2026-04-16T10:00:00+00:00"
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(telegram_unread_count=3)
        )
        conversation.products_mentioned = ["Old product"]
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}",
            headers=auth_headers,
        )

        assert res.status_code == 200
        snapshot = res.json()["crm_snapshot"]
        assert res.json()["pipeline_stage"] == "qualified"
        assert res.json()["products_mentioned"] == ["iPhone 15 Pro", "AirPods"]
        assert snapshot["pipeline_stage"] == "qualified"
        assert snapshot["lead_score"] == 4.5
        assert snapshot["last_intent"] == "price_inquiry"
        assert snapshot["products_interested"] == ["iPhone 15 Pro", "AirPods"]
        assert snapshot["urgency"] is True
        assert snapshot["last_updated"] == "2026-04-16T10:00:00Z"

    async def test_get_crm_snapshot_ignores_latest_insight_drift(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        state = get_customer_conversation_state(conversation)
        state.pipeline_stage = "qualified"
        state.lead_score = 4.5
        state.last_intent = "price_inquiry"
        state.urgency = True
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        db_session.add(
            MessageInsight(
                conversation_id=conversation.id,
                workspace_id=conversation.workspace_id,
                intent="old_intent",
                lead_score=1.0,
                urgency=False,
            )
        )
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}",
            headers=auth_headers,
        )

        assert res.status_code == 200
        snapshot = res.json()["crm_snapshot"]
        assert snapshot["pipeline_stage"] == "qualified"
        assert snapshot["lead_score"] == 4.5
        assert snapshot["last_intent"] == "price_inquiry"
        assert snapshot["urgency"] is True

    async def test_get_crm_snapshot_exposes_media_ready_from_canonical_state(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Sprint 7 #193: product-facing readers observe media readiness
        through canonical AI CRM state without rebuilding their own view."""
        state = get_customer_conversation_state(conversation)
        state.__pydantic_extra__ = {"media_ready": False}
        state.field_provenance["media_ready"] = "system"
        set_customer_conversation_state(conversation, state)
        db_session.add(conversation)
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}",
            headers=auth_headers,
        )

        assert res.status_code == 200
        snapshot = res.json()["crm_snapshot"]
        assert snapshot["media_ready"] is False

    async def test_get_crm_snapshot_media_ready_defaults_to_none(
        self,
        client: AsyncClient,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Conversations with no AI-relevant media report media_ready=None —
        downstream consumers distinguish it from a ready/not-ready decision."""
        res = await client.get(
            f"/api/conversations/{conversation.id}",
            headers=auth_headers,
        )

        assert res.status_code == 200
        snapshot = res.json()["crm_snapshot"]
        assert snapshot["media_ready"] is None

    async def test_get_next_best_action_projects_reply_when_unresolved_tail(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Sprint 7 #194: conversation detail exposes the next-best-action
        projection alongside CRM snapshot. Unresolved tail → reply_to_customer."""
        customer_msg = Message(
            conversation_id=conversation.id,
            sender_type="customer",
            content="Narxi qancha?",
        )
        db_session.add(customer_msg)
        await db_session.flush()
        from app.services.conversation_state import refresh_customer_conversation_state
        await refresh_customer_conversation_state(conversation, messages=[customer_msg])
        db_session.add(conversation)
        await db_session.commit()

        res = await client.get(
            f"/api/conversations/{conversation.id}",
            headers=auth_headers,
        )

        assert res.status_code == 200
        nba = res.json()["next_best_action"]
        assert nba["action"] == "reply_to_customer"
        assert nba["ready"] is True
        assert nba["reason"] == "unresolved_customer_tail"

    async def test_get_next_best_action_reports_settled_on_empty_conversation(
        self,
        client: AsyncClient,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Fresh conversation, no messages yet → conversation_settled."""
        res = await client.get(
            f"/api/conversations/{conversation.id}",
            headers=auth_headers,
        )

        assert res.status_code == 200
        nba = res.json()["next_best_action"]
        assert nba["action"] == "conversation_settled"
        assert nba["ready"] is True

    async def test_get_nonexistent_404(self, client: AsyncClient, auth_headers: dict):
        res = await client.get("/api/conversations/99999", headers=auth_headers)
        assert res.status_code == 404

    async def test_get_other_workspace_404(
        self, client: AsyncClient, conversation: Conversation, auth_headers_b: dict
    ):
        """Cannot access another workspace's conversation."""
        res = await client.get(
            f"/api/conversations/{conversation.id}", headers=auth_headers_b
        )
        assert res.status_code == 404

    async def test_get_requires_auth(self, client: AsyncClient, conversation: Conversation):
        res = await client.get(f"/api/conversations/{conversation.id}")
        assert res.status_code == 401

    async def test_latest_action_field_is_always_none(
        self,
        client: AsyncClient,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """latest_action was removed in P1.2. Field must exist but always be None."""
        res = await client.get(
            f"/api/conversations/{conversation.id}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert "latest_action" in data
        assert data["latest_action"] is None


class TestListMessages:
    async def test_list_messages(
        self,
        client: AsyncClient,
        conversation: Conversation,
        message: Message,
        auth_headers: dict,
    ):
        res = await client.get(
            f"/api/conversations/{conversation.id}/messages", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert "has_older" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["content"] == "Salom! iPhone 15 bormi?"
        assert data["items"][0]["sender_type"] == "customer"
        assert data["has_older"] is False

    async def test_list_messages_exposes_conversation_cursor(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        conversation.message_sequence = 2
        conversation.message_revision = 2
        db_session.add_all([
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="Birinchi",
                conversation_seq=1,
            ),
            Message(
                conversation_id=conversation.id,
                sender_type="seller",
                content="Ikkinchi",
                conversation_seq=2,
            ),
        ])
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages",
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["latest_conversation_seq"] == 2
        assert data["latest_conversation_revision"] == 2
        assert [item["conversation_seq"] for item in data["items"]] == [1, 2]

    async def test_list_messages_exposes_delivery_runtime_projection(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        message = Message(
            conversation_id=conversation.id,
            sender_type="seller",
            content="Ha, bor",
            client_message_uuid="send-key-api",
            delivery_state="confirmed",
            conversation_seq=1,
        )
        db_session.add(message)
        await db_session.flush()
        db_session.add(
            DeliveryRuntime(
                workspace_id=conversation.workspace_id,
                conversation_id=conversation.id,
                message_id=message.id,
                channel="telegram_dm",
                channel_conversation_id=str(conversation.telegram_chat_id),
                client_idempotency_key="send-key-api",
                state="reconciled",
                attempt_count=2,
                external_message_id="9001",
                last_error="sidecar_timeout",
            )
        )
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages",
            headers=auth_headers,
        )

        assert res.status_code == 200
        item = res.json()["items"][0]
        assert item["delivery_state"] == "confirmed"
        assert item["delivery_runtime"]["schema_version"] == "delivery_runtime.v1"
        assert item["delivery_runtime"]["state"] == "reconciled"
        assert item["delivery_runtime"]["customer_status"] == "sent"
        assert item["delivery_runtime"]["next_action"] == "none"
        assert item["delivery_runtime"]["attempt_count"] == 2
        assert item["delivery_runtime"]["max_attempts"] == 3
        assert item["delivery_runtime"]["retry_budget_remaining"] == 1
        assert item["delivery_runtime"]["external_message_id"] == "9001"
        assert item["delivery_runtime"]["last_error"] == "sidecar_timeout"

    async def test_list_messages_after_conversation_seq_returns_only_newer_tail(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        conversation.message_sequence = 3
        conversation.message_revision = 3
        db_session.add_all([
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="Birinchi",
                conversation_seq=1,
            ),
            Message(
                conversation_id=conversation.id,
                sender_type="seller",
                content="Ikkinchi",
                conversation_seq=2,
            ),
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="Uchinchi",
                conversation_seq=3,
            ),
        ])
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages?after_conversation_seq=1",
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["has_older"] is False
        assert data["latest_conversation_seq"] == 3
        assert data["latest_conversation_revision"] == 3
        assert [item["conversation_seq"] for item in data["items"]] == [2, 3]
        assert data["tail"]["schema_version"] == "conversation_tail.v1"
        assert data["tail"]["status"] == "ok"
        assert data["tail"]["source"] == "local_message"
        assert data["tail"]["latest_message_text"] == "Uchinchi"
        assert data["tail"]["latest_conversation_seq"] == 3

    async def test_list_messages_empty(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        res = await client.get(
            f"/api/conversations/{conversation.id}/messages", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.json()
        assert data["items"] == []
        assert data["has_older"] is False
        assert data["tail"]["source"] == "none"
        assert data["tail"]["status"] == "stale"
        assert data["tail"]["gap"]["reason"] == "conversation_preview_ahead"

    async def test_messages_ordered_by_created_at(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Messages should be ordered by created_at ascending."""
        msg1 = Message(
            conversation_id=conversation.id,
            sender_type="customer",
            content="First message",
        )
        db_session.add(msg1)
        await db_session.flush()

        msg2 = Message(
            conversation_id=conversation.id,
            sender_type="seller",
            content="Second message",
        )
        db_session.add(msg2)
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["content"] == "First message"
        assert data["items"][1]["content"] == "Second message"

    async def test_deleted_messages_remain_visible_as_tombstones(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        db_session.add(
            Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content="[deleted]",
                is_deleted=True,
            )
        )
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages",
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["content"] == "[deleted]"
        assert data["items"][0]["is_deleted"] is True

    async def test_messages_nonexistent_conversation_404(
        self, client: AsyncClient, auth_headers: dict
    ):
        res = await client.get(
            "/api/conversations/99999/messages", headers=auth_headers
        )
        assert res.status_code == 404

    async def test_messages_other_workspace_404(
        self, client: AsyncClient, conversation: Conversation, auth_headers_b: dict
    ):
        """Cannot list messages from another workspace's conversation."""
        res = await client.get(
            f"/api/conversations/{conversation.id}/messages", headers=auth_headers_b
        )
        assert res.status_code == 404

    async def test_messages_requires_auth(
        self, client: AsyncClient, conversation: Conversation
    ):
        res = await client.get(f"/api/conversations/{conversation.id}/messages")
        assert res.status_code == 401

    async def test_pagination_has_older(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """has_older should be True when more messages exist beyond the limit."""
        for i in range(5):
            db_session.add(Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content=f"Message {i}",
            ))
            await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages?limit=3",
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) == 3
        assert data["has_older"] is True

    async def test_pagination_before_id(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """before_id cursor returns only messages with id less than cursor."""
        msgs = []
        for i in range(5):
            m = Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content=f"Message {i}",
            )
            db_session.add(m)
            await db_session.flush()
            msgs.append(m)

        cursor_id = msgs[3].id  # Use 4th message as cursor
        res = await client.get(
            f"/api/conversations/{conversation.id}/messages?before_id={cursor_id}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        # Should get messages 0, 1, 2 (all with id < cursor_id)
        assert all(item["id"] < cursor_id for item in data["items"])

    async def test_pagination_before_id_uses_id_tiebreaker_for_same_timestamp(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Messages with identical timestamps should still paginate deterministically."""
        shared_ts = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
        msgs = []
        for i in range(5):
            message = Message(
                conversation_id=conversation.id,
                sender_type="customer",
                content=f"same-ts {i}",
                created_at=shared_ts,
            )
            db_session.add(message)
            await db_session.flush()
            msgs.append(message)

        cursor_id = msgs[3].id
        res = await client.get(
            f"/api/conversations/{conversation.id}/messages?before_id={cursor_id}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert [item["id"] for item in data["items"]] == [msgs[0].id, msgs[1].id, msgs[2].id]

    async def test_messages_order_same_timestamp_by_telegram_message_id(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Telegram albums often share one timestamp; channel ids define order."""
        conversation.channel = "telegram_dm"
        conversation.telegram_chat_id = 7314053423
        conversation.external_chat_id = "7314053423"
        shared_ts = datetime(2026, 4, 24, 14, 9, 5, tzinfo=UTC)
        for telegram_id in [315828, 315826, 315830, 315829]:
            db_session.add(
                Message(
                    conversation_id=conversation.id,
                    channel="telegram_dm",
                    sender_type="customer",
                    content=f"media {telegram_id}",
                    telegram_message_id=telegram_id,
                    external_message_id=str(telegram_id),
                    telegram_timestamp=shared_ts,
                    is_read=True,
                )
            )
            await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages?limit=50",
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert [item["telegram_message_id"] for item in data["items"]] == [
            315826,
            315828,
            315829,
            315830,
        ]

    async def test_pagination_before_id_skips_remote_fetch_when_oldest_watermark_complete(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        oldest = Message(
            conversation_id=conversation.id,
            channel="telegram_dm",
            sender_type="customer",
            content="Current oldest",
            telegram_message_id=500,
            external_message_id="500",
            telegram_timestamp=datetime.now(UTC),
            is_read=True,
        )
        state = get_customer_conversation_state(conversation)
        state.sync = ConversationSyncState(
            watermarks=ConversationSyncWatermarks(
                oldest_external_message_id="500",
                latest_external_message_id="500",
                oldest_complete=True,
                latest_complete=True,
            )
        )
        set_customer_conversation_state(conversation, state)
        db_session.add(oldest)
        db_session.add(conversation)
        await db_session.flush()

        with patch(
            "app.services.channel_conversation_sync.ChannelConversationSync.sync_conversation",
            new=AsyncMock(),
        ) as mock_sync:
            res = await client.get(
                f"/api/conversations/{conversation.id}/messages?before_id={oldest.id}",
                headers=auth_headers,
            )

        assert res.status_code == 200
        data = res.json()
        assert data["items"] == []
        assert data["has_older"] is False
        mock_sync.assert_not_called()

    async def test_messages_do_not_repair_empty_page(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """Normal message reads stay side-effect-free."""

        conv = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=777003,
            external_chat_id="777003",
        )
        db_session.add(conv)
        await db_session.flush()

        with patch(
            "app.services.channel_conversation_sync.ChannelConversationSync.sync_conversation",
            new=AsyncMock(),
        ) as mock_sync:
            res = await client.get(
                f"/api/conversations/{conv.id}/messages",
                headers=auth_headers,
            )

        assert res.status_code == 200
        assert res.json()["items"] == []
        assert "x-oqim-repair-attempts" not in res.headers
        assert "x-oqim-repair-reasons" not in res.headers
        mock_sync.assert_not_called()

    async def test_hydrate_conversation_marks_read_without_route_time_history_fetch(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        conv = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=777031,
            external_chat_id="777031",
        )
        db_session.add(conv)
        await db_session.flush()

        unread = Message(
            conversation_id=conv.id,
            channel="telegram_dm",
            sender_type="customer",
            content="old unread",
            telegram_message_id=10,
            external_message_id="10",
            telegram_timestamp=datetime.now(UTC),
            is_read=False,
        )
        db_session.add(unread)
        await db_session.flush()

        with (
            patch(
                "app.services.channel_conversation_sync.ChannelConversationSync.sync_conversation",
                new=AsyncMock(),
            ) as mock_sync,
            patch(
                "app.api.routes.conversation_commands.get_channel_adapter",
            ) as get_adapter,
        ):
            adapter = SimpleNamespace(
                capabilities=lambda: SimpleNamespace(mark_read=True),
                mark_read=AsyncMock(),
            )
            get_adapter.return_value = adapter
            res = await client.post(
                f"/api/conversations/{conv.id}/hydrate",
                headers=auth_headers,
                json={"limit": 100},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["requested"] == 0
        assert data["persisted"] == 0
        assert data["duplicates"] == 0
        assert data["unread_count"] == 0
        assert data["sync_status"] == "idle"
        assert "deprecated" not in data
        assert data["hydration"]["state"] == "idle"
        assert data["hydration"]["needed"] is False
        assert data["tail"]["schema_version"] == "conversation_tail.v1"
        mock_sync.assert_not_called()
        adapter.mark_read.assert_awaited_once_with(
            workspace_id=workspace.id,
            conversation_id="777031",
            message_id="10",
        )
        refreshed = await db_session.get(Message, unread.id)
        assert refreshed is not None
        assert refreshed.is_read is True

    async def test_hydrate_conversation_marks_non_telegram_read_without_history_fetch(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        conv = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="instagram_dm",
            external_chat_id="ig-thread-alpha",
        )
        db_session.add(conv)
        await db_session.flush()

        unread = Message(
            conversation_id=conv.id,
            channel="instagram_dm",
            sender_type="customer",
            content="old unread",
            external_message_id="ig-msg-old",
            telegram_timestamp=datetime.now(UTC),
            is_read=False,
        )
        db_session.add(unread)
        await db_session.flush()

        with (
            patch(
                "app.services.channel_conversation_sync.ChannelConversationSync.sync_conversation",
                new=AsyncMock(),
            ) as mock_sync,
            patch(
                "app.api.routes.conversation_commands.get_channel_adapter",
            ) as get_adapter,
        ):
            adapter = SimpleNamespace(
                capabilities=lambda: SimpleNamespace(mark_read=True),
                mark_read=AsyncMock(),
            )
            get_adapter.return_value = adapter
            res = await client.post(
                f"/api/conversations/{conv.id}/hydrate",
                headers=auth_headers,
                json={"limit": 100},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["requested"] == 0
        assert data["persisted"] == 0
        assert data["duplicates"] == 0
        assert data["unread_count"] == 0
        assert data["sync_status"] == "idle"
        assert "deprecated" not in data
        assert data["hydration"]["state"] == "idle"
        assert data["hydration"]["needed"] is False
        assert data["tail"]["schema_version"] == "conversation_tail.v1"
        mock_sync.assert_not_called()
        adapter.mark_read.assert_awaited_once_with(
            workspace_id=workspace.id,
            conversation_id="ig-thread-alpha",
            message_id="ig-msg-old",
        )
        refreshed = await db_session.get(Message, unread.id)
        assert refreshed is not None
        assert refreshed.is_read is True

    async def test_hydrate_conversation_queues_shell_tail_without_route_time_fetch(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        conv = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=777033,
            external_chat_id="777033",
        )
        projected_at = datetime.now(UTC)
        state = get_customer_conversation_state(conv)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(
                title="Gulsevarr",
                top_message_id=932,
                last_message_text="Aktivlashtirildi",
                last_message_date=projected_at.isoformat(),
                last_message_is_outgoing=True,
                telegram_unread_count=0,
            )
        )
        conv.summary = "Aktivlashtirildi"
        conv.last_message_at = projected_at
        set_customer_conversation_state(conv, state)
        db_session.add(conv)
        await db_session.flush()

        with patch(
            "app.services.channel_conversation_sync.ChannelConversationSync.sync_conversation",
            new=AsyncMock(),
        ) as mock_sync:
            res = await client.post(
                f"/api/conversations/{conv.id}/hydrate",
                headers=auth_headers,
                json={"limit": 50},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["requested"] == 0
        assert data["persisted"] == 0
        assert data["duplicates"] == 0
        assert data["sync_status"] == "queued"
        assert "deprecated" not in data
        assert data["hydration"]["state"] == "queued"
        assert data["hydration"]["needed"] is True
        assert data["tail"]["latest_message_text"] == "Aktivlashtirildi"
        mock_sync.assert_not_called()

        runtime = await db_session.scalar(
            select(ConversationHydrationRuntime).where(
                ConversationHydrationRuntime.workspace_id == workspace.id,
                ConversationHydrationRuntime.conversation_id == conv.id,
            )
        )
        assert runtime is not None
        assert runtime.state == "queued"
        assert runtime.requested_limit == 50

        messages_res = await client.get(
            f"/api/conversations/{conv.id}/messages",
            headers=auth_headers,
        )
        assert messages_res.status_code == 200
        messages_data = messages_res.json()
        assert messages_data["items"] == []
        assert messages_data["hydration"]["state"] == "queued"
        assert messages_data["hydration"]["needed"] is True

    async def test_hydrate_conversation_queues_media_or_empty_text_dialog_tail(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        conv = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=777035,
            external_chat_id="777035",
        )
        projected_at = datetime.now(UTC)
        state = get_customer_conversation_state(conv)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(
                title="Azim Egamberdiev",
                top_message_id=914,
                last_message_text="",
                last_message_date=projected_at.isoformat(),
                last_message_is_outgoing=False,
                telegram_unread_count=0,
            )
        )
        conv.summary = "Telegram preview came from dialog shell"
        conv.last_message_at = projected_at
        set_customer_conversation_state(conv, state)
        db_session.add(conv)
        await db_session.flush()

        with patch(
            "app.services.channel_conversation_sync.ChannelConversationSync.sync_conversation",
            new=AsyncMock(),
        ) as mock_sync:
            res = await client.post(
                f"/api/conversations/{conv.id}/hydrate",
                headers=auth_headers,
                json={"limit": 50},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["sync_status"] == "queued"
        assert data["hydration"]["needed"] is True
        assert data["hydration"]["state"] == "queued"
        mock_sync.assert_not_called()

        runtime = await db_session.scalar(
            select(ConversationHydrationRuntime).where(
                ConversationHydrationRuntime.workspace_id == workspace.id,
                ConversationHydrationRuntime.conversation_id == conv.id,
            )
        )
        assert runtime is not None
        assert runtime.state == "queued"

    async def test_hydrate_conversation_is_only_explicit_history_fetch(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        conv = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=777032,
            external_chat_id="777032",
        )
        db_session.add(conv)
        await db_session.flush()

        with patch(
            "app.services.channel_conversation_sync.ChannelConversationSync.sync_conversation",
            new=AsyncMock(),
        ) as read_repair_sync:
            res = await client.get(
                f"/api/conversations/{conv.id}/messages",
                headers=auth_headers,
            )

        assert res.status_code == 200
        assert res.json()["items"] == []
        read_repair_sync.assert_not_called()

    async def test_conversation_hydration_worker_defers_when_dialog_tail_still_ahead(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
    ):
        conv = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=777034,
            external_chat_id="777034",
        )
        projected_at = datetime.now(UTC)
        state = get_customer_conversation_state(conv)
        state.sync = ConversationSyncState(
            dialog=ConversationDialogState(
                last_message_text="Preview from Telegram",
                last_message_date=projected_at.isoformat(),
            )
        )
        conv.summary = "Preview from Telegram"
        conv.last_message_at = projected_at
        set_customer_conversation_state(conv, state)
        db_session.add(conv)
        await db_session.flush()

        runtime = ConversationHydrationRuntime(
            workspace_id=workspace.id,
            conversation_id=conv.id,
            state="queued",
            requested_limit=50,
            attempt_count=0,
            max_attempts=3,
            next_attempt_at=projected_at,
        )
        db_session.add(runtime)
        await db_session.flush()

        class ExistingSessionFactory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return db_session

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

        class EmptySync:
            async def sync_conversation(self, **kwargs):
                return ConversationSyncResult(requested=0, persisted=0, duplicates=0)

        worker = ConversationHydrationWorker(
            db_factory=ExistingSessionFactory(),
            sync_service=EmptySync(),
        )

        with patch(
            "app.services.conversation_hydration_worker.ws_manager.broadcast",
            new=AsyncMock(),
        ):
            processed = await worker.run_once(now=projected_at)

        assert processed == 1
        refreshed = await db_session.get(ConversationHydrationRuntime, runtime.id)
        assert refreshed is not None
        assert refreshed.state == "deferred"
        assert refreshed.last_error == "history_source_returned_no_messages_while_dialog_tail_is_ahead"

    async def test_messages_do_not_repair_visible_gap(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """Middle Telegram ID gaps are reported, not fetched during normal reads."""

        conv = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="telegram_dm",
            telegram_chat_id=777009,
            external_chat_id="777009",
        )
        db_session.add(conv)
        await db_session.flush()

        older = Message(
            conversation_id=conv.id,
            channel="telegram_dm",
            sender_type="customer",
            content="before gap",
            telegram_message_id=100,
            external_message_id="100",
            telegram_timestamp=datetime.now(UTC) - timedelta(minutes=2),
            is_read=True,
        )
        newer = Message(
            conversation_id=conv.id,
            channel="telegram_dm",
            sender_type="customer",
            content="after gap",
            telegram_message_id=120,
            external_message_id="120",
            telegram_timestamp=datetime.now(UTC),
            is_read=True,
        )
        db_session.add_all([older, newer])
        await db_session.flush()

        with patch(
            "app.services.channel_conversation_sync.ChannelConversationSync.sync_conversation",
            new=AsyncMock(),
        ) as mock_sync:
            res = await client.get(
                f"/api/conversations/{conv.id}/messages",
                headers=auth_headers,
            )

        assert res.status_code == 200
        assert [item["content"] for item in res.json()["items"]] == [
            "before gap",
            "after gap",
        ]
        assert "x-oqim-repair-attempts" not in res.headers
        assert "x-oqim-repair-reasons" not in res.headers
        mock_sync.assert_not_called()

    async def test_projects_invalid_media_url_to_canonical_route_without_read_repair(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Normal reads project canonical media URLs without mutating stored truth."""
        msg = Message(
            conversation_id=conversation.id,
            sender_type="customer",
            content="[photo]",
            media_type="photo",
            media_url="/api/media/9bc2c0d1.jpg",
            telegram_message_id=444001,
        )
        db_session.add(msg)
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages",
            headers=auth_headers,
        )

        assert res.status_code == 200
        item = res.json()["items"][0]
        expected_url = f"/api/media/{conversation.telegram_chat_id}/{msg.telegram_message_id}"
        assert item["media_url"] == expected_url

        refreshed = await db_session.get(Message, msg.id)
        assert refreshed.media_url == "/api/media/9bc2c0d1.jpg"

    async def test_projects_legacy_gramjs_media_type_to_frontend_supported_type_without_read_repair(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        msg = Message(
            conversation_id=conversation.id,
            sender_type="customer",
            content="",
            media_type="MessageMediaPhoto",
            telegram_message_id=555001,
        )
        db_session.add(msg)
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages",
            headers=auth_headers,
        )

        assert res.status_code == 200
        item = res.json()["items"][0]
        assert item["media_type"] == "photo"
        assert item["media_url"] == f"/api/media/{conversation.telegram_chat_id}/{msg.telegram_message_id}"

        refreshed = await db_session.get(Message, msg.id)
        assert refreshed.media_type == "MessageMediaPhoto"

    async def test_exposes_distinct_preview_and_full_media_urls(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        msg = Message(
            conversation_id=conversation.id,
            sender_type="customer",
            content="[photo]",
            media_type="photo",
            telegram_message_id=666001,
        )
        db_session.add(msg)
        await db_session.flush()

        res = await client.get(
            f"/api/conversations/{conversation.id}/messages",
            headers=auth_headers,
        )

        assert res.status_code == 200
        item = res.json()["items"][0]
        full_url = f"/api/media/{conversation.telegram_chat_id}/{msg.telegram_message_id}"
        assert item["media_url"] == full_url
        assert item["media_full_url"] == full_url
        assert item["media_preview_url"] == f"{full_url}?thumb=true"


class TestSendMessage:
    async def test_send_success(
        self,
        client: AsyncClient,
        conversation: Conversation,
        auth_headers: dict,
    ):
        res = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={"content": "Ha, iPhone 15 bor!"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["content"] == "Ha, iPhone 15 bor!"
        assert data["sender_type"] == "seller"
        assert data["conversation_id"] == conversation.id
        assert data["is_read"] is True
        assert data["delivery_state"] == "confirmed"

    async def test_send_calls_delivery_service(
        self,
        client: AsyncClient,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """send-message endpoint delivers via DeliveryService and returns externalMessageId."""
        from app.core.deps import get_delivery_service
        delivery = client._transport.app.dependency_overrides[get_delivery_service]()  # type: ignore[union-attr]
        delivery.deliver_message.reset_mock()

        res = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={"content": "Yetkazib beramiz!"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["external_message_id"] == "mock_ext_123"
        assert data["delivery_state"] == "confirmed"

        # DeliveryService.deliver_message was called
        delivery.deliver_message.assert_called_once()

    async def test_send_delivery_failure_keeps_placeholder_for_echo_reconciliation(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Ambiguous sidecar failures keep the local send so Telegram echoes can reconcile."""
        from app.core.deps import get_delivery_service
        from app.services.delivery import DeliveryResult
        delivery = client._transport.app.dependency_overrides[get_delivery_service]()  # type: ignore[union-attr]
        delivery.deliver_message.return_value = DeliveryResult(
            success=False, error="sidecar down",
        )

        res = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={"content": "Saved but not delivered"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["content"] == "Saved but not delivered"
        assert res.json()["sender_type"] == "seller"
        assert res.json()["external_message_id"] is None
        assert res.json()["delivery_state"] == "unknown"

        result = await db_session.execute(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.content == "Saved but not delivered",
            )
        )
        stored = result.scalar_one()
        assert stored.client_message_uuid is not None
        assert len(stored.client_message_uuid) == 32
        assert stored.delivery_state == "unknown"

        # Restore mock for other tests
        delivery.deliver_message.return_value = DeliveryResult(
            success=True, external_message_id="mock_ext_123",
        )

    async def test_send_creates_seller_message_in_db(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """The message saved to DB should have sender_type='seller'."""
        res = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={"content": "Yetkazib beramiz"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        msg_id = res.json()["id"]

        # Verify in DB
        from sqlalchemy import select

        result = await db_session.execute(
            select(Message).where(Message.id == msg_id)
        )
        msg = result.scalar_one()
        assert msg.sender_type == "seller"
        assert msg.content == "Yetkazib beramiz"
        assert msg.is_read is True
        assert msg.delivery_state == "confirmed"
        assert msg.conversation_seq == res.json()["conversation_seq"]

    async def test_send_retry_reuses_client_uuid_without_second_unknown_send(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        from app.core.deps import get_delivery_service
        from app.services.delivery import DeliveryResult

        delivery = client._transport.app.dependency_overrides[get_delivery_service]()  # type: ignore[union-attr]
        client_message_uuid = "retry-same-send-uuid"
        delivery.deliver_message.return_value = DeliveryResult(
            success=False,
            error="timeout",
            state="unknown",
        )

        first = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={
                "content": "Retry qilamiz",
                "client_message_uuid": client_message_uuid,
            },
            headers=auth_headers,
        )
        second = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={
                "content": "Retry qilamiz",
                "client_message_uuid": client_message_uuid,
            },
            headers=auth_headers,
        )

        assert first.status_code == 200
        assert first.json()["delivery_state"] == "unknown"
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["external_message_id"] is None
        assert second.json()["delivery_state"] == "unknown"
        rows = (
            await db_session.execute(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.client_message_uuid == client_message_uuid,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].delivery_state == "unknown"
        assert delivery.deliver_message.await_count == 1
        assert [
            call.kwargs["client_idempotency_key"]
            for call in delivery.deliver_message.await_args_list
        ] == [client_message_uuid]

        delivery.deliver_message.return_value = DeliveryResult(
            success=True,
            external_message_id="mock_ext_123",
        )

    async def test_send_persists_client_message_uuid(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        client_message_uuid = "1d2f7f7c-2f24-4c8b-8160-5fcb4747b8a9"
        res = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={
                "content": "UUID bilan yuborildi",
                "client_message_uuid": client_message_uuid,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["client_message_uuid"] == client_message_uuid
        assert res.json()["conversation_seq"] == 1

        from sqlalchemy import select

        result = await db_session.execute(
            select(Message).where(Message.id == res.json()["id"])
        )
        msg = result.scalar_one()
        assert msg.client_message_uuid == client_message_uuid
        assert msg.conversation_seq == 1

    async def test_send_nonexistent_conversation_404(
        self, client: AsyncClient, auth_headers: dict
    ):
        res = await client.post(
            "/api/conversations/99999/send-message",
            json={"content": "Hello"},
            headers=auth_headers,
        )
        assert res.status_code == 404

    async def test_send_other_workspace_404(
        self, client: AsyncClient, conversation: Conversation, auth_headers_b: dict
    ):
        """Cannot send message to another workspace's conversation."""
        res = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={"content": "Hacked!"},
            headers=auth_headers_b,
        )
        assert res.status_code == 404

    async def test_send_empty_content_422(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        res = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 422

    async def test_send_requires_auth(self, client: AsyncClient, conversation: Conversation):
        res = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={"content": "No auth"},
        )
        assert res.status_code == 401

    async def test_send_no_telegram_chat_id_returns_error_without_phantom_message(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        customer: Customer,
        auth_headers: dict,
    ):
        """Manual send needs a real channel identity before writing seller message."""
        from app.core.deps import get_delivery_service
        from app.services.delivery import DeliveryResult
        delivery = client._transport.app.dependency_overrides[get_delivery_service]()  # type: ignore[union-attr]
        delivery.deliver_message.return_value = DeliveryResult(
            success=False, error="No telegram_chat_id",
        )

        conv_no_tg = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            telegram_chat_id=None,
            pipeline_stage="new",
            last_message_at=datetime.now(UTC),
        )
        db_session.add(conv_no_tg)
        await db_session.flush()

        res = await client.post(
            f"/api/conversations/{conv_no_tg.id}/send-message",
            json={"content": "No telegram"},
            headers=auth_headers,
        )
        assert res.status_code == 503

        result = await db_session.execute(
            select(Message).where(
                Message.conversation_id == conv_no_tg.id,
                Message.content == "No telegram",
            )
        )
        assert result.scalar_one_or_none() is None

        # Restore mock for other tests
        delivery.deliver_message.return_value = DeliveryResult(
            success=True, external_message_id="mock_ext_123",
        )

    async def test_send_mock_instagram_uses_channel_adapter_contract(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        auth_headers: dict,
    ):
        from app.core.deps import get_delivery_service

        delivery = client._transport.app.dependency_overrides[get_delivery_service]()  # type: ignore[union-attr]
        delivery.deliver_message.reset_mock()
        customer = Customer(
            workspace_id=workspace.id,
            channel="instagram_dm",
            external_id="ig-customer-1",
            display_name="IG Customer",
        )
        db_session.add(customer)
        await db_session.flush()
        conversation = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="instagram_dm",
            telegram_chat_id=None,
            external_chat_id="ig-thread-1",
            pipeline_stage="new",
            last_message_at=datetime.now(UTC),
        )
        db_session.add(conversation)
        await db_session.flush()

        res = await client.post(
            f"/api/conversations/{conversation.id}/send-message",
            json={"content": "Instagram orqali yuborildi"},
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["channel"] == "instagram_dm"
        assert data["external_message_id"] == "ig:ig-thread-1:1"
        assert data["delivery_state"] == "confirmed"
        delivery.deliver_message.assert_not_called()

        stored = await db_session.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.content == "Instagram orqali yuborildi",
            )
        )
        assert stored is not None
        assert stored.channel == "instagram_dm"
        assert stored.external_message_id == "ig:ig-thread-1:1"


class TestUpdateConversation:
    async def test_update_pipeline_stage(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        res = await client.patch(
            f"/api/conversations/{conversation.id}",
            json={"pipeline_stage": "qualified"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["pipeline_stage"] == "qualified"
        await db_session.refresh(conversation)
        state = get_customer_conversation_state(conversation)
        assert state.pipeline_stage == "qualified"
        assert state.field_provenance["pipeline_stage"] == "seller"

    async def test_update_needs_attention(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        res = await client.patch(
            f"/api/conversations/{conversation.id}",
            json={"needs_attention": True},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["needs_attention"] is True

    async def test_partial_update_preserves_other_fields(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        """Updating only pipeline_stage should not change needs_attention."""
        res = await client.patch(
            f"/api/conversations/{conversation.id}",
            json={"pipeline_stage": "negotiation"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["pipeline_stage"] == "negotiation"
        assert data["needs_attention"] is False  # unchanged from default

    async def test_update_both_fields(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        res = await client.patch(
            f"/api/conversations/{conversation.id}",
            json={"pipeline_stage": "won", "needs_attention": True},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["pipeline_stage"] == "won"
        assert data["needs_attention"] is True

    async def test_update_override_mode(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        res = await client.patch(
            f"/api/conversations/{conversation.id}",
            json={"override_mode": "force_draft"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["override_mode"] == "force_draft"

    async def test_update_returns_customer_name(
        self, client: AsyncClient, conversation: Conversation, auth_headers: dict
    ):
        res = await client.patch(
            f"/api/conversations/{conversation.id}",
            json={"pipeline_stage": "lost"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["customer_name"] == "Alisher Valiev"

    async def test_update_nonexistent_404(self, client: AsyncClient, auth_headers: dict):
        res = await client.patch(
            "/api/conversations/99999",
            json={"pipeline_stage": "won"},
            headers=auth_headers,
        )
        assert res.status_code == 404

    async def test_update_other_workspace_404(
        self, client: AsyncClient, conversation: Conversation, auth_headers_b: dict
    ):
        """Cannot update another workspace's conversation."""
        res = await client.patch(
            f"/api/conversations/{conversation.id}",
            json={"pipeline_stage": "won"},
            headers=auth_headers_b,
        )
        assert res.status_code == 404

    async def test_update_requires_auth(
        self, client: AsyncClient, conversation: Conversation
    ):
        res = await client.patch(
            f"/api/conversations/{conversation.id}",
            json={"pipeline_stage": "won"},
        )
        assert res.status_code == 401


class TestMarkRead:
    async def test_mark_read_success(
        self,
        client: AsyncClient,
        conversation: Conversation,
        message: Message,
        auth_headers: dict,
    ):
        """Marking read should return ok and mark customer messages as read."""
        # Verify the message starts as unread
        assert message.is_read is False

        res = await client.post(
            f"/api/conversations/{conversation.id}/mark-read",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True}

    async def test_mark_read_updates_messages(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        message: Message,
        auth_headers: dict,
    ):
        """After mark-read, unread_count should be 0."""
        # Mark read
        await client.post(
            f"/api/conversations/{conversation.id}/mark-read",
            headers=auth_headers,
        )

        # Fetch conversation to check unread_count
        res = await client.get(
            f"/api/conversations/{conversation.id}", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.json()["unread_count"] == 0

    async def test_mark_read_only_affects_customer_messages(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Seller messages should not be affected by mark-read."""
        # Create a customer message (unread) and a seller message (already read)
        customer_msg = Message(
            conversation_id=conversation.id,
            sender_type="customer",
            content="Narxi qancha?",
            is_read=False,
        )
        seller_msg = Message(
            conversation_id=conversation.id,
            sender_type="seller",
            content="12 million",
            is_read=True,
        )
        db_session.add_all([customer_msg, seller_msg])
        await db_session.flush()

        # Mark read
        await client.post(
            f"/api/conversations/{conversation.id}/mark-read",
            headers=auth_headers,
        )

        # Verify: customer message should now be read
        from sqlalchemy import select

        result = await db_session.execute(
            select(Message).where(Message.id == customer_msg.id)
        )
        updated_msg = result.scalar_one()
        assert updated_msg.is_read is True

    async def test_mark_read_multiple_unread_messages(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        conversation: Conversation,
        auth_headers: dict,
    ):
        """Multiple unread customer messages should all become read."""
        for i in range(3):
            db_session.add(
                Message(
                    conversation_id=conversation.id,
                    sender_type="customer",
                    content=f"Message {i}",
                    is_read=False,
                )
            )
        await db_session.flush()

        await client.post(
            f"/api/conversations/{conversation.id}/mark-read",
            headers=auth_headers,
        )

        res = await client.get(
            f"/api/conversations/{conversation.id}", headers=auth_headers
        )
        assert res.json()["unread_count"] == 0

    async def test_mark_read_idempotent(
        self,
        client: AsyncClient,
        conversation: Conversation,
        message: Message,
        auth_headers: dict,
    ):
        """Calling mark-read twice should not cause errors."""
        res1 = await client.post(
            f"/api/conversations/{conversation.id}/mark-read",
            headers=auth_headers,
        )
        assert res1.status_code == 200

        res2 = await client.post(
            f"/api/conversations/{conversation.id}/mark-read",
            headers=auth_headers,
        )
        assert res2.status_code == 200
        assert res2.json() == {"ok": True}

    async def test_mark_read_nonexistent_404(
        self, client: AsyncClient, auth_headers: dict
    ):
        res = await client.post(
            "/api/conversations/99999/mark-read", headers=auth_headers
        )
        assert res.status_code == 404

    async def test_mark_read_other_workspace_404(
        self, client: AsyncClient, conversation: Conversation, auth_headers_b: dict
    ):
        """Cannot mark-read another workspace's conversation."""
        res = await client.post(
            f"/api/conversations/{conversation.id}/mark-read",
            headers=auth_headers_b,
        )
        assert res.status_code == 404

    async def test_mark_read_requires_auth(
        self, client: AsyncClient, conversation: Conversation
    ):
        res = await client.post(
            f"/api/conversations/{conversation.id}/mark-read"
        )
        assert res.status_code == 401

    async def test_mark_read_mock_instagram_uses_channel_adapter_contract(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        auth_headers: dict,
    ):
        customer = Customer(
            workspace_id=workspace.id,
            channel="instagram_dm",
            external_id="ig-customer-read",
            display_name="IG Read Customer",
        )
        db_session.add(customer)
        await db_session.flush()
        conversation = Conversation(
            workspace_id=workspace.id,
            customer_id=customer.id,
            channel="instagram_dm",
            telegram_chat_id=None,
            external_chat_id="ig-thread-read",
            pipeline_stage="new",
            last_message_at=datetime.now(UTC),
        )
        db_session.add(conversation)
        await db_session.flush()
        message = Message(
            conversation_id=conversation.id,
            channel="instagram_dm",
            sender_type="customer",
            content="Seen?",
            external_message_id="ig-msg-read",
            is_read=False,
        )
        db_session.add(message)
        await db_session.flush()

        with patch("app.api.routes.conversation_commands.get_channel_adapter") as get_adapter:
            adapter = SimpleNamespace(
                capabilities=lambda: SimpleNamespace(mark_read=True),
                mark_read=AsyncMock(),
            )
            get_adapter.return_value = adapter
            res = await client.post(
                f"/api/conversations/{conversation.id}/mark-read",
                headers=auth_headers,
            )

        assert res.status_code == 200
        adapter.mark_read.assert_awaited_once_with(
            workspace_id=workspace.id,
            conversation_id="ig-thread-read",
            message_id="ig-msg-read",
        )
