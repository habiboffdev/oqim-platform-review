from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.commercial_spine import LLMGatewayTraceRecord
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.extraction_runtime.contracts import (
    ExtractionPart,
    ExtractionRequest,
    ExtractionScope,
)
from app.modules.extraction_runtime.llm_provider import LLMGatewayCandidateProvider
from app.modules.extraction_runtime.profiles import default_profile_registry
from app.modules.extraction_runtime.runtime import UniversalExtractionRuntime


class BuyerIntentEvalCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class BuyerIntentEvalResult(BaseModel):
    case_id: str
    description: str
    workspace_id: int = Field(gt=0)
    repetition: int = Field(ge=0)
    live: bool
    passed: bool
    extraction_status: str
    detected_intent: str | None = None
    response_strategy: str | None = None
    answer_shape: str | None = None
    accepted_candidate_count: int = Field(ge=0)
    rejected_candidate_count: int = Field(ge=0)
    llm_trace_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    checks: list[BuyerIntentEvalCheck] = Field(default_factory=list)


class BuyerIntentEvalSuiteReport(BaseModel):
    suite: str = "buyer-intent"
    live: bool
    concurrency: int = Field(ge=1)
    total_runs: int = Field(ge=0)
    passed_runs: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    hard_failure_count: int = Field(ge=0)
    provider_error_count: int = Field(ge=0)
    rejected_candidate_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    median_case_duration_ms: int = Field(ge=0)
    p95_case_duration_ms: int = Field(ge=0)
    max_case_duration_ms: int = Field(ge=0)
    results: list[BuyerIntentEvalResult] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _BuyerIntentCase:
    case_id: str
    description: str
    turns: tuple[dict[str, Any], ...]
    expected_intents: tuple[str, ...]
    expected_strategies: tuple[str, ...]
    expected_shapes: tuple[str, ...]


async def run_buyer_intent_eval_suite(
    *,
    repository: CommercialSpineRepository,
    workspace_id: int,
    live: bool = False,
    repetitions: int = 1,
    concurrency: int = 1,
    session_factory: Callable[[], Any] | None = None,
) -> BuyerIntentEvalSuiteReport:
    """Evaluate `buyer_intent.v1` extraction quality in isolation.

    Offline mode proves the schema/evidence/trace harness with fixture-labeled
    outputs. Live mode uses the real LLM Gateway and is the quality gate for
    chaotic buyer-tail intent.
    """

    if workspace_id <= 0:
        raise ValueError("workspace_id must be positive")
    bounded_repetitions = max(1, min(int(repetitions), 20))
    bounded_concurrency = max(1, min(int(concurrency), 8))
    cases = _default_cases()
    started = time.monotonic()
    jobs = [
        (repetition, case)
        for repetition in range(bounded_repetitions)
        for case in cases
    ]
    if bounded_concurrency == 1:
        results = [
            await _run_case(
                repository=repository,
                workspace_id=workspace_id,
                live=live,
                repetition=repetition,
                case=case,
            )
            for repetition, case in jobs
        ]
    else:
        if session_factory is None:
            raise ValueError("session_factory is required when concurrency > 1")
        semaphore = asyncio.Semaphore(bounded_concurrency)

        async def run_job(repetition: int, case: _BuyerIntentCase) -> BuyerIntentEvalResult:
            async with semaphore:
                async with session_factory() as session:
                    result = await _run_case(
                        repository=CommercialSpineRepository(session),
                        workspace_id=workspace_id,
                        live=live,
                        repetition=repetition,
                        case=case,
                    )
                    await session.rollback()
                    return result

        results = await asyncio.gather(
            *(run_job(repetition, case) for repetition, case in jobs)
        )

    passed = sum(1 for result in results if result.passed)
    provider_errors = sum(
        1
        for result in results
        if result.extraction_status != "ok"
        or any(check.name == "provider_ok" and not check.passed for check in result.checks)
    )
    rejected = sum(result.rejected_candidate_count for result in results)
    durations = [result.duration_ms for result in results]
    return BuyerIntentEvalSuiteReport(
        live=live,
        concurrency=bounded_concurrency,
        total_runs=len(results),
        passed_runs=passed,
        pass_rate=(passed / len(results)) if results else 0.0,
        hard_failure_count=len(results) - passed,
        provider_error_count=provider_errors,
        rejected_candidate_count=rejected,
        duration_ms=int((time.monotonic() - started) * 1000),
        median_case_duration_ms=_percentile_ms(durations, 0.5),
        p95_case_duration_ms=_percentile_ms(durations, 0.95),
        max_case_duration_ms=max(durations, default=0),
        results=results,
    )


