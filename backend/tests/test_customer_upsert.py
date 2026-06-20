"""Tests for shared customer upsert — Issue #72."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.conversation_core.service import (
    upsert_customer_and_conversation,
)


pytestmark = pytest.mark.asyncio


class TestUpsertCustomerAndConversation:
    async def test_creates_new_customer_and_conversation(
        self, db_session: AsyncSession, workspace: Workspace,
    ):
        """First upsert creates both customer and conversation."""
        customer, conversation = await upsert_customer_and_conversation(
            db_session,
            workspace_id=workspace.id,
            telegram_chat_id=12345,
            display_name="Alisher",
        )
        assert customer.display_name == "Alisher"
        assert customer.telegram_id == 12345
        assert conversation.telegram_chat_id == 12345
        assert conversation.workspace_id == workspace.id

    async def test_creates_non_telegram_customer_and_conversation_by_external_ids(
        self,
        db_session: AsyncSession,
        workspace: Workspace,
    ):
        customer, conversation = await upsert_customer_and_conversation(
            db_session,
            workspace_id=workspace.id,
            external_id="ig-user-1",
            external_chat_id="ig-thread-1",
            display_name="Instagram Lead",
            channel="instagram_dm",
        )

        assert customer.display_name == "Instagram Lead"
        assert customer.channel == "instagram_dm"
        assert customer.telegram_id is None
        assert customer.external_id == "ig-user-1"
        assert conversation.channel == "instagram_dm"
        assert conversation.telegram_chat_id is None
        assert conversation.external_chat_id == "ig-thread-1"

    async def test_updates_unknown_display_name(
        self, db_session: AsyncSession, workspace: Workspace,
    ):
        """Second upsert updates display_name if it was 'Unknown'."""
        await upsert_customer_and_conversation(
            db_session, workspace_id=workspace.id,
            telegram_chat_id=100, display_name="",
        )
        customer, _ = await upsert_customer_and_conversation(
            db_session, workspace_id=workspace.id,
            telegram_chat_id=100, display_name="Malika",
        )
        assert customer.display_name == "Malika"

    async def test_preserves_existing_display_name(
        self, db_session: AsyncSession, workspace: Workspace,
    ):
        """Second upsert does NOT overwrite a real display_name."""
        await upsert_customer_and_conversation(
            db_session, workspace_id=workspace.id,
            telegram_chat_id=200, display_name="Alisher",
        )
        customer, _ = await upsert_customer_and_conversation(
            db_session, workspace_id=workspace.id,
            telegram_chat_id=200, display_name="Different",
        )
        assert customer.display_name == "Alisher"

    async def test_preserves_contact_type_when_not_provided(
        self, db_session: AsyncSession, workspace: Workspace,
    ):
        """Upsert without contact_type preserves existing classification."""
        await upsert_customer_and_conversation(
            db_session, workspace_id=workspace.id,
            telegram_chat_id=300, display_name="Test",
            contact_type="personal", classification_confidence=0.9,
        )
        customer, _ = await upsert_customer_and_conversation(
            db_session, workspace_id=workspace.id,
            telegram_chat_id=300, display_name="Test",
        )
        assert customer.contact_type == "personal"

    async def test_sets_contact_type_on_new_customer(
        self, db_session: AsyncSession, workspace: Workspace,
    ):
        """New customer gets contact_type if provided."""
        customer, _ = await upsert_customer_and_conversation(
            db_session, workspace_id=workspace.id,
            telegram_chat_id=400, display_name="Supplier",
            contact_type="supplier", classification_confidence=0.85,
        )
        assert customer.contact_type == "supplier"
