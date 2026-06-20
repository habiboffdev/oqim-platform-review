from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.triggers.contracts import (
    Phase3TriggerDefinition,
    TriggerKind,
    TriggerRunMode,
)


def test_phase3_trigger_definition_maps_message_to_existing_trigger_input() -> None:
    definition = Phase3TriggerDefinition(
        owner_agent_id=2,
        kind=TriggerKind.MESSAGE,
        run_mode=TriggerRunMode.REPLY,
        event_filters={"channel": "telegram_dm"},
        idempotency_scope={"conversation": "same"},
    )

    payload = definition.to_trigger_input()

    assert payload.event_source == "channel_message_received"
    assert payload.action_proposal_type == "hermes.reply"
    assert payload.matching_scope["channel"] == "telegram_dm"
    assert payload.matching_scope["phase3"]["run_mode"] == "reply"


def test_phase3_trigger_definition_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Phase3TriggerDefinition(
            owner_agent_id=2,
            kind=TriggerKind.MESSAGE,
            nope=True,  # type: ignore[arg-type]
        )