async def _run_case(
    *,
    repository: CommercialSpineRepository,
    workspace_id: int,
    live: bool,
    repetition: int,
    case: _BuyerIntentCase,
) -> BuyerIntentEvalResult:
    started = time.monotonic()
    run_key = f"buyer-intent-eval:ws{workspace_id}:r{repetition}:{case.case_id}"
    customer, conversation = await _create_eval_conversation(
        repository=repository,
        workspace_id=workspace_id,
        run_key=run_key,
    )
    request = _build_request(
        workspace_id=workspace_id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        run_key=run_key,
        turns=case.turns,
    )
    gateway = LLMGateway(repository=repository)
    if not live:
        gateway = LLMGateway(
            repository=repository,
            provider=_fixture_provider(
                workspace_id=workspace_id,
                run_key=run_key,
                case=case,
                evidence_refs=list(request.allowed_evidence_refs()),
            ),
        )
    extraction = await UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=LLMGatewayCandidateProvider(gateway=gateway),
        provider_timeout_seconds=30,
    ).extract(request)
    buyer_candidates = [
        candidate
        for candidate in extraction.accepted_candidates
        if candidate.profile_ref == "buyer_intent.v1"
        and candidate.kind == "buyer_intent"
        and candidate.owner == "action_runtime"
    ]
    value = dict(buyer_candidates[0].value) if buyer_candidates else {}
    llm_trace_count = await _llm_trace_count(
        repository=repository,
        workspace_id=workspace_id,
        correlation_id=f"{run_key}:buyer_intent.v1",
    )
    checks = _checks(
        case=case,
        extraction_status=extraction.status,
        buyer_candidate_count=len(buyer_candidates),
        rejected_candidate_count=len(extraction.rejected_candidates),
        llm_trace_count=llm_trace_count,
        detected_intent=_optional_string(value.get("detected_intent")),
        response_strategy=_optional_string(value.get("response_strategy")),
        answer_shape=_optional_string(value.get("answer_shape")),
        evidence_ref_count=(
            len(buyer_candidates[0].evidence_refs) if buyer_candidates else 0
        ),
    )
    return BuyerIntentEvalResult(
        case_id=case.case_id,
        description=case.description,
        workspace_id=workspace_id,
        repetition=repetition,
        live=live,
        passed=all(check.passed for check in checks),
        extraction_status=extraction.status,
        detected_intent=_optional_string(value.get("detected_intent")),
        response_strategy=_optional_string(value.get("response_strategy")),
        answer_shape=_optional_string(value.get("answer_shape")),
        accepted_candidate_count=len(buyer_candidates),
        rejected_candidate_count=len(extraction.rejected_candidates),
        llm_trace_count=llm_trace_count,
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


async def _create_eval_conversation(
    *,
    repository: CommercialSpineRepository,
    workspace_id: int,
    run_key: str,
) -> tuple[Customer, Conversation]:
    safe_key = run_key.replace(":", "-")
    customer = Customer(
        workspace_id=workspace_id,
        external_id=safe_key,
        channel="eval",
        display_name=f"Buyer Intent Eval {safe_key}",
        contact_type="customer",
    )
    repository.session.add(customer)
    await repository.session.flush()
    conversation = Conversation(
        workspace_id=workspace_id,
        customer_id=customer.id,
        channel="eval",
        external_chat_id=safe_key,
        pipeline_stage="new",
    )
    repository.session.add(conversation)
    await repository.session.flush()
    return customer, conversation


def _build_request(
    *,
    workspace_id: int,
    conversation_id: int,
    customer_id: int,
    run_key: str,
    turns: tuple[dict[str, Any], ...],
) -> ExtractionRequest:
    parts = [
        ExtractionPart(
            kind="chat_turn",
            ref=f"{run_key}:message:{index}",
            payload={
                "sender_type": turn["sender_type"],
                "content": turn["content"],
                **({"media_semantics": turn["media_semantics"]} if turn.get("media_semantics") else {}),
            },
        )
        for index, turn in enumerate(turns, start=1)
    ]
    return ExtractionRequest(
        scope=ExtractionScope(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
        ),
        source_kind="chat_tail",
        source_ref=f"conversation:{conversation_id}:buyer-intent-eval:{run_key}",
        parts=parts,
        profile_refs=["buyer_intent.v1"],
        target_kinds=["buyer_intent"],
        correlation_id=run_key,
        idempotency_key=run_key,
        max_parallelism=1,
    )


def _fixture_provider(
    *,
    workspace_id: int,
    run_key: str,
    case: _BuyerIntentCase,
    evidence_refs: list[str],
):
    async def provider(_gateway_request):
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "extraction_candidate_provider_output.v1",
                    "candidates": [
                        {
                            "schema_version": "extraction_candidate.v1",
                            "candidate_id": f"{run_key}:buyer-intent",
                            "workspace_id": workspace_id,
                            "owner": "action_runtime",
                            "profile_ref": "buyer_intent.v1",
                            "kind": "buyer_intent",
                            "entity_ref": f"buyer_intent:{run_key}:latest",
                            "operation": "signal",
                            "value": {
                                "detected_intent": case.expected_intents[0],
                                "response_strategy": case.expected_strategies[0],
                                "answer_shape": case.expected_shapes[0],
                                "sales_moment": case.description,
                                "latest_intent_refs": [evidence_refs[-1]],
                            },
                            "confidence": 0.9,
                            "risk_tier": "low",
                            "evidence_refs": [evidence_refs[-1]],
                            "evidence_state": "valid",
                            "requires_review": False,
                            "reason_code": "fixture_buyer_intent_eval",
                        }
                    ],
                }
            ),
            model_used="fixture-buyer-intent-eval",
            token_usage={"input_tokens": 1, "output_tokens": 1},
        )

    return provider


