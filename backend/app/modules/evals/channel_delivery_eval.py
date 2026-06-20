from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_talking.contracts import (
    TalkAction,
    TalkActionKind,
    TalkBundle,
    TalkingPolicy,
)
from app.modules.agent_talking.service import TalkBundleService
from app.modules.channel_runtime.source import ChannelRuntimeCore
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.services.delivery import DeliveryResult


class ChannelDeliveryEvalCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class ChannelDeliveryEvalResult(BaseModel):
    case_id: str
    description: str
    passed: bool
    intent_count: int = Field(ge=0)
    sent_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    replayed_count: int = Field(ge=0)
    duplicate_delivery_count: int = Field(ge=0)
    delivery_call_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    checks: list[ChannelDeliveryEvalCheck] = Field(default_factory=list)


class ChannelDeliveryEvalSuiteReport(BaseModel):
    suite: str = "channel-delivery"
    workspace_id: int = Field(gt=0)
    agent_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    total_runs: int = Field(ge=0)
    passed_runs: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    intent_count: int = Field(ge=0)
    sent_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    replayed_count: int = Field(ge=0)
    duplicate_delivery_count: int = Field(ge=0)
    delivery_call_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    p95_case_duration_ms: int = Field(ge=0)
    results: list[ChannelDeliveryEvalResult] = Field(default_factory=list)


@dataclass
class _SequencedDelivery:
    results: list[DeliveryResult]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def deliver_message(
        self,
        conversation_id: int,
        text: str,
        **kwargs: Any,
    ) -> DeliveryResult:
        self.calls.append({"conversation_id": conversation_id, "text": text, **kwargs})
        if not self.results:
            return DeliveryResult(
                success=False,
                error="unexpected_duplicate_delivery",
                state="failed",
            )
        return self.results.pop(0)


async def run_channel_delivery_eval_suite(
    *,
    session: AsyncSession,
    workspace_id: int,
    agent_id: int,
    conversation_id: int,
) -> ChannelDeliveryEvalSuiteReport:
    started = time.monotonic()
    await ToolGrantService(session).grant(
        workspace_id=workspace_id,
        payload=ToolGrantInput(agent_id=agent_id, scope="telegram.send_message"),
    )
    bundle = _eval_bundle(
        workspace_id=workspace_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        run_key=uuid.uuid4().hex[:12],
    )
    delivery = _SequencedDelivery(
        results=[
            DeliveryResult(success=True, external_message_id="eval-ext:1", state="confirmed"),
            DeliveryResult(success=False, error="sidecar timeout", state="unknown"),
        ]
    )
    service = TalkBundleService(session, delivery=delivery, sleep=lambda _: None)

    first = await service.execute_bundle(
        bundle=bundle,
        correlation_id="channel-delivery-eval:first",
    )
    first_call_count = len(delivery.calls)
    first_result = _partial_delivery_case(
        bundle=bundle,
        execution=first,
        delivery=delivery,
        first_call_count=first_call_count,
    )

    second = await service.execute_bundle(
        bundle=bundle,
        correlation_id="channel-delivery-eval:replay",
    )
    replay_call_count = len(delivery.calls) - first_call_count
    replay_result = _replay_case(
        execution=second,
        replay_call_count=replay_call_count,
        total_delivery_call_count=len(delivery.calls),
    )

    restart_delivery = _SequencedDelivery(results=[])
    restarted_service = TalkBundleService(
        session,
        delivery=restart_delivery,
        sleep=lambda _: None,
    )
    restarted = await restarted_service.execute_bundle(
        bundle=bundle,
        correlation_id="channel-delivery-eval:restart",
    )
    restart_result = _restart_replay_case(
        execution=restarted,
        restart_call_count=len(restart_delivery.calls),
    )

    burst_bundle = _burst_bundle(
        workspace_id=workspace_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        run_key=uuid.uuid4().hex[:12],
    )
    burst_delivery = _SequencedDelivery(
        results=[
            DeliveryResult(
                success=True,
                external_message_id=f"eval-burst:{index}",
                state="confirmed",
            )
            for index in range(5)
        ]
    )
    burst_execution = await TalkBundleService(
        session,
        delivery=burst_delivery,
        sleep=lambda _: None,
    ).execute_bundle(
        bundle=burst_bundle,
        correlation_id="channel-delivery-eval:burst",
    )
    burst_result = _burst_case(
        bundle=burst_bundle,
        execution=burst_execution,
        delivery=burst_delivery,
    )

    results = [first_result, replay_result, restart_result, burst_result]
    passed = sum(1 for result in results if result.passed)
    durations = [result.duration_ms for result in results]
    return ChannelDeliveryEvalSuiteReport(
        workspace_id=workspace_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        total_runs=len(results),
        passed_runs=passed,
        pass_rate=(passed / len(results)) if results else 0.0,
        intent_count=max(result.intent_count for result in results) if results else 0,
        sent_count=sum(result.sent_count for result in results),
        unknown_count=sum(result.unknown_count for result in results),
        replayed_count=sum(result.replayed_count for result in results),
        duplicate_delivery_count=sum(result.duplicate_delivery_count for result in results),
        delivery_call_count=sum(result.delivery_call_count for result in results),
        duration_ms=int((time.monotonic() - started) * 1000),
        p95_case_duration_ms=_percentile_ms(durations, 0.95),
        results=results,
    )


