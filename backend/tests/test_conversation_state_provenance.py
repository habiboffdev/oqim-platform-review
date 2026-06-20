from __future__ import annotations

from app.services.conversation_state import (
    apply_derived_field_update,
    apply_manual_field_override,
    get_customer_conversation_state,
    set_customer_conversation_state,
)


async def test_fresh_state_has_empty_field_provenance(conversation):
    state = get_customer_conversation_state(conversation)
    assert state.field_provenance == {}


async def test_derived_update_writes_value_and_stamps_ai_provenance(conversation):
    state = get_customer_conversation_state(conversation)

    applied = apply_derived_field_update(
        state,
        field="pipeline_stage",
        value="qualified",
        source="ai",
    )

    assert applied is True
    assert state.pipeline_stage == "qualified"
    assert state.field_provenance == {"pipeline_stage": "ai"}


async def test_derived_update_skips_field_with_seller_provenance(conversation):
    state = get_customer_conversation_state(conversation)
    apply_manual_field_override(state, field="pipeline_stage", value="won")

    applied = apply_derived_field_update(
        state,
        field="pipeline_stage",
        value="qualified",
        source="ai",
    )

    assert applied is False
    assert state.pipeline_stage == "won"
    assert state.field_provenance["pipeline_stage"] == "seller"


async def test_manual_override_overwrites_ai_derived_value(conversation):
    state = get_customer_conversation_state(conversation)
    apply_derived_field_update(state, field="urgency", value=False, source="ai")
    assert state.field_provenance["urgency"] == "ai"

    apply_manual_field_override(state, field="urgency", value=True)

    assert state.urgency is True
    assert state.field_provenance["urgency"] == "seller"


async def test_derived_update_allows_source_to_be_system_heuristic(conversation):
    """Non-AI system writes (e.g. simple rules) stamp their own source so
    later readers can explain why a value was set."""
    state = get_customer_conversation_state(conversation)

    apply_derived_field_update(
        state,
        field="last_intent",
        value="price_inquiry",
        source="system",
    )

    assert state.last_intent == "price_inquiry"
    assert state.field_provenance["last_intent"] == "system"


async def test_derived_update_to_extensible_field_uses_model_extra(conversation):
    """Future CRM fields (e.g. media_ready in #192) land on the open schema
    via model_extra; provenance still records the source."""
    state = get_customer_conversation_state(conversation)

    apply_derived_field_update(
        state,
        field="media_ready",
        value=True,
        source="ai",
    )

    assert state.model_extra["media_ready"] is True
    assert state.field_provenance["media_ready"] == "ai"


async def test_field_provenance_round_trips_through_conversation_crm_state(conversation):
    state = get_customer_conversation_state(conversation)
    apply_derived_field_update(state, field="pipeline_stage", value="negotiation", source="ai")
    apply_manual_field_override(state, field="urgency", value=True)
    set_customer_conversation_state(conversation, state)

    assert conversation.crm_state["pipeline_stage"] == "negotiation"
    assert conversation.crm_state["urgency"] is True
    assert conversation.crm_state["field_provenance"] == {
        "pipeline_stage": "ai",
        "urgency": "seller",
    }

    reloaded = get_customer_conversation_state(conversation)
    assert reloaded.field_provenance == {
        "pipeline_stage": "ai",
        "urgency": "seller",
    }
