from __future__ import annotations

from datetime import datetime, timezone

from app.models.message import Message, SenderType
from app.services.conversation_state import (
    MEDIA_READINESS_NOT_APPLICABLE,
    MEDIA_READINESS_PENDING,
    MEDIA_READINESS_READY,
    MEDIA_READINESS_UNAVAILABLE,
    apply_derived_field_update,
    apply_manual_field_override,
    derive_media_readiness_from_messages,
    derive_media_readiness_status_from_messages,
    get_customer_conversation_state,
    project_media_readiness_block_reason,
    refresh_customer_conversation_state,
    set_customer_conversation_state,
)


def _media_message(
    conversation_id: int,
    *,
    media_type: str,
    ai_relevant: bool,
    hydration_status: str,
) -> Message:
    return Message(
        conversation_id=conversation_id,
        sender_type=SenderType.CUSTOMER.value,
        content="",
        media_type=media_type,
        media_metadata={
            "ai_relevant": ai_relevant,
            "hydration_status": hydration_status,
        },
        created_at=datetime.now(timezone.utc),
    )


async def test_media_readiness_returns_none_when_no_ai_relevant_media(conversation):
    messages = [
        Message(
            conversation_id=conversation.id,
            sender_type=SenderType.CUSTOMER.value,
            content="Salom",
            created_at=datetime.now(timezone.utc),
        ),
    ]

    assert derive_media_readiness_from_messages(messages) is None
    assert derive_media_readiness_status_from_messages(messages) == MEDIA_READINESS_NOT_APPLICABLE


async def test_media_readiness_true_when_all_relevant_messages_hydrated(conversation):
    messages = [
        _media_message(conversation.id, media_type="voice", ai_relevant=True, hydration_status="hydrated"),
        _media_message(conversation.id, media_type="photo", ai_relevant=True, hydration_status="hydrated"),
    ]

    assert derive_media_readiness_from_messages(messages) is True
    assert derive_media_readiness_status_from_messages(messages) == MEDIA_READINESS_READY


async def test_media_readiness_false_when_any_relevant_message_pending(conversation):
    messages = [
        _media_message(conversation.id, media_type="voice", ai_relevant=True, hydration_status="hydrated"),
        _media_message(conversation.id, media_type="photo", ai_relevant=True, hydration_status="pending"),
    ]

    assert derive_media_readiness_from_messages(messages) is False
    assert derive_media_readiness_status_from_messages(messages) == MEDIA_READINESS_PENDING


async def test_media_readiness_false_on_unavailable_status(conversation):
    messages = [
        _media_message(conversation.id, media_type="voice", ai_relevant=True, hydration_status="unavailable"),
    ]

    assert derive_media_readiness_from_messages(messages) is False
    assert derive_media_readiness_status_from_messages(messages) == MEDIA_READINESS_UNAVAILABLE


async def test_media_readiness_ignores_non_ai_relevant_media(conversation):
    """A sticker or document that is explicitly not AI-relevant does not hold
    up readiness — still None when no AI-relevant media exists."""
    messages = [
        _media_message(conversation.id, media_type="sticker", ai_relevant=False, hydration_status="not_applicable"),
    ]

    assert derive_media_readiness_from_messages(messages) is None
    assert derive_media_readiness_status_from_messages(messages) == MEDIA_READINESS_NOT_APPLICABLE


async def test_refresh_state_stamps_system_provenance_when_media_readiness_derived(
    conversation, db_session
):
    hydrated_voice = _media_message(
        conversation.id,
        media_type="voice",
        ai_relevant=True,
        hydration_status="hydrated",
    )
    db_session.add(hydrated_voice)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[hydrated_voice])

    state = get_customer_conversation_state(conversation)
    assert state.model_extra["media_ready"] is True
    assert state.field_provenance["media_ready"] == "system"
    assert state.model_extra["media_readiness_status"] == MEDIA_READINESS_READY
    assert state.field_provenance["media_readiness_status"] == "system"


async def test_refresh_state_leaves_media_readiness_unset_when_no_relevant_media(
    conversation, db_session
):
    neutral_msg = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="Salom",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(neutral_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[neutral_msg])

    state = get_customer_conversation_state(conversation)
    assert (state.model_extra or {}).get("media_ready") is None
    assert state.model_extra["media_readiness_status"] == MEDIA_READINESS_NOT_APPLICABLE
    assert state.field_provenance["media_readiness_status"] == "system"
    assert "media_ready" not in state.field_provenance


async def test_refresh_state_preserves_seller_media_readiness_override(conversation, db_session):
    state = get_customer_conversation_state(conversation)
    apply_manual_field_override(state, field="media_ready", value=True)
    set_customer_conversation_state(conversation, state)

    pending_voice = _media_message(
        conversation.id,
        media_type="voice",
        ai_relevant=True,
        hydration_status="pending",
    )
    db_session.add(pending_voice)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[pending_voice])

    reloaded = get_customer_conversation_state(conversation)
    assert reloaded.model_extra["media_ready"] is True
    assert reloaded.field_provenance["media_ready"] == "seller"
    assert reloaded.model_extra["media_readiness_status"] == MEDIA_READINESS_PENDING


