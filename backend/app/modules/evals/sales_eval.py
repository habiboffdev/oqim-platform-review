from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.services.conversation_state import (
    ConversationFollowUpState,
    ConversationReplyState,
    CustomerConversationState,
    project_next_best_action,
)


@dataclass(frozen=True, slots=True)
class SalesEvalCase:
    case_id: str
    description: str
    state: CustomerConversationState
    expected_stage: str
    expected_action: str
    expected_ready: bool
    expected_reason_prefix: str
    needs_attention: bool = False
    override_mode: str = "auto"


class SalesEvalCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class SalesEvalCaseResult(BaseModel):
    case_id: str
    description: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    checks: list[SalesEvalCheck]


class SalesEvalSuiteReport(BaseModel):
    suite: str
    generated_at: str
    total_cases: int
    passed_cases: int
    pass_rate: float = Field(ge=0.0, le=1.0)
    results: list[SalesEvalCaseResult]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def default_sales_eval_suite(now: datetime | None = None) -> tuple[SalesEvalCase, ...]:
    current_time = now or _now()
    return (
        SalesEvalCase(
            case_id="price_question_needs_reply",
            description="A buyer asking price should remain qualified and ready for a seller reply.",
            state=CustomerConversationState(
                pipeline_stage="qualified",
                last_intent="price_inquiry",
                products_interested=["kechki kurs"],
                reply=ConversationReplyState(
                    unresolved_customer_message_ids=[101],
                    latest_unresolved_customer_message_id=101,
                ),
            ),
            expected_stage="qualified",
            expected_action="reply_to_customer",
            expected_ready=True,
            expected_reason_prefix="unresolved_customer_tail",
        ),
        SalesEvalCase(
            case_id="negotiation_follow_up_wins",
            description="A due follow-up in negotiation should take priority over settled state.",
            state=CustomerConversationState(
                pipeline_stage="negotiation",
                last_intent="objection",
                follow_up=ConversationFollowUpState(
                    status="due",
                    kind="negotiation_stall",
                    due_at=(current_time - timedelta(minutes=5)).isoformat(),
                    waiting_for="seller",
                ),
            ),
            expected_stage="negotiation",
            expected_action="follow_up_due",
            expected_ready=True,
            expected_reason_prefix="follow_up_due:negotiation_stall",
        ),
        SalesEvalCase(
            case_id="won_deal_is_settled",
            description="A won deal with no unresolved buyer tail or follow-up should be settled.",
            state=CustomerConversationState(
                pipeline_stage="won",
                last_intent="order_ready",
                products_interested=["2 xonali ijara"],
            ),
            expected_stage="won",
            expected_action="conversation_settled",
            expected_ready=True,
            expected_reason_prefix="no_unresolved_tail_no_follow_up",
        ),
        SalesEvalCase(
            case_id="seller_attention_wins",
            description="A seller-flagged conversation should surface attention before automatic actions.",
            state=CustomerConversationState(
                pipeline_stage="qualified",
                last_intent="price_inquiry",
                reply=ConversationReplyState(
                    unresolved_customer_message_ids=[201],
                    latest_unresolved_customer_message_id=201,
                ),
            ),
            expected_stage="qualified",
            expected_action="attention_flagged",
            expected_ready=True,
            expected_reason_prefix="seller_flagged_attention",
            needs_attention=True,
        ),
        SalesEvalCase(
            case_id="ai_off_blocks_reply_readiness",
            description="An unresolved buyer tail should remain visible but not ready when AI replies are disabled.",
            state=CustomerConversationState(
                pipeline_stage="qualified",
                last_intent="availability",
                reply=ConversationReplyState(
                    unresolved_customer_message_ids=[301],
                    latest_unresolved_customer_message_id=301,
                ),
            ),
            expected_stage="qualified",
            expected_action="reply_to_customer",
            expected_ready=False,
            expected_reason_prefix="agent_actions_disabled",
            override_mode="off",
        ),
        SalesEvalCase(
            case_id="media_hydration_blocks_reply",
            description="A media-heavy buyer tail should wait for media semantics instead of replying blindly.",
            state=CustomerConversationState(
                pipeline_stage="qualified",
                last_intent="media_inquiry",
                reply=ConversationReplyState(
                    unresolved_customer_message_ids=[401],
                    latest_unresolved_customer_message_id=401,
                ),
                media_readiness_status="pending",
            ),
            expected_stage="qualified",
            expected_action="reply_to_customer",
            expected_ready=False,
            expected_reason_prefix="waiting_on_media_hydration",
        ),
        SalesEvalCase(
            case_id="follow_up_waiting_on_customer",
            description="A future follow-up waiting on customer should not wake the seller.",
            state=CustomerConversationState(
                pipeline_stage="negotiation",
                last_intent="objection",
                follow_up=ConversationFollowUpState(
                    status="scheduled",
                    kind="customer_deciding",
                    due_at=(current_time + timedelta(hours=2)).isoformat(),
                    waiting_for="customer",
                ),
            ),
            expected_stage="negotiation",
            expected_action="wait_on_customer_reply",
            expected_ready=False,
            expected_reason_prefix="follow_up_waiting_customer:customer_deciding",
        ),
        SalesEvalCase(
            case_id="follow_up_not_due_yet",
            description="A seller follow-up scheduled in the future should stay visible but not ready.",
            state=CustomerConversationState(
                pipeline_stage="proposal",
                last_intent="considering",
                follow_up=ConversationFollowUpState(
                    status="scheduled",
                    kind="proposal_check",
                    due_at=(current_time + timedelta(hours=4)).isoformat(),
                    waiting_for="seller",
                ),
            ),
            expected_stage="proposal",
            expected_action="wait_on_follow_up",
            expected_ready=False,
            expected_reason_prefix="follow_up_not_due_yet:proposal_check",
        ),
    )


