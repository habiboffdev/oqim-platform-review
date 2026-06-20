from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.conversation_state import (
    ConversationFollowUpState,
    ConversationReplyState,
    CustomerConversationState,
    project_next_best_action,
)


def _state(
    *,
    reply: ConversationReplyState | None = None,
    follow_up: ConversationFollowUpState | None = None,
    media_ready: bool | None = None,
    media_readiness_status: str | None = None,
) -> CustomerConversationState:
    extras: dict = {}
    if media_ready is not None:
        extras["media_ready"] = media_ready
    if media_readiness_status is not None:
        extras["media_readiness_status"] = media_readiness_status
    return CustomerConversationState(reply=reply, follow_up=follow_up, **extras)


def _unresolved_reply(message_id: int = 1) -> ConversationReplyState:
    return ConversationReplyState(
        unresolved_customer_message_ids=[message_id],
        latest_unresolved_customer_message_id=message_id,
        seller_responded_after_latest_customer=False,
    )


def _resolved_reply(message_id: int = 2) -> ConversationReplyState:
    return ConversationReplyState(
        unresolved_customer_message_ids=[],
        latest_unresolved_customer_message_id=None,
        seller_responded_after_latest_customer=True,
        seller_response_message_id=message_id,
    )


async def test_attention_flagged_wins_over_all_other_signals():
    state = _state(
        reply=_unresolved_reply(),
        follow_up=ConversationFollowUpState(
            status="due",
            due_at="2026-03-30T00:00:00+00:00",
            waiting_for="seller",
        ),
    )

    nba = project_next_best_action(state, needs_attention=True)

    assert nba.action == "attention_flagged"
    assert nba.ready is True


async def test_follow_up_due_outranks_reply_to_customer():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    state = _state(
        reply=_unresolved_reply(),
        follow_up=ConversationFollowUpState(
            status="due",
            due_at=past,
            waiting_for="seller",
        ),
    )

    nba = project_next_best_action(state)

    assert nba.action == "follow_up_due"
    assert nba.ready is True


async def test_reply_to_customer_when_unresolved_tail_and_media_ready_true():
    state = _state(reply=_unresolved_reply(), media_ready=True)

    nba = project_next_best_action(state)

    assert nba.action == "reply_to_customer"
    assert nba.ready is True


async def test_reply_to_customer_ready_when_no_media_state():
    state = _state(reply=_unresolved_reply())

    nba = project_next_best_action(state)

    assert nba.action == "reply_to_customer"
    assert nba.ready is True


async def test_reply_to_customer_waits_on_media_when_hydration_pending():
    state = _state(reply=_unresolved_reply(), media_ready=False)

    nba = project_next_best_action(state)

    assert nba.action == "reply_to_customer"
    assert nba.ready is False
    assert nba.reason == "waiting_on_media_hydration"


async def test_reply_to_customer_ready_when_media_unavailable():
    # Terminal unavailable no longer blocks: the agent is ready to reply honestly
    # that it could not open the media (silent-stall trust fix). Only still-pending
    # hydration keeps the next-best-action not-ready.
    state = _state(
        reply=_unresolved_reply(),
        media_ready=False,
        media_readiness_status="unavailable",
    )

    nba = project_next_best_action(state)

    assert nba.action == "reply_to_customer"
    assert nba.ready is True
    assert nba.reason == "unresolved_customer_tail"


async def test_wait_on_customer_reply_when_seller_responded_and_obligation_waits_on_customer():
    state = _state(
        reply=_resolved_reply(),
        follow_up=ConversationFollowUpState(
            status="pending",
            due_at=(datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
            waiting_for="customer",
        ),
    )

    nba = project_next_best_action(state)

    assert nba.action == "wait_on_customer_reply"
    assert nba.ready is False


async def test_send_follow_up_to_customer_when_obligation_ready_and_waits_on_seller():
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    state = _state(
        reply=_resolved_reply(),
        follow_up=ConversationFollowUpState(
            status="due",
            due_at=past,
            waiting_for="seller",
        ),
    )

    nba = project_next_best_action(state)

    assert nba.action == "follow_up_due"
    assert nba.ready is True


async def test_conversation_settled_when_no_tail_and_no_follow_up():
    state = _state(reply=_resolved_reply())

    nba = project_next_best_action(state)

    assert nba.action == "conversation_settled"
    assert nba.ready is True


async def test_override_mode_off_blocks_reply_action():
    state = _state(reply=_unresolved_reply())

    nba = project_next_best_action(state, override_mode="off")

    assert nba.action == "reply_to_customer"
    assert nba.ready is False
    assert nba.reason == "agent_actions_disabled"


async def test_projection_includes_reason_for_observability():
    state = _state(reply=_unresolved_reply(), media_ready=True)

    nba = project_next_best_action(state)

    assert nba.reason  # non-empty string
