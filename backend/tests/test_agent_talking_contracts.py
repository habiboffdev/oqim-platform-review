from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.agent_talking.contracts import (
    TalkAction,
    TalkActionKind,
    TalkBundle,
    TalkingMode,
    TalkingPolicy,
)


def test_talking_policy_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        TalkingPolicy(mode=TalkingMode.REPLY, unknown=True)  # type: ignore[arg-type]


def test_seller_policy_defaults_allow_three_fast_short_bubbles_not_delete() -> None:
    policy = TalkingPolicy.seller_default()

    assert policy.mode == TalkingMode.REPLY
    assert policy.max_bubbles_per_turn == 3
    assert policy.max_chars_per_bubble == 220  # salesman brevity: short bubbles
    assert policy.allow_media is True
    assert policy.allow_delete is False
    assert policy.pacing_profile == "fast"


def test_for_agent_without_overrides_equals_seller_default() -> None:
    assert TalkingPolicy.for_agent() == TalkingPolicy.seller_default()


def test_for_agent_applies_only_supplied_overrides() -> None:
    policy = TalkingPolicy.for_agent(max_chars=300, allow_reaction=False)

    assert policy.max_chars_per_bubble == 300
    assert policy.allow_reaction is False
    # untouched fields keep seller defaults
    assert policy.max_bubbles_per_turn == 3
    assert policy.pacing_profile == "fast"


def test_for_agent_supports_bubble_and_pacing_overrides() -> None:
    policy = TalkingPolicy.for_agent(max_bubbles=2, pacing="human")

    assert policy.max_bubbles_per_turn == 2
    assert policy.pacing_profile == "human"
    assert policy.max_chars_per_bubble == 220


def test_emoji_usage_defaults_medium_and_is_owner_tunable() -> None:
    assert TalkingPolicy.seller_default().emoji_usage == "medium"

    policy = TalkingPolicy.for_agent(emoji_usage="low")
    assert policy.emoji_usage == "low"
    # other fields untouched
    assert policy.max_chars_per_bubble == 220

    with pytest.raises(ValidationError):
        TalkingPolicy.for_agent(emoji_usage="extreme")


def test_talk_bundle_serializes_stable_json() -> None:
    bundle = TalkBundle(
        workspace_id=1,
        agent_id=2,
        hermes_run_id="hermes_run:abc",
        conversation_id=3,
        channel_account_id="telegram:1",
        actions=[
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="Assalomu alaykum",
                requires_scope="telegram.send_message",
            )
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
        confidence=0.91,
    )

    dumped = bundle.model_dump(mode="json")

    assert dumped["schema_version"] == "talk_bundle.v1"
    assert dumped["actions"][0]["kind"] == "send_msg"
    assert dumped["actions"][0]["text"] == "Assalomu alaykum"
    assert dumped["talking_policy_snapshot"]["mode"] == "reply"
    assert "loop_guard_policy" not in dumped["talking_policy_snapshot"]
