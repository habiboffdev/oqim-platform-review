from __future__ import annotations

from datetime import datetime, timezone

from app.models.message import Message, SenderType
from app.services.conversation_state import (
    apply_derived_field_update,
    apply_manual_field_override,
    get_customer_conversation_state,
    refresh_customer_conversation_state,
    set_customer_conversation_state,
)


def _make_customer_message(conversation_id: int, content: str) -> Message:
    return Message(
        conversation_id=conversation_id,
        sender_type=SenderType.CUSTOMER.value,
        content=content,
        created_at=datetime.now(timezone.utc),
    )


async def test_refresh_preserves_legacy_unknown_fields_in_crm_state(conversation, db_session):
    """A legacy/background-job field living on the open schema must survive a
    full re-derivation — this is the #191 safe-merge guarantee."""
    conversation.crm_state = {
        "lead_score": 4.0,
        "legacy_custom_field": {"written_by": "run_lead_decay"},
    }

    customer_msg = _make_customer_message(conversation.id, "Narxi qancha?")
    db_session.add(customer_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[customer_msg])

    assert conversation.crm_state["lead_score"] == 4.0
    assert conversation.crm_state["legacy_custom_field"] == {"written_by": "run_lead_decay"}
    assert conversation.crm_state["pipeline_stage"] == "new"


async def test_refresh_preserves_existing_follow_up_projection(conversation, db_session):
    """Safe-merge also means structured sibling subtrees (follow_up, sync) are
    not clobbered when the runtime recomputes reply/pipeline/urgency."""
    conversation.crm_state = {
        "follow_up": {
            "kind": "quote_stall",
            "status": "pending",
            "reason_code": "quote_sent_waiting_customer",
        },
        "sync": {
            "last_recovered_tail_trigger_message_id": 99,
        },
    }

    customer_msg = _make_customer_message(conversation.id, "Tezroq kerak, bugun olaman")
    db_session.add(customer_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(conversation, messages=[customer_msg])

    assert conversation.crm_state["follow_up"]["kind"] == "quote_stall"
    assert conversation.crm_state["follow_up"]["reason_code"] == "quote_sent_waiting_customer"
    assert conversation.crm_state["sync"]["last_recovered_tail_trigger_message_id"] == 99


async def test_seller_override_round_trips_through_recompute_cycle(conversation, db_session):
    """Seller-owned state survives deterministic recompute cycles."""
    conversation.pipeline_stage = "new"
    db_session.add(conversation)

    customer_msg = _make_customer_message(conversation.id, "Narxi qancha?")
    db_session.add(customer_msg)
    await db_session.flush()

    # Seller flips to negotiation manually.
    state = get_customer_conversation_state(conversation)
    apply_manual_field_override(state, field="pipeline_stage", value="negotiation")
    set_customer_conversation_state(conversation, state)
    conversation.pipeline_stage = "negotiation"

    # Recompute must not clobber seller-owned state.
    another_customer_msg = _make_customer_message(conversation.id, "Narxi chegirma bilan qancha?")
    db_session.add(another_customer_msg)
    await db_session.flush()

    await refresh_customer_conversation_state(
        conversation,
        messages=[customer_msg, another_customer_msg],
    )

    reloaded = get_customer_conversation_state(conversation)
    assert conversation.pipeline_stage == "negotiation"
    assert reloaded.pipeline_stage == "negotiation"
    assert reloaded.field_provenance["pipeline_stage"] == "seller"

    # A future AutoCRM confirmation can still write after override is cleared.
    reloaded.field_provenance.pop("pipeline_stage", None)
    applied = apply_derived_field_update(
        reloaded,
        field="pipeline_stage",
        value="qualified",
        source="ai",
    )
    assert applied is True
    assert reloaded.pipeline_stage == "qualified"
    assert reloaded.field_provenance["pipeline_stage"] == "ai"
