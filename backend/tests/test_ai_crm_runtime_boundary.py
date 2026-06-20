"""Sprint 7 #195 — AI CRM runtime boundary tests.

Verifies the canonical AI CRM runtime through canonical message events and
durable state/NBA outputs. Focuses on externally visible behavior — replay
idempotence, seller-response clearing, follow-up projection, business-state
derivation, manual-override survival — rather than internal dict shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, SenderType
from app.services.conversation_state import (
    ConversationFollowUpState,
    apply_manual_field_override,
    get_customer_conversation_state,
    project_next_best_action,
    refresh_customer_conversation_state,
    set_customer_conversation_state,
)


def _customer(conversation_id: int, content: str) -> Message:
    return Message(
        conversation_id=conversation_id,
        sender_type=SenderType.CUSTOMER.value,
        content=content,
        created_at=datetime.now(timezone.utc),
    )


def _seller(conversation_id: int, content: str) -> Message:
    return Message(
        conversation_id=conversation_id,
        sender_type=SenderType.SELLER.value,
        content=content,
        created_at=datetime.now(timezone.utc),
    )


async def test_replay_same_events_converges_to_identical_state(
    conversation, db_session: AsyncSession
):
    """PRD: state derivation must be idempotent — replaying the same canonical
    event batch twice must produce the same state."""
    customer_msg = _customer(conversation.id, "iPhone 15 Pro narxi qancha?")
    db_session.add(customer_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[customer_msg])
    first = get_customer_conversation_state(conversation).model_dump()

    await refresh_customer_conversation_state(conversation, messages=[customer_msg])
    second = get_customer_conversation_state(conversation).model_dump()

    assert first == second


async def test_seller_response_clears_unresolved_tail_and_settles_nba(
    conversation, db_session: AsyncSession
):
    """Canonical sequence: customer asks, seller answers, tail resolves, NBA
    transitions from reply_to_customer to conversation_settled."""
    customer_msg = _customer(conversation.id, "Narxi qancha?")
    db_session.add(customer_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[customer_msg])
    state_before = get_customer_conversation_state(conversation)
    nba_before = project_next_best_action(
        state_before,
        needs_attention=bool(conversation.needs_attention),
        override_mode=conversation.override_mode or "auto",
    )
    assert nba_before.action == "reply_to_customer"
    assert nba_before.ready is True

    seller_msg = _seller(conversation.id, "12 mln")
    db_session.add(seller_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[customer_msg, seller_msg])
    state_after = get_customer_conversation_state(conversation)
    nba_after = project_next_best_action(
        state_after,
        needs_attention=bool(conversation.needs_attention),
        override_mode=conversation.override_mode or "auto",
    )
    assert state_after.reply.seller_responded_after_latest_customer is True
    assert nba_after.action == "conversation_settled"


async def test_due_follow_up_dominates_reply_signal_in_nba(
    conversation, db_session: AsyncSession
):
    """When an obligation is due AND the customer has an unresolved tail,
    the NBA must point at the follow-up first — the seller's attention has
    a deadline on the obligation, not the unanswered message."""
    customer_msg = _customer(conversation.id, "Salom, savolim bor")
    db_session.add(customer_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[customer_msg])

    state = get_customer_conversation_state(conversation)
    past_due = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    state.follow_up = ConversationFollowUpState(
        kind="promise_callback",
        status="due",
        due_at=past_due,
        waiting_for="seller",
    )
    set_customer_conversation_state(conversation, state)

    reloaded = get_customer_conversation_state(conversation)
    nba = project_next_best_action(reloaded)
    assert nba.action == "follow_up_due"
    assert nba.ready is True


async def test_media_hydration_pending_blocks_reply_readiness(
    conversation, db_session: AsyncSession
):
    """Unresolved tail with a pending voice message: NBA correctly surfaces
    reply_to_customer with ready=False and an explainable reason."""
    voice_msg = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="",
        media_type="voice",
        media_metadata={"ai_relevant": True, "hydration_status": "pending"},
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(voice_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[voice_msg])

    state = get_customer_conversation_state(conversation)
    nba = project_next_best_action(state)
    assert nba.action == "reply_to_customer"
    assert nba.ready is False
    assert nba.reason == "waiting_on_media_hydration"


async def test_pipeline_projection_no_longer_uses_inline_semantic_extraction(
    conversation, db_session: AsyncSession
):
    """Conversation recompute is deterministic; AutoCRM owns semantic stage changes."""
    conversation.pipeline_stage = "new"
    db_session.add(conversation)

    qualify_msg = _customer(conversation.id, "iPhone 15 bormi? narxi qancha?")
    db_session.add(qualify_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[qualify_msg])
    assert conversation.pipeline_stage == "new"

    neutral_msg = _customer(conversation.id, "Rahmat")
    db_session.add(neutral_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(
        conversation,
        messages=[qualify_msg, neutral_msg],
    )
    assert conversation.pipeline_stage == "new"


async def test_seller_pipeline_override_survives_multiple_refresh_cycles(
    conversation, db_session: AsyncSession
):
    """#191 boundary: seller's manual override for pipeline_stage survives an
    unbounded number of derivation cycles until explicitly cleared."""
    conversation.pipeline_stage = "negotiation"
    state = get_customer_conversation_state(conversation)
    apply_manual_field_override(state, field="pipeline_stage", value="negotiation")
    set_customer_conversation_state(conversation, state)
    db_session.add(conversation)

    for idx in range(4):
        probe_msg = _customer(conversation.id, f"iPhone narxi qancha? (probe {idx})")
        db_session.add(probe_msg)
        await db_session.flush()
        await refresh_customer_conversation_state(conversation, messages=[probe_msg])
        assert conversation.pipeline_stage == "negotiation"
        reloaded = get_customer_conversation_state(conversation)
        assert reloaded.field_provenance["pipeline_stage"] == "seller"


async def test_deleted_message_does_not_count_toward_unresolved_tail(
    conversation, db_session: AsyncSession
):
    """Mutation runtime boundary: a deleted customer message cannot keep the
    NBA stuck on reply_to_customer."""
    original = _customer(conversation.id, "Narxi qancha?")
    seller_message = _seller(conversation.id, "12 mln")
    deleted = _customer(conversation.id, "bekor qilaman")
    deleted.is_deleted = True
    db_session.add_all([original, seller_message, deleted])
    await db_session.flush()

    await refresh_customer_conversation_state(
        conversation,
        messages=[original, seller_message, deleted],
    )

    state = get_customer_conversation_state(conversation)
    nba = project_next_best_action(state)
    assert state.reply.seller_responded_after_latest_customer is True
    assert nba.action == "conversation_settled"