def run_sales_eval_suite(
    *,
    suite: str = "core",
    now: datetime | None = None,
) -> SalesEvalSuiteReport:
    suite_name = (suite or "core").strip().lower()
    if suite_name not in {"core", "default", "sales"}:
        raise ValueError(f"Unknown or empty sales eval suite: {suite}")

    current_time = now or _now()
    results = [
        _run_sales_eval_case(case, now=current_time)
        for case in default_sales_eval_suite(now=current_time)
    ]
    passed_cases = sum(1 for result in results if result.passed)
    return SalesEvalSuiteReport(
        suite=suite_name,
        generated_at=current_time.isoformat(),
        total_cases=len(results),
        passed_cases=passed_cases,
        pass_rate=(passed_cases / len(results)) if results else 0.0,
        results=results,
    )


def _run_sales_eval_case(
    case: SalesEvalCase,
    *,
    now: datetime,
) -> SalesEvalCaseResult:
    action = project_next_best_action(
        case.state,
        needs_attention=case.needs_attention,
        override_mode=case.override_mode,
        now=now,
    )
    checks = [
        SalesEvalCheck(
            name="pipeline_stage",
            passed=case.state.pipeline_stage == case.expected_stage,
            detail=f"Expected stage {case.expected_stage!r}, got {case.state.pipeline_stage!r}.",
        ),
        SalesEvalCheck(
            name="next_action",
            passed=action.action == case.expected_action,
            detail=f"Expected action {case.expected_action!r}, got {action.action!r}.",
        ),
        SalesEvalCheck(
            name="action_readiness",
            passed=action.ready is case.expected_ready,
            detail=f"Expected ready={case.expected_ready}, got ready={action.ready}.",
        ),
        SalesEvalCheck(
            name="action_reason",
            passed=action.reason.startswith(case.expected_reason_prefix),
            detail=(
                f"Expected reason prefix {case.expected_reason_prefix!r}, "
                f"got {action.reason!r}."
            ),
        ),
    ]
    passed = all(check.passed for check in checks)
    score = sum(1 for check in checks if check.passed) / len(checks)
    return SalesEvalCaseResult(
        case_id=case.case_id,
        description=case.description,
        passed=passed,
        score=score,
        checks=checks,
    )
