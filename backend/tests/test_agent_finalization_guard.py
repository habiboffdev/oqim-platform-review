from __future__ import annotations

from typing import Any

import pytest

from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.faithfulness import FaithfulnessVerdict
from app.modules.agent_runtime_v2.finalization_guard import (
    finalize_customer_visible_reply,
)
from app.modules.agent_runtime_v2.reply_runtime import ReplyResult
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler
from app.modules.agent_runtime_v2.runtime_service import (
    AgentRuntimeService,
    _AgentTurnContext,
    _authority_query_text,
    _GatheredContext,
)
from app.modules.agent_talking.contracts import TalkAction, TalkActionKind, TalkBundle, TalkingPolicy
from app.modules.catalog_authority.contracts import CatalogAuthorityBundle


def test_unsupported_business_promise_is_critic_only_without_committed_action() -> None:
    verdict = FaithfulnessVerdict.model_validate(
        {
            "claims": [
                {
                    "claim": "I will send Click details now.",
                    "claim_type": "policy",
                    "supported": False,
                }
            ]
        }
    )

    result = finalize_customer_visible_reply(
        reply_text="I will send Click details now.",
        faithfulness=verdict,
        committed_action_refs=[],
    )

    assert result.customer_visible_text == "I will send Click details now."
    assert result.blocked is False
    assert result.reason_code == "unsupported_authority_observed"
    assert result.telemetry["mode"] == "critic_only"
    assert result.telemetry["committed_action_refs"] == []


def test_safe_handoff_reply_is_allowed_after_owner_task_notification_or_order_intent() -> None:
    verdict = FaithfulnessVerdict.model_validate(
        {
            "claims": [
                {
                    "claim": "Owner will send payment details.",
                    "claim_type": "policy",
                    "supported": False,
                }
            ]
        }
    )

    result = finalize_customer_visible_reply(
        reply_text="I asked the owner for the payment details. I’ll send them once confirmed.",
        faithfulness=verdict,
        committed_action_refs=[
            "owner_task:owner_task:123",
            "owner_notification:1:abc",
            "order_intent:1:abc",
        ],
    )

    assert result.customer_visible_text.startswith("I asked the owner")
    assert result.blocked is False
    assert result.reason_code == "unsupported_authority_with_committed_handoff"
    assert result.telemetry["committed_action_refs"] == [
        "owner_task:owner_task:123",
        "owner_notification:1:abc",
        "order_intent:1:abc",
    ]


def test_authority_query_uses_compact_session_state_without_expanding_history() -> None:
    query = _authority_query_text(
        customer_query_text="what is price",
        conversation_state={
            "active_intent": "ask_price",
            "selected_items": [{"product_ref": "catalog:satstation:starter-coins"}],
        },
        session_summary=(
            "Customer asked about platform and starter coin options. "
            "Main platform price is not confirmed."
        ),
    )

    assert "what is price" in query
    assert "ask_price" in query
    assert "catalog satstation starter-coins" in query
    assert "starter coin options" in query


@pytest.mark.asyncio
async def test_runtime_finalization_defers_faithfulness_without_blocking_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AgentConfig(
        agent_id=13,
        workspace_id=1,
        name="Generic seller",
        trust_mode="autopilot",
        auto_send_threshold=0.8,
        agent_md="Generic seller agent.",
    )
    profile = RuntimeProfileCompiler().compile_agent(
        config=config,
        agent_kind="seller",
    )

    async def fake_hermes_run(self, **_: Any) -> ReplyResult:
        return ReplyResult(
            reply_text="I will send payment details now.",
            confidence=0.0,
            grounding_hits=0,
        )

    async def fake_faithfulness(**_: Any) -> FaithfulnessVerdict:
        raise AssertionError("interactive reply path should not spend a verifier LLM call")

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        fake_hermes_run,
    )
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.runtime_service.judge_faithfulness",
        fake_faithfulness,
    )
    ctx = _AgentTurnContext(
        config=config,
        gathered=_GatheredContext(
            grounding=[],
            history=[],
            agent_kind="seller",
            voice_examples=[],
            authority_warnings=[],
            authority_bundle=CatalogAuthorityBundle(query="payment details"),
            runtime_profile=profile,
        ),
        agent_id=config.agent_id,
        customer_message="Can you send payment details?",
        customer_query_text="Can you send payment details?",
    )

    outcome = await AgentRuntimeService(session=None).run_from_context(ctx)  # type: ignore[arg-type]

    assert outcome.reply_text == "I will send payment details now."
    assert outcome.talk_bundle is None
    assert outcome.telemetry is not None
    assert outcome.telemetry["faithfulness"]["mode"] == "deferred_critic"
    assert outcome.telemetry["faithfulness"]["unsupported_authority_claims"] == 0
    assert outcome.telemetry["finalization"]["blocked"] is False
    assert outcome.telemetry["finalization"]["mode"] == "critic_only"
    assert outcome.telemetry["finalization"]["reason_code"] == "supported_or_no_authority_claim"