async def test_block_reason_blocks_while_media_pending(conversation, db_session):
    msg = _media_message(conversation.id, media_type="photo", ai_relevant=True, hydration_status="pending")
    db_session.add(msg)
    await db_session.flush()
    await refresh_customer_conversation_state(conversation, messages=[msg])
    state = get_customer_conversation_state(conversation)
    assert project_media_readiness_block_reason(state) == "awaiting_media_hydration"


async def test_block_reason_does_not_block_on_terminal_unavailable(conversation, db_session):
    # The hydration worker has given up retrying; blocking forever would stall the
    # conversation. The agent should reply honestly instead — so no block reason.
    msg = _media_message(conversation.id, media_type="photo", ai_relevant=True, hydration_status="unavailable")
    db_session.add(msg)
    await db_session.flush()
    await refresh_customer_conversation_state(conversation, messages=[msg])
    state = get_customer_conversation_state(conversation)
    assert project_media_readiness_block_reason(state) is None


async def test_block_reason_none_when_media_ready(conversation, db_session):
    msg = _media_message(conversation.id, media_type="photo", ai_relevant=True, hydration_status="hydrated")
    db_session.add(msg)
    await db_session.flush()
    await refresh_customer_conversation_state(conversation, messages=[msg])
    state = get_customer_conversation_state(conversation)
    assert project_media_readiness_block_reason(state) is None


async def test_block_reason_none_when_not_applicable(conversation, db_session):
    msg = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="Salom",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(msg)
    await db_session.flush()
    await refresh_customer_conversation_state(conversation, messages=[msg])
    state = get_customer_conversation_state(conversation)
    assert project_media_readiness_block_reason(state) is None


async def test_block_reason_fallback_blocks_when_status_absent(conversation):
    # Legacy/missing-status path: be conservative and block (treat as still hydrating).
    state = get_customer_conversation_state(conversation)
    apply_manual_field_override(state, field="media_ready", value=False)
    assert project_media_readiness_block_reason(state) == "awaiting_media_hydration"


async def test_block_reason_status_authoritative_over_stale_media_ready_bool(conversation):
    """Regression (live conv 2791): an explicit ``media_readiness_status`` is
    authoritative over the legacy ``media_ready`` bool. ``not_applicable`` means
    "no AI-relevant media to wait for" and must never block, even when an earlier
    window left ``media_ready`` stale at False."""
    state = get_customer_conversation_state(conversation)
    apply_derived_field_update(
        state, field="media_readiness_status", value=MEDIA_READINESS_NOT_APPLICABLE
    )
    apply_derived_field_update(state, field="media_ready", value=False)

    assert project_media_readiness_block_reason(state) is None


async def test_block_reason_recovers_after_media_scrolls_out_of_window(conversation, db_session):
    """Live regression (conv 2791): terminal-``unavailable`` media persists
    ``media_ready=False``, then later text messages push all media out of the
    recent window so the derived status becomes ``not_applicable``. The
    conversation must NOT remain blocked forever on the stale bool."""
    media = _media_message(
        conversation.id, media_type="photo", ai_relevant=True, hydration_status="unavailable"
    )
    db_session.add(media)
    await db_session.flush()
    await refresh_customer_conversation_state(conversation, messages=[media])
    # unavailable media does not block (agent replies honestly) but persists media_ready=False
    assert project_media_readiness_block_reason(get_customer_conversation_state(conversation)) is None

    text = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="hello",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(text)
    await db_session.flush()
    await refresh_customer_conversation_state(conversation, messages=[text])

    state = get_customer_conversation_state(conversation)
    assert state.model_extra["media_readiness_status"] == MEDIA_READINESS_NOT_APPLICABLE
    assert project_media_readiness_block_reason(state) is None


async def test_refresh_clears_stale_system_media_ready_on_not_applicable(conversation, db_session):
    """Defense-in-depth: when the window rolls past all media (status becomes
    ``not_applicable``), a previously system-derived ``media_ready=False`` must be
    cleared so the canonical status and the legacy bool can never contradict."""
    media = _media_message(
        conversation.id, media_type="photo", ai_relevant=True, hydration_status="pending"
    )
    db_session.add(media)
    await db_session.flush()
    await refresh_customer_conversation_state(conversation, messages=[media])
    assert get_customer_conversation_state(conversation).model_extra["media_ready"] is False

    text = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="hello",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(text)
    await db_session.flush()
    await refresh_customer_conversation_state(conversation, messages=[text])

    state = get_customer_conversation_state(conversation)
    assert state.model_extra.get("media_ready") is not False


async def test_refresh_preserves_seller_media_ready_override_on_not_applicable(
    conversation, db_session
):
    """A seller-owned ``media_ready`` survives the not_applicable transition — the
    defense-in-depth clear only touches system/AI-derived values."""
    state = get_customer_conversation_state(conversation)
    apply_manual_field_override(state, field="media_ready", value=False)
    set_customer_conversation_state(conversation, state)

    text = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="hello",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(text)
    await db_session.flush()
    await refresh_customer_conversation_state(conversation, messages=[text])

    reloaded = get_customer_conversation_state(conversation)
    assert reloaded.model_extra["media_ready"] is False
    assert reloaded.field_provenance["media_ready"] == "seller"
