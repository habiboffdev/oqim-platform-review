from datetime import datetime, timezone

from app.models.message import Message, SenderType
from app.services.conversation_state import (
    ConversationDialogState,
    ConversationFollowUpState,
    ConversationReplyState,
    ConversationSyncState,
    get_customer_conversation_state,
    project_crm_stage,
    project_dialog_last_message_text,
    project_dialog_unread_count,
    project_message_preview_text,
    refresh_customer_conversation_state,
    resolved_pipeline_stage,
    resolved_products_interested,
    set_customer_conversation_state,
)


async def test_customer_conversation_state_round_trip_preserves_unknown_fields(conversation):
    conversation.crm_state = {
        "lead_score": 4.0,
        "pipeline_stage": "qualified",
        "sync": {
            "last_recovered_tail_trigger_message_id": 42,
            "custom_flag": True,
        },
        "follow_up": {
            "kind": "quote_stall",
            "status": "pending",
        },
    }

    state = get_customer_conversation_state(conversation)
    assert state.pipeline_stage == "qualified"
    assert state.sync is not None
    assert state.sync.last_recovered_tail_trigger_message_id == 42
    assert state.model_extra["lead_score"] == 4.0

    state.follow_up = ConversationFollowUpState(kind="negotiation_stall", status="due")
    state.sync = ConversationSyncState(
        last_recovered_tail_trigger_message_id=43,
    )
    set_customer_conversation_state(conversation, state)

    assert conversation.crm_state["lead_score"] == 4.0
    assert conversation.crm_state["pipeline_stage"] == "qualified"
    assert conversation.crm_state["sync"]["last_recovered_tail_trigger_message_id"] == 43
    assert conversation.crm_state["follow_up"]["kind"] == "negotiation_stall"


async def test_derive_reply_state_respects_chronological_order_during_backfill(
    db_session,
    conversation,
):
    """Ultrareview bug_003: during recovery/backfill a customer message can
    persist with a higher id but an older telegram_timestamp than a seller's
    later reply. derive_conversation_reply_state must use positional order
    (matching the caller's sort) rather than raw id comparisons."""
    from app.services.conversation_state import derive_conversation_reply_state

    live_customer = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="live customer msg",
        created_at=datetime.now(timezone.utc),
    )
    live_seller = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.SELLER.value,
        content="live seller reply",
        created_at=datetime.now(timezone.utc),
    )
    backfilled_customer = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="older customer msg backfilled later",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([live_customer, live_seller, backfilled_customer])
    await db_session.flush()

    # Caller sorts by telegram_timestamp: backfilled customer is
    # chronologically BEFORE the seller reply but persisted afterwards, so it
    # has a higher id. Messages arrive at derive_* already sorted.
    chronological_order = [backfilled_customer, live_customer, live_seller]

    reply = derive_conversation_reply_state(chronological_order)

    assert reply is not None
    assert reply.seller_responded_after_latest_customer is True, (
        "seller replied chronologically after all customer messages — "
        "id-based comparison misreports unresolved tail"
    )
    assert reply.latest_unresolved_customer_message_id is None
    assert reply.unresolved_customer_message_ids == []
    assert reply.seller_response_message_id == live_seller.id


async def test_derive_reply_state_uses_telegram_id_for_same_second_burst_tail(
    db_session,
    conversation,
):
    from app.services.conversation_state import derive_conversation_reply_state

    telegram_second = datetime(2026, 6, 6, 16, 28, 56, tzinfo=timezone.utc)
    greeting = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="Assalomu alaykum",
        telegram_message_id=1628,
        telegram_timestamp=telegram_second,
        created_at=datetime(2026, 6, 6, 16, 28, 56, 681402, tzinfo=timezone.utc),
    )
    price = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="narxi qancha",
        telegram_message_id=1630,
        telegram_timestamp=telegram_second,
        created_at=datetime(2026, 6, 6, 16, 28, 57, 91841, tzinfo=timezone.utc),
    )
    availability = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="sat bormi",
        telegram_message_id=1629,
        telegram_timestamp=telegram_second,
        created_at=datetime(2026, 6, 6, 16, 28, 57, 596334, tzinfo=timezone.utc),
    )
    db_session.add_all([greeting, price, availability])
    await db_session.flush()

    reply = derive_conversation_reply_state([greeting, price, availability])

    assert reply is not None
    assert reply.unresolved_customer_message_ids == [
        greeting.id,
        availability.id,
        price.id,
    ]
    assert reply.latest_unresolved_customer_message_id == price.id


