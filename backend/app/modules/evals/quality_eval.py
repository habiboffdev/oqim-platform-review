from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ReleaseClass = Literal["red", "yellow", "blue", "green"]
ScenarioStatus = Literal["pass", "partial", "fail"]


@dataclass(frozen=True, slots=True)
class QualityEvalScenario:
    scenario_id: str
    suite_tags: tuple[str, ...]
    description: str
    current_status: ScenarioStatus
    score: float
    evidence: tuple[str, ...]
    missing: tuple[str, ...]
    critical_faults: tuple[str, ...] = ()


class QualityEvalScenarioResult(BaseModel):
    scenario_id: str
    description: str
    status: ScenarioStatus
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    evidence: list[str]
    missing: list[str]
    critical_faults: list[str]


class QualityEvalSuiteReport(BaseModel):
    suite: str
    generated_at: str
    release_class: ReleaseClass
    total_cases: int
    passed_cases: int
    partial_cases: int
    failed_cases: int
    pass_rate: float = Field(ge=0.0, le=1.0)
    weighted_score: float = Field(ge=0.0, le=1.0)
    hard_failure_count: int
    critical_fault_count: int
    results: list[QualityEvalScenarioResult]
    decision: str


QUALITY_EVAL_SCENARIOS: tuple[QualityEvalScenario, ...] = (
    QualityEvalScenario(
        scenario_id="ONB-001",
        suite_tags=("golden-demo", "onboarding"),
        description="Smooth company brain setup from Telegram history and owner rules.",
        current_status="fail",
        score=0.20,
        evidence=(
            "onboarding runtime projection exists",
            "Business Brain and OQIM Intelligence contracts are documented",
        ),
        missing=(
            "phone/QR entry plus business basics eval fixture",
            "preferences, sources, rules, and automation-boundary proof",
            "source-linked learned catalog/KB/rules/voice/pairs proof with seller review",
            "browser proof that seller can correct learned facts and enter workspace in under 30-60 seconds",
        ),
    ),
    QualityEvalScenario(
        scenario_id="ONB-002",
        suite_tags=("golden-demo", "onboarding"),
        description="Degraded onboarding recovery is stage-specific and honest.",
        current_status="fail",
        score=0.25,
        evidence=(
            "onboarding runtime has stage-state direction",
            "degraded provider behavior is documented as required",
        ),
        missing=(
            "provider-failure eval fixture",
            "refresh/retry proof for failed stages",
            "seller-facing Uzbek degraded copy proof with no technical terms",
            "workspace-isolated LLM/embedding rate-limit proof",
        ),
    ),
    QualityEvalScenario(
        scenario_id="SELL-001",
        suite_tags=("golden-demo", "seller-agent", "grounding"),
        description="Customer sends product photo and asks price.",
        current_status="partial",
        score=0.45,
        evidence=(
            "existing seller eval has photo price no-grounding guard",
            "Business Brain multimodal retrieval direction is documented",
        ),
        missing=(
            "multimodal catalog candidate fixture",
            "exact/stale/conflicting price source-ref proof",
            "Seller Workbench evidence rendering proof",
        ),
    ),
    QualityEvalScenario(
        scenario_id="SELL-002",
        suite_tags=("golden-demo", "seller-agent", "grounding", "autopilot"),
        description="Customer asks from KB and owner rules with integration risk.",
        current_status="partial",
        score=0.45,
        evidence=(
            "existing seller eval has warranty KB grounding case",
            "Action Runtime policy/autopilot baseline exists",
        ),
        missing=(
            "owner-rule text/voice eval fixture",
            "integration capability block proof",
            "proposal instead of direct side-effect proof in UI",
        ),
    ),
    QualityEvalScenario(
        scenario_id="SELL-003",
        suite_tags=("golden-demo", "seller-agent"),
        description="Chaotic multi-message tail blocks stale replies and creates one current decision.",
        current_status="fail",
        score=0.30,
        evidence=(
            "Seller Agent stale-tail direction is documented",
            "conversation tail harness exists in backend tests",
        ),
        missing=(
            "scenario eval fixture with three rapid customer messages",
            "stale reply cannot-send proof",
            "SellerAgentActionSurface current decision proof",
        ),
    ),
    QualityEvalScenario(
        scenario_id="SELL-004",
        suite_tags=("golden-demo", "seller-agent", "grounding", "autopilot"),
        description="Customer asks for product images and OQIM proposes correct catalog media.",
        current_status="fail",
        score=0.30,
        evidence=(
            "catalog media action direction is documented",
            "commercial media signal tests exist",
        ),
        missing=(
            "catalog media retrieval eval fixture",
            "wrong-product-image hard-fail proof",
            "Action Runtime media-send proposal proof",
        ),
    ),
    QualityEvalScenario(
        scenario_id="INTEL-001",
        suite_tags=("golden-demo", "intelligence"),
        description="Ambiguous customer state change requires seller review.",
        current_status="partial",
        score=0.45,
        evidence=(
            "sales eval covers deterministic next-action state",
            "OQIM Intelligence review proposal direction is documented",
        ),
        missing=(
            "paid/reserved/canceled/address ambiguity fixture",
            "review proposal instead of confirmed truth proof",
            "analytics wait-for-confirmation proof",
        ),
    ),
    QualityEvalScenario(
        scenario_id="INTEL-002",
        suite_tags=("golden-demo", "intelligence"),
        description="Cross-channel customer ambiguity creates merge proposal, not silent merge.",
        current_status="fail",
        score=0.25,
        evidence=(
            "customer identity tests exist",
            "cross-channel ambiguity rule is documented",
        ),
        missing=(
            "cross-channel eval fixture",
            "merge proposal proof",
            "no silent merge proof in OQIM Intelligence surface",
        ),
    ),
    QualityEvalScenario(
        scenario_id="AUTO-001",
        suite_tags=("golden-demo", "autopilot"),
        description="Seller-controlled autopilot respects confidence, allowlist, quiet hours, and escalation.",
        current_status="partial",
        score=0.50,
        evidence=(
            "Action Runtime phase 7 tests exist",
            "autopilot policy baseline exists",
        ),
        missing=(
            "end-to-end seller policy fixture",
            "quiet-hours/capability block proof",
            "in-app escalation proof",
        ),
    ),
    QualityEvalScenario(
        scenario_id="BI-001",
        suite_tags=("golden-demo", "bi"),
        description="Morning business state shows replies, summary, stages, and what customers ask.",
        current_status="fail",
        score=0.35,
        evidence=(
            "BI/Promoter phase 8 backend baseline exists",
            "first BI/Promoter route exists",
        ),
        missing=(
            "projection-backed morning dashboard eval fixture",
            "source freshness proof",
            "no frontend-computed hidden truth proof",
        ),
    ),
    QualityEvalScenario(
        scenario_id="LEARN-001",
        suite_tags=("golden-demo", "learning-loop"),
        description="Seller correction improves a similar future reply.",
        current_status="fail",
        score=0.30,
        evidence=(
            "Learning Lab direction and correction-pair substrate are documented",
            "learning loop tests exist",
        ),
        missing=(
            "correction-to-future-reply eval fixture",
            "auditable pair/update source refs proof",
            "UI proof that learning happened without overclaiming",
        ),
    ),
)

