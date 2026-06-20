"""Typing-aware coalescing: short debounce + hold while the customer types.

Founder call (2026-06-10): a fixed 6s window taxed every reply; steering is
architecturally unsafe (mid-run tool calls are permanent). The clean lever is
dispatch timing — wait briefly after the last bubble, and keep waiting while
Telegram says "yozmoqda…", with a hard cap so a drafting customer can never
stall the agent forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.message import Message, SenderType
from app.modules.conversation_turns.service import (
    MEDIA_HYDRATION_MAX_HOLD_SECONDS,
    TURN_COALESCE_SECONDS,
    TYPING_HOLD_SECONDS,
    TYPING_MAX_HOLD_SECONDS,
    ConversationTurnSessionService,
)

pytestmark = pytest.mark.asyncio


def test_window_constants_shape():
    # short base debounce (latency floor), typing hold a bit longer, hard cap
    assert TURN_COALESCE_SECONDS <= 4.5
    assert TYPING_HOLD_SECONDS > TURN_COALESCE_SECONDS
    assert TYPING_MAX_HOLD_SECONDS >= 20.0


async def _open_turn(db_session, workspace, conversation, customer, agent):
    message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content="salom",
        created_at=datetime.now(UTC),
    )
    db_session.add(message)
    await db_session.flush()
    service = ConversationTurnSessionService(db_session)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=message,
        agent_id=agent.id,
    )
    await db_session.flush()
    return service, turn


async def test_lease_holds_while_customer_is_typing(
    db_session, workspace, conversation, customer, agent
):
    service, turn = await _open_turn(db_session, workspace, conversation, customer, agent)
    now = datetime.now(UTC)
    # message debounce elapsed, but the customer is typing right now
    turn.latest_customer_message_at = now - timedelta(seconds=TURN_COALESCE_SECONDS + 1)
    turn.latest_customer_typing_at = now
    await db_session.flush()

    assert await service.lease_ready_turns(limit=5, max_per_workspace=5) == []

    # typing went stale -> leased
    turn.latest_customer_typing_at = now - timedelta(seconds=TYPING_HOLD_SECONDS + 1)
    await db_session.flush()
    leased = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert [lease.turn_session_id for lease in leased] == [turn.id]


async def test_typing_cannot_stall_past_the_cap(
    db_session, workspace, conversation, customer, agent
):
    service, turn = await _open_turn(db_session, workspace, conversation, customer, agent)
    now = datetime.now(UTC)
    # customer has been "typing" for ages -> the cap releases the turn anyway
    turn.latest_customer_message_at = now - timedelta(seconds=TYPING_MAX_HOLD_SECONDS + 1)
    turn.latest_customer_typing_at = now
    await db_session.flush()

    leased = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert [lease.turn_session_id for lease in leased] == [turn.id]


async def test_mark_customer_typing_touches_open_turns(
    db_session, workspace, conversation, customer, agent
):
    service, turn = await _open_turn(db_session, workspace, conversation, customer, agent)
    assert turn.latest_customer_typing_at is None

    touched = await service.mark_customer_typing(
        workspace_id=workspace.id, conversation_id=conversation.id
    )

    assert touched == 1
    await db_session.refresh(turn)
    assert turn.latest_customer_typing_at is not None


async def _open_media_turn(
    db_session, workspace, conversation, customer, agent,
    *, media_type="voice", hydration_status=None,
):
    """Open a turn whose latest bubble is media, with coalesce/typing already
    satisfied so the media-hydration hold is the only thing that can gate it."""
    meta: dict = {} if hydration_status is None else {"hydration_status": hydration_status}
    message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content=f"[{media_type}]",
        media_type=media_type,
        media_metadata=meta,
        created_at=datetime.now(UTC),
    )
    db_session.add(message)
    await db_session.flush()
    service = ConversationTurnSessionService(db_session)
    turn = await service.append_customer_message(
        workspace_id=workspace.id,
        conversation=conversation,
        customer=customer,
        message=message,
        agent_id=agent.id,
    )
    turn.latest_customer_message_at = datetime.now(UTC) - timedelta(
        seconds=TURN_COALESCE_SECONDS + 1
    )
    await db_session.flush()
    return service, turn, message


async def test_media_turn_held_while_pending_then_released_when_hydrated(
    db_session, workspace, conversation, customer, agent
):
    service, turn, message = await _open_media_turn(
        db_session, workspace, conversation, customer, agent,
        media_type="voice", hydration_status="pending",
    )
    # voice still hydrating -> the agent must not answer a bare "[voice]" yet
    assert await service.lease_ready_turns(limit=5, max_per_workspace=5) == []

    message.media_metadata = {"hydration_status": "hydrated"}
    db_session.add(message)
    await db_session.flush()
    leased = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert [lease.turn_session_id for lease in leased] == [turn.id]


async def test_media_turn_with_no_metadata_is_held(
    db_session, workspace, conversation, customer, agent
):
    service, _turn, _ = await _open_media_turn(
        db_session, workspace, conversation, customer, agent,
        media_type="photo", hydration_status=None,
    )
    assert await service.lease_ready_turns(limit=5, max_per_workspace=5) == []


async def test_media_turn_released_when_terminal_unavailable(
    db_session, workspace, conversation, customer, agent
):
    service, turn, _ = await _open_media_turn(
        db_session, workspace, conversation, customer, agent,
        media_type="voice", hydration_status="unavailable",
    )
    leased = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert [lease.turn_session_id for lease in leased] == [turn.id]


async def test_media_hold_cap_releases_stuck_hydration(
    db_session, workspace, conversation, customer, agent
):
    service, turn, _ = await _open_media_turn(
        db_session, workspace, conversation, customer, agent,
        media_type="voice", hydration_status="pending",
    )
    # pending far longer than the cap -> dispatch anyway (never stall forever)
    turn.latest_customer_message_at = datetime.now(UTC) - timedelta(
        seconds=MEDIA_HYDRATION_MAX_HOLD_SECONDS + 1
    )
    await db_session.flush()
    leased = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert [lease.turn_session_id for lease in leased] == [turn.id]


async def test_text_turn_not_held_by_media_gate(
    db_session, workspace, conversation, customer, agent
):
    service, turn = await _open_turn(db_session, workspace, conversation, customer, agent)
    turn.latest_customer_message_at = datetime.now(UTC) - timedelta(
        seconds=TURN_COALESCE_SECONDS + 1
    )
    await db_session.flush()
    leased = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert [lease.turn_session_id for lease in leased] == [turn.id]


async def test_sticker_turn_held_until_hydrated(
    db_session, workspace, conversation, customer, agent
):
    # stickers are now perceived (full .webp) -> held until the sticker hydrates
    service, turn, message = await _open_media_turn(
        db_session, workspace, conversation, customer, agent,
        media_type="sticker", hydration_status="pending",
    )
    assert await service.lease_ready_turns(limit=5, max_per_workspace=5) == []
    message.media_metadata = {"hydration_status": "hydrated"}
    db_session.add(message)
    await db_session.flush()
    leased = await service.lease_ready_turns(limit=5, max_per_workspace=5)
    assert [lease.turn_session_id for lease in leased] == [turn.id]
