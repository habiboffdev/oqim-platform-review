"""Tests for multi-channel customer identity (#112)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.conversation_core.service import _find_or_create_customer


pytestmark = pytest.mark.asyncio


class TestCustomerExternalId:
    async def test_create_with_external_id(
        self, db_session: AsyncSession, workspace: Workspace,
    ):
        """Customer created via external_id + channel is findable."""
        customer = await _find_or_create_customer(
            db_session,
            workspace_id=workspace.id,
            display_name="Alisher",
            external_id="ig_12345",
            channel="instagram_dm",
        )
        assert customer.id is not None
        assert customer.external_id == "ig_12345"
        assert customer.channel == "instagram_dm"

    async def test_same_external_id_returns_existing(
        self, db_session: AsyncSession, workspace: Workspace,
    ):
        """Same external_id + channel combo returns the existing customer, not a duplicate."""
        c1 = await _find_or_create_customer(
            db_session,
            workspace_id=workspace.id,
            display_name="Alisher",
            external_id="ig_12345",
            channel="instagram_dm",
        )
        c2 = await _find_or_create_customer(
            db_session,
            workspace_id=workspace.id,
            display_name="Alisher Updated",
            external_id="ig_12345",
            channel="instagram_dm",
        )
        assert c1.id == c2.id

    async def test_different_channel_creates_new(
        self, db_session: AsyncSession, workspace: Workspace,
    ):
        """Same external_id but different channel creates a new customer."""
        c1 = await _find_or_create_customer(
            db_session,
            workspace_id=workspace.id,
            display_name="Alisher TG",
            external_id="12345",
            channel="telegram_dm",
        )
        c2 = await _find_or_create_customer(
            db_session,
            workspace_id=workspace.id,
            display_name="Alisher IG",
            external_id="12345",
            channel="instagram_dm",
        )
        assert c1.id != c2.id

    async def test_telegram_id_path_still_works(
        self, db_session: AsyncSession, workspace: Workspace,
    ):
        """Existing telegram_id-based dedup still works (backward compat)."""
        c1 = await _find_or_create_customer(
            db_session,
            workspace_id=workspace.id,
            display_name="Telegram User",
            telegram_id=999888777,
        )
        c2 = await _find_or_create_customer(
            db_session,
            workspace_id=workspace.id,
            display_name="Updated Name",
            telegram_id=999888777,
        )
        assert c1.id == c2.id
