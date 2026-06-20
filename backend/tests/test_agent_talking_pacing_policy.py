from __future__ import annotations

from app.modules.agent_talking.contracts import (
    TalkAction,
    TalkActionKind,
    TalkBundle,
    TalkingMode,
    TalkingPolicy,
)
from app.modules.agent_talking.pacing import compute_pacing_plan
from app.modules.agent_talking.policy import evaluate_talk_bundle


def _bundle(*actions: TalkAction, policy: TalkingPolicy | None = None) -> TalkBundle:
    return TalkBundle(
        workspace_id=1,
        agent_id=2,
        hermes_run_id="hermes_run:abc",
        conversation_id=3,
        channel_account_id="telegram:1",
        actions=list(actions),
        talking_policy_snapshot=policy or TalkingPolicy.seller_default(),
        confidence=0.9,
    )


def test_human_pacing_is_bounded_by_text_length() -> None:
    policy = TalkingPolicy(mode=TalkingMode.REPLY, pacing_profile="human")
    bundle = _bundle(
        TalkAction(kind=TalkActionKind.SEND_MSG, text="Salom", requires_scope="telegram.send_message"),
        TalkAction(
            kind=TalkActionKind.SEND_MSG,
            text="Starter coins 5 ta — 40 000 UZS",
            requires_scope="telegram.send_message",
        ),
        policy=policy,
    )

    plan = compute_pacing_plan(bundle)

    assert len(plan) == 2
    assert 500 <= plan[0].typing_ms <= 2600
    assert 350 <= plan[0].delay_after_ms <= 1400
    assert plan[1].typing_ms >= plan[0].typing_ms


def test_pacing_none_has_no_artificial_wait() -> None:
    policy = TalkingPolicy(mode=TalkingMode.REPLY, pacing_profile="none", typing_indicator="off")
    plan = compute_pacing_plan(
        _bundle(
            TalkAction(kind=TalkActionKind.SEND_MSG, text="Salom", requires_scope="telegram.send_message"),
            policy=policy,
        )
    )

    assert plan[0].typing_ms == 0
    assert plan[0].delay_after_ms == 0


def test_policy_blocks_too_many_bubbles() -> None:
    policy = TalkingPolicy(mode=TalkingMode.REPLY, max_bubbles_per_turn=1)
    decision = evaluate_talk_bundle(
        _bundle(
            TalkAction(kind=TalkActionKind.SEND_MSG, text="1", requires_scope="telegram.send_message"),
            TalkAction(kind=TalkActionKind.SEND_MSG, text="2", requires_scope="telegram.send_message"),
            policy=policy,
        ),
        granted_scopes={"telegram.send_message"},
        trust_mode="autopilot",
        confidence=0.9,
        auto_send_threshold=0.8,
    )

    assert decision.action == "blocked"
    assert "max_bubbles" in decision.reason


def test_policy_blocks_missing_grant() -> None:
    decision = evaluate_talk_bundle(
        _bundle(TalkAction(kind=TalkActionKind.SEND_MSG, text="Salom", requires_scope="telegram.send_message")),
        granted_scopes=set(),
        trust_mode="autopilot",
        confidence=0.9,
        auto_send_threshold=0.8,
    )

    assert decision.action == "blocked"
    assert decision.required_scopes == ["telegram.send_message"]


def test_delete_disabled_by_default() -> None:
    decision = evaluate_talk_bundle(
        _bundle(
            TalkAction(
                kind=TalkActionKind.DELETE_MESSAGE,
                target_message_ref="message:1",
                requires_scope="telegram.delete_message",
                risk_level="high",
            )
        ),
        granted_scopes={"telegram.delete_message"},
        trust_mode="autopilot",
        confidence=0.9,
        auto_send_threshold=0.8,
    )

    assert decision.action == "blocked"
    assert "delete_disabled" in decision.reason


def test_draft_mode_proposes_bundle() -> None:
    decision = evaluate_talk_bundle(
        _bundle(TalkAction(kind=TalkActionKind.SEND_MSG, text="Salom", requires_scope="telegram.send_message")),
        granted_scopes={"telegram.send_message"},
        trust_mode="disabled",
        confidence=0.99,
        auto_send_threshold=0.0,
    )

    assert decision.action == "propose"
    assert decision.reason == "trust_mode_disabled"


def test_full_autopilot_bundle_can_auto_send_when_policy_allows() -> None:
    decision = evaluate_talk_bundle(
        _bundle(TalkAction(kind=TalkActionKind.SEND_MSG, text="Salom", requires_scope="telegram.send_message")),
        granted_scopes={"telegram.send_message"},
        trust_mode="autopilot",
        confidence=0.99,
        auto_send_threshold=0.0,
    )

    assert decision.action == "auto_send"


def test_seller_pacing_reads_human_not_inverted():
    """Founder UX report (2026-06-10): with the 'fast' profile every bubble
    "typed" for <=0.9s, so a long second bubble materialized instantly after
    a short first one — inverted vs human typing. Sellers pace like humans:
    typing time grows with bubble length, visibly."""
    from app.modules.agent_talking.contracts import TalkAction, TalkActionKind, TalkBundle, TalkingPolicy
    from app.modules.agent_talking.pacing import _PROFILES, compute_pacing_plan

    assert TalkingPolicy.seller_default().pacing_profile == "human"
    human = _PROFILES["human"]
    # a ~180-char bubble must type for several seconds, not get capped flat
    assert human["max"] >= 5000
    assert human["base"] + 180 * human["per_char"] >= 4000

    bundle = TalkBundle(
        workspace_id=1,
        agent_id=2,
        hermes_run_id="hr:pace",
        trigger_ref="message:1",
        conversation_id=3,
        actions=[
            TalkAction(kind=TalkActionKind.SEND_MSG, text="Tushunarli, aka.", idempotency_key="a:0"),
            TalkAction(kind=TalkActionKind.SEND_MSG, text="x" * 180, idempotency_key="a:1"),
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )
    plans = compute_pacing_plan(bundle)
    # longer text -> strictly longer typing
    assert plans[1].typing_ms > plans[0].typing_ms * 2