async def _llm_trace_count(
    *,
    repository: CommercialSpineRepository,
    workspace_id: int,
    correlation_id: str,
) -> int:
    rows = await repository.session.execute(
        select(LLMGatewayTraceRecord.id).where(
            LLMGatewayTraceRecord.workspace_id == workspace_id,
            LLMGatewayTraceRecord.correlation_id == correlation_id,
        )
    )
    return len(rows.scalars().all())


def _checks(
    *,
    case: _BuyerIntentCase,
    extraction_status: str,
    buyer_candidate_count: int,
    rejected_candidate_count: int,
    llm_trace_count: int,
    detected_intent: str | None,
    response_strategy: str | None,
    answer_shape: str | None,
    evidence_ref_count: int,
) -> list[BuyerIntentEvalCheck]:
    return [
        BuyerIntentEvalCheck(
            name="provider_ok",
            passed=extraction_status == "ok",
            detail=f"extraction_status={extraction_status}",
        ),
        BuyerIntentEvalCheck(
            name="accepted_buyer_intent",
            passed=buyer_candidate_count == 1,
            detail=f"accepted_buyer_intent={buyer_candidate_count}",
        ),
        BuyerIntentEvalCheck(
            name="no_rejections",
            passed=rejected_candidate_count == 0,
            detail=f"rejected_candidate_count={rejected_candidate_count}",
        ),
        BuyerIntentEvalCheck(
            name="detected_intent",
            passed=detected_intent in case.expected_intents,
            detail=f"expected={case.expected_intents} got={detected_intent}",
        ),
        BuyerIntentEvalCheck(
            name="response_strategy",
            passed=response_strategy in case.expected_strategies,
            detail=f"expected={case.expected_strategies} got={response_strategy}",
        ),
        BuyerIntentEvalCheck(
            name="answer_shape",
            passed=answer_shape in case.expected_shapes,
            detail=f"expected={case.expected_shapes} got={answer_shape}",
        ),
        BuyerIntentEvalCheck(
            name="evidence_refs",
            passed=evidence_ref_count > 0,
            detail=f"evidence_ref_count={evidence_ref_count}",
        ),
        BuyerIntentEvalCheck(
            name="llm_trace",
            passed=llm_trace_count == 1,
            detail=f"llm_trace_count={llm_trace_count}",
        ),
    ]


def _default_cases() -> tuple[_BuyerIntentCase, ...]:
    return (
        _BuyerIntentCase(
            case_id="medicine_media_inquiry",
            description="Medicine buyer sends a package photo and asks if it is available.",
            turns=(
                {
                    "sender_type": "customer",
                    "content": "Mana shuni topib bera olasizmi?",
                    "media_semantics": {
                        "media_type": "photo",
                        "media_description": "medicine package photo",
                    },
                },
            ),
            expected_intents=("media_inquiry",),
            expected_strategies=("clarify_variant", "seller_confirmation"),
            expected_shapes=("one_clarifying_question", "safe_check"),
        ),
        _BuyerIntentCase(
            case_id="course_vague_offer",
            description="Course buyer asks for an evening course without enough detail.",
            turns=(
                {
                    "sender_type": "customer",
                    "content": "kechki kurs bormi aka, ishga mosrog'i kerak edi",
                },
            ),
            expected_intents=("faq", "other"),
            expected_strategies=("clarify_variant",),
            expected_shapes=("one_clarifying_question",),
        ),
        _BuyerIntentCase(
            case_id="real_estate_negotiation",
            description="Real-estate buyer asks for a lower price in a district.",
            turns=(
                {
                    "sender_type": "customer",
                    "content": "Yunusoboddan uy qidiryapman, arzonroq variant bormi?",
                },
            ),
            expected_intents=("negotiation", "faq"),
            expected_strategies=("clarify_variant",),
            expected_shapes=("one_clarifying_question", "safe_check"),
        ),
        _BuyerIntentCase(
            case_id="payment_claim",
            description="Buyer claims payment and asks whether it arrived.",
            turns=(
                {
                    "sender_type": "customer",
                    "content": "pul tashladim, yetib bordimi?",
                },
            ),
            expected_intents=("payment",),
            expected_strategies=("confirm_next_step", "seller_confirmation"),
            expected_shapes=("receipt_request", "safe_check"),
        ),
        _BuyerIntentCase(
            case_id="warranty_faq",
            description="Buyer asks a simple warranty question.",
            turns=(
                {
                    "sender_type": "customer",
                    "content": "kafolat bormi?",
                },
            ),
            expected_intents=("faq", "support"),
            expected_strategies=("answer_directly", "seller_confirmation"),
            expected_shapes=("direct_answer", "safe_check"),
        ),
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _percentile_ms(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int(round((len(ordered) - 1) * percentile))),
    )
    return ordered[index]
