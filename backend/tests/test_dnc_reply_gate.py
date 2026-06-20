"""DNC (opted_out) suppresses the seller's reactive reply.

When a do-not-contact customer messages, _handle_customer_message must NOT enter
the reply lifecycle (no enqueue) and must raise one owner-bot card per
conversation per day. The non-opted-out path is covered by
test_telegram_intake_harness (asserts enqueue for a normal customer)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.services.inbound_pipeline import _handle_customer_message

pytestmark = pytest.mark.asyncio


async def _owner_card_count(db_session) -> int:
    return await db_session.scalar(
        select(func.count())
        .select_from(BusinessBrainProjectionRecord)
        .where(BusinessBrainProjectionRecord.projection_type == "owner_notification")
    )


async def test_opted_out_customer_is_silent_and_cards_owner(
    db_session, workspace, conversation, customer, message
):
    customer.opted_out = True
    await db_session.flush()
    runner = AsyncMock()

    result = await _handle_customer_message(
        session=db_session,
        workspace=workspace,
        conversation=conversation,
        message=message,
        customer=customer,
        conversation_turn_runner=runner,
        raw_payload={"text": "Salom, narx qancha?"},
    )

    assert result is False
    runner.enqueue_message.assert_not_awaited()        # silent: no turn dispatched
    assert await _owner_card_count(db_session) == 1     # one owner card raised


async def test_opted_out_owner_card_deduped_same_day(
    db_session, workspace, conversation, customer, message
):
    customer.opted_out = True
    await db_session.flush()
    runner = AsyncMock()
    args = dict(
        session=db_session, workspace=workspace, conversation=conversation,
        message=message, customer=customer, conversation_turn_runner=runner,
        raw_payload={"text": "yana xabar"},
    )
    await _handle_customer_message(**args)
    await _handle_customer_message(**args)              # second message, same day
    assert await _owner_card_count(db_session) == 1     # one card, not two


async def test_opted_out_card_failure_is_non_fatal_and_still_silent(
    db_session, workspace, conversation, customer, message
):
    customer.opted_out = True
    await db_session.flush()
    runner = AsyncMock()
    with patch(
        "app.modules.crm_connector.owner_cards.queue_crm_owner_notification",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await _handle_customer_message(
            session=db_session, workspace=workspace, conversation=conversation,
            message=message, customer=customer, conversation_turn_runner=runner,
            raw_payload={"text": "salom"},
        )
    assert result is False                              # still silent
    runner.enqueue_message.assert_not_awaited()         # never enqueued