SUITE_ALIASES: dict[str, tuple[str, ...]] = {
    "golden-demo": ("golden-demo",),
    "onboarding": ("onboarding",),
    "seller-agent": ("seller-agent",),
    "grounding": ("grounding",),
    "intelligence": ("intelligence",),
    "autopilot": ("autopilot",),
    "bi": ("bi",),
    "learning-loop": ("learning-loop",),
}


def run_quality_eval_suite(*, suite: str = "golden-demo") -> QualityEvalSuiteReport:
    suite_name = (suite or "golden-demo").strip().lower()
    tags = SUITE_ALIASES.get(suite_name)
    if tags is None:
        known = ", ".join(sorted(SUITE_ALIASES))
        raise ValueError(f"Unknown quality eval suite: {suite}. Known suites: {known}")

    selected = [
        scenario
        for scenario in QUALITY_EVAL_SCENARIOS
        if any(tag in scenario.suite_tags for tag in tags)
    ]
    results = [_result_for_scenario(scenario) for scenario in selected]
    total = len(results)
    passed = sum(1 for result in results if result.status == "pass")
    partial = sum(1 for result in results if result.status == "partial")
    failed = sum(1 for result in results if result.status == "fail")
    critical_fault_count = sum(len(result.critical_faults) for result in results)
    weighted_score = (
        sum(result.score for result in results) / total
        if total
        else 0.0
    )
    hard_failure_count = failed + critical_fault_count
    release_class = _release_class(
        weighted_score=weighted_score,
        failed_cases=failed,
        critical_fault_count=critical_fault_count,
    )

    return QualityEvalSuiteReport(
        suite=suite_name,
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        release_class=release_class,
        total_cases=total,
        passed_cases=passed,
        partial_cases=partial,
        failed_cases=failed,
        pass_rate=(passed / total) if total else 0.0,
        weighted_score=round(weighted_score, 4),
        hard_failure_count=hard_failure_count,
        critical_fault_count=critical_fault_count,
        results=results,
        decision=_decision_for_release_class(release_class),
    )


def _result_for_scenario(scenario: QualityEvalScenario) -> QualityEvalScenarioResult:
    return QualityEvalScenarioResult(
        scenario_id=scenario.scenario_id,
        description=scenario.description,
        status=scenario.current_status,
        passed=scenario.current_status == "pass",
        score=scenario.score,
        evidence=list(scenario.evidence),
        missing=list(scenario.missing),
        critical_faults=list(scenario.critical_faults),
    )


def _release_class(
    *,
    weighted_score: float,
    failed_cases: int,
    critical_fault_count: int,
) -> ReleaseClass:
    if critical_fault_count > 0 or failed_cases > 0:
        return "red"
    if weighted_score >= 0.92:
        return "green"
    if weighted_score >= 0.85:
        return "blue"
    return "yellow"


def _decision_for_release_class(release_class: ReleaseClass) -> str:
    if release_class == "green":
        return "pilot-ready"
    if release_class == "blue":
        return "limited beta only"
    if release_class == "yellow":
        return "internal controlled testing only"
    return "hold release; missing scenario proof"