def _eval_bundle(
    *,
    workspace_id: int,
    agent_id: int,
    conversation_id: int,
    run_key: str,
) -> TalkBundle:
    return TalkBundle(
        workspace_id=workspace_id,
        agent_id=agent_id,
        hermes_run_id=f"hermes_run:channel-delivery-eval:{run_key}",
        conversation_id=conversation_id,
        channel_account_id="telegram:eval",
        actions=[
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="Salom, birinchi xabar.",
                requires_scope="telegram.send_message",
            ),
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text="Ikkinchi xabar tasdiq kutmoqda.",
                requires_scope="telegram.send_message",
            ),
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default().model_copy(
            update={"typing_indicator": "off", "pacing_profile": "none"}
        ),
        confidence=0.91,
    )


def _burst_bundle(
    *,
    workspace_id: int,
    agent_id: int,
    conversation_id: int,
    run_key: str,
) -> TalkBundle:
    return TalkBundle(
        workspace_id=workspace_id,
        agent_id=agent_id,
        hermes_run_id=f"hermes_run:channel-delivery-burst-eval:{run_key}",
        conversation_id=conversation_id,
        channel_account_id="telegram:eval",
        actions=[
            TalkAction(
                kind=TalkActionKind.SEND_MSG,
                text=f"Burst xabar {index + 1}",
                requires_scope="telegram.send_message",
            )
            for index in range(5)
        ],
        talking_policy_snapshot=TalkingPolicy.seller_default().model_copy(
            update={
                "max_bubbles_per_turn": 8,
                "typing_indicator": "off",
                "pacing_profile": "none",
            }
        ),
        confidence=0.91,
    )