@pytest.mark.asyncio
async def test_runtime_preserves_talk_bundle_when_faithfulness_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AgentConfig(
        agent_id=13,
        workspace_id=1,
        name="Generic seller",
        trust_mode="autopilot",
        auto_send_threshold=0.8,
        agent_md="Generic seller agent.",
    )
    profile = RuntimeProfileCompiler().compile_agent(
        config=config,
        agent_kind="seller",
    )
    bundle = TalkBundle(
        workspace_id=1,
        agent_id=13,
        hermes_run_id="hermes_run:test",
        conversation_id=10,
        actions=[
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="Starter Coins package is 40 000 UZS.",
            )
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default(),
    )

    async def fake_hermes_run(self, **_: Any) -> ReplyResult:
        return ReplyResult(
            reply_text=bundle.text_preview(),
            confidence=0.0,
            grounding_hits=1,
            talk_bundle=bundle,
            tool_authority_lines=["[OFFER] Starter coins: 40 000 UZS"],
        )

    async def fake_faithfulness(**_: Any) -> FaithfulnessVerdict:
        raise AssertionError("interactive reply path should not spend a verifier LLM call")

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.engine.HermesEngineAdapter.run",
        fake_hermes_run,
    )
    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.runtime_service.judge_faithfulness",
        fake_faithfulness,
    )
    ctx = _AgentTurnContext(
        config=config,
        gathered=_GatheredContext(
            grounding=["[PRODUCT] SATStation Digital SAT Prep Platform"],
            history=[],
            agent_kind="seller",
            voice_examples=[],
            authority_warnings=[],
            authority_bundle=CatalogAuthorityBundle(
                query="what is price",
            ),
            runtime_profile=profile,
        ),
        agent_id=config.agent_id,
        customer_message="what is price",
        customer_query_text="what is price",
        conversation_id=10,
    )

    outcome = await AgentRuntimeService(session=None).run_from_context(ctx)  # type: ignore[arg-type]

    assert outcome.reply_text == "Starter Coins package is 40 000 UZS."
    assert outcome.talk_bundle is bundle
    assert outcome.telemetry is not None
    assert outcome.telemetry["faithfulness"]["mode"] == "deferred_critic"
    assert outcome.telemetry["finalization"]["blocked"] is False


def test_safe_ack_used_for_empty_failed_outcome():
    from app.modules.agent_runtime_v2.dispatcher import _should_send_safe_ack
    from app.modules.agent_runtime_v2.reply_runtime import SendAction
    from app.modules.agent_runtime_v2.runtime_service import AgentRuntimeOutcome

    failed = AgentRuntimeOutcome(
        action=SendAction.PROPOSE,
        reply_text="",
        confidence=0.0,
        agent_id=1,
        reason="pending_send_policy",
        tool_errors=1,
    )
    assert _should_send_safe_ack(failed) is True

    budget = AgentRuntimeOutcome(
        action=SendAction.PROPOSE,
        reply_text="",
        confidence=0.0,
        agent_id=1,
        reason="budget_exceeded: workspace daily token cap reached",
    )
    assert _should_send_safe_ack(budget) is False