async def test_refresh_customer_conversation_state_derives_unresolved_tail_and_seller_response(
    db_session,
    conversation,
):
    first_customer = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="Salom",
        created_at=datetime.now(timezone.utc),
    )
    second_customer = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.CUSTOMER.value,
        content="Narxi qancha?",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([first_customer, second_customer])
    await db_session.flush()

    unresolved = await refresh_customer_conversation_state(
        conversation,
        messages=[first_customer, second_customer],
    )

    assert unresolved.reply == ConversationReplyState(
        unresolved_customer_message_ids=[first_customer.id, second_customer.id],
        latest_unresolved_customer_message_id=second_customer.id,
        seller_responded_after_latest_customer=False,
        seller_response_message_id=None,
    )
    assert conversation.crm_state["reply"]["latest_unresolved_customer_message_id"] == second_customer.id

    seller_message = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.SELLER.value,
        content="Ha, bor",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(seller_message)
    await db_session.flush()

    resolved = await refresh_customer_conversation_state(
        conversation,
        messages=[first_customer, second_customer, seller_message],
    )

    assert resolved.reply == ConversationReplyState(
        unresolved_customer_message_ids=[],
        latest_unresolved_customer_message_id=None,
        seller_responded_after_latest_customer=True,
        seller_response_message_id=seller_message.id,
    )
    assert conversation.crm_state["reply"]["seller_responded_after_latest_customer"] is True
    assert conversation.crm_state["reply"]["seller_response_message_id"] == seller_message.id


async def test_set_customer_conversation_state_mirrors_legacy_cache_columns(conversation):
    state = get_customer_conversation_state(conversation)
    state.pipeline_stage = "qualified"
    state.products_interested = ["iPhone 15 Pro", "AirPods Pro"]

    set_customer_conversation_state(conversation, state)

    assert conversation.crm_state["pipeline_stage"] == "qualified"
    assert conversation.crm_state["products_interested"] == ["iPhone 15 Pro", "AirPods Pro"]
    assert conversation.pipeline_stage == "qualified"
    assert conversation.products_mentioned == ["iPhone 15 Pro", "AirPods Pro"]


async def test_crm_stage_projection_normalizes_legacy_aliases_in_canonical_state(conversation):
    conversation.crm_state = {
        "pipeline_stage": "talking",
        "lead_score": 0.77,
        "last_intent": "asked_price",
        "field_provenance": {"pipeline_stage": "llm"},
    }

    stage = project_crm_stage(conversation)

    assert stage.stage == "qualified"
    assert stage.source == "crm_state"
    assert stage.raw_stage == "talking"
    assert stage.normalized_from == "talking"
    assert stage.confidence == 0.77
    assert stage.last_intent == "asked_price"
    assert stage.field_provenance["pipeline_stage"] == "llm"


async def test_crm_stage_projection_does_not_turn_order_words_into_won(conversation):
    conversation.crm_state = {
        "pipeline_stage": "buyurtma",
        "field_provenance": {"pipeline_stage": "legacy_alias"},
    }

    stage = project_crm_stage(conversation)

    assert stage.stage == "new"
    assert stage.raw_stage == "buyurtma"
    assert stage.normalized_from == "buyurtma"


async def test_resolved_helpers_ignore_legacy_fields_when_canonical_state_missing(conversation):
    conversation.crm_state = None
    conversation.pipeline_stage = "won"
    conversation.products_mentioned = ["Legacy-only product"]

    assert resolved_pipeline_stage(conversation) == "new"
    assert resolved_products_interested(conversation) == []


async def test_project_dialog_preview_prefers_newer_canonical_state(conversation):
    local_at = datetime(2026, 4, 20, 8, 0, tzinfo=timezone.utc)
    state = get_customer_conversation_state(conversation)
    state.sync = ConversationSyncState(
        dialog=ConversationDialogState(
            telegram_unread_count=5,
            last_message_text="Fresh Telegram preview",
            last_message_date="2026-04-20T08:05:00+00:00",
        )
    )
    set_customer_conversation_state(conversation, state)

    assert project_dialog_unread_count(conversation) == 5
    assert (
        project_dialog_last_message_text(
            conversation,
            local_text="Old local row",
            local_at=local_at,
        )
        == "Fresh Telegram preview"
    )


async def test_project_dialog_preview_keeps_newer_local_row(conversation):
    local_at = datetime(2026, 4, 20, 8, 10, tzinfo=timezone.utc)
    state = get_customer_conversation_state(conversation)
    state.sync = ConversationSyncState(
        dialog=ConversationDialogState(
            telegram_unread_count=1,
            last_message_text="Older Telegram preview",
            last_message_date="2026-04-20T08:05:00+00:00",
        )
    )
    set_customer_conversation_state(conversation, state)

    assert (
        project_dialog_last_message_text(
            conversation,
            local_text="Fresh local row",
            local_at=local_at,
        )
        == "Fresh local row"
    )


async def test_project_message_preview_uses_media_label_for_empty_media_tail():
    assert project_message_preview_text("", media_type="document") == "Fayl"
    assert project_message_preview_text(None, media_type="photo") == "Rasm"
    assert project_message_preview_text("  salom  ", media_type="document") == "salom"
    assert project_message_preview_text("", media_type=None) == ""