def _partial_delivery_case(
    *,
    bundle: TalkBundle,
    execution: Any,
    delivery: _SequencedDelivery,
    first_call_count: int,
) -> ChannelDeliveryEvalResult:
    started = time.monotonic()
    plan = ChannelRuntimeCore().plan_talk_bundle_delivery(bundle)
    checks = [
        ChannelDeliveryEvalCheck(
            name="delivery_intents_planned",
            passed=len(plan.intents) == 2
            and [intent.client_idempotency_key for intent in plan.intents]
            == [bubble.idempotency_key for bubble in execution.bubbles],
            detail=f"intents={[intent.model_dump(mode='json') for intent in plan.intents]}",
        ),
        ChannelDeliveryEvalCheck(
            name="partial_state_recorded",
            passed=execution.status == "partial"
            and execution.delivery_state == "partially_sent"
            and execution.sent_count == 1
            and execution.unknown_count == 1,
            detail=execution.model_dump_json(),
        ),
        ChannelDeliveryEvalCheck(
            name="delivery_called_once_per_bubble",
            passed=first_call_count == 2,
            detail=f"delivery_calls={first_call_count}",
        ),
        ChannelDeliveryEvalCheck(
            name="unknown_bubble_retains_idempotency",
            passed=execution.bubbles[1].status == "unknown"
            and delivery.calls[1]["client_idempotency_key"] == execution.bubbles[1].idempotency_key,
            detail=f"bubble={execution.bubbles[1].model_dump(mode='json')}",
        ),
    ]
    return ChannelDeliveryEvalResult(
        case_id="multi_bubble_partial_delivery",
        description="Multi-bubble Channel Runtime delivery records confirmed and unknown bubbles.",
        passed=all(check.passed for check in checks),
        intent_count=len(plan.intents),
        sent_count=execution.sent_count,
        unknown_count=execution.unknown_count,
        replayed_count=0,
        duplicate_delivery_count=0,
        delivery_call_count=first_call_count,
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


def _restart_replay_case(
    *,
    execution: Any,
    restart_call_count: int,
) -> ChannelDeliveryEvalResult:
    started = time.monotonic()
    replayed_count = sum(1 for bubble in execution.bubbles if bubble.status == "replayed")
    unknown_count = sum(1 for bubble in execution.bubbles if bubble.status == "unknown")
    checks = [
        ChannelDeliveryEvalCheck(
            name="restart_replays_confirmed_and_preserves_unknown",
            passed=replayed_count == 1
            and unknown_count == 1
            and execution.sent_count == 1
            and execution.unknown_count == 1,
            detail=execution.model_dump_json(),
        ),
        ChannelDeliveryEvalCheck(
            name="restart_does_not_call_delivery",
            passed=restart_call_count == 0,
            detail=f"restart_calls={restart_call_count}",
        ),
    ]
    return ChannelDeliveryEvalResult(
        case_id="restart_replays_unconfirmed_bubbles_without_delivery_call",
        description=(
            "A restarted Channel Runtime service replays stored bubble records "
            "instead of duplicating unknown Telegram sends."
        ),
        passed=all(check.passed for check in checks),
        intent_count=len(execution.bubbles),
        sent_count=execution.sent_count,
        unknown_count=execution.unknown_count,
        replayed_count=replayed_count,
        duplicate_delivery_count=restart_call_count,
        delivery_call_count=restart_call_count,
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


def _burst_case(
    *,
    bundle: TalkBundle,
    execution: Any,
    delivery: _SequencedDelivery,
) -> ChannelDeliveryEvalResult:
    started = time.monotonic()
    plan = ChannelRuntimeCore().plan_talk_bundle_delivery(bundle)
    planned_keys = [intent.client_idempotency_key for intent in plan.intents]
    called_keys = [str(call["client_idempotency_key"]) for call in delivery.calls]
    checks = [
        ChannelDeliveryEvalCheck(
            name="burst_intents_keep_action_order",
            passed=[intent.action_index for intent in plan.intents] == list(range(5)),
            detail=f"intents={[intent.model_dump(mode='json') for intent in plan.intents]}",
        ),
        ChannelDeliveryEvalCheck(
            name="burst_delivery_uses_planned_idempotency",
            passed=planned_keys == called_keys,
            detail=f"planned={planned_keys} called={called_keys}",
        ),
        ChannelDeliveryEvalCheck(
            name="burst_all_bubbles_confirmed",
            passed=execution.status == "executed"
            and execution.delivery_state == "confirmed"
            and execution.sent_count == 5
            and execution.unknown_count == 0,
            detail=execution.model_dump_json(),
        ),
    ]
    return ChannelDeliveryEvalResult(
        case_id="burst_delivery_preserves_ordered_idempotency",
        description=(
            "A larger approved talk bundle preserves ordered delivery intents "
            "and per-bubble idempotency through execution."
        ),
        passed=all(check.passed for check in checks),
        intent_count=len(plan.intents),
        sent_count=execution.sent_count,
        unknown_count=execution.unknown_count,
        replayed_count=0,
        duplicate_delivery_count=0,
        delivery_call_count=len(delivery.calls),
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


def _replay_case(
    *,
    execution: Any,
    replay_call_count: int,
    total_delivery_call_count: int,
) -> ChannelDeliveryEvalResult:
    started = time.monotonic()
    replayed_count = sum(1 for bubble in execution.bubbles if bubble.status == "replayed")
    unknown_count = sum(1 for bubble in execution.bubbles if bubble.status == "unknown")
    checks = [
        ChannelDeliveryEvalCheck(
            name="confirmed_bubble_replayed_unknown_preserved",
            passed=replayed_count == 1
            and unknown_count == 1
            and execution.sent_count == 1
            and execution.unknown_count == 1,
            detail=execution.model_dump_json(),
        ),
        ChannelDeliveryEvalCheck(
            name="replay_does_not_call_delivery",
            passed=replay_call_count == 0,
            detail=f"replay_calls={replay_call_count} total_calls={total_delivery_call_count}",
        ),
    ]
    return ChannelDeliveryEvalResult(
        case_id="idempotent_replay_after_partial_delivery",
        description="Re-executing the same delivery bundle replays stored bubbles without duplicate sends.",
        passed=all(check.passed for check in checks),
        intent_count=len(execution.bubbles),
        sent_count=execution.sent_count,
        unknown_count=execution.unknown_count,
        replayed_count=replayed_count,
        duplicate_delivery_count=replay_call_count,
        delivery_call_count=replay_call_count,
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


def _percentile_ms(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * percentile)),
    )
    return ordered[index]
