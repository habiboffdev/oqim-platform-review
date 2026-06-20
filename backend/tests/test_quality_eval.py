from __future__ import annotations

import pytest

from app.modules.evals.quality_eval import QUALITY_EVAL_SCENARIOS, run_quality_eval_suite


def test_quality_eval_catalog_covers_active_scenario_doc_ids() -> None:
    scenario_ids = {scenario.scenario_id for scenario in QUALITY_EVAL_SCENARIOS}

    assert {
        "ONB-001",
        "ONB-002",
        "SELL-001",
        "SELL-002",
        "SELL-003",
        "SELL-004",
        "INTEL-001",
        "INTEL-002",
        "AUTO-001",
        "BI-001",
        "LEARN-001",
    } <= scenario_ids


def test_golden_demo_quality_eval_is_executable_and_honestly_red() -> None:
    report = run_quality_eval_suite(suite="golden-demo")

    assert report.suite == "golden-demo"
    assert report.total_cases == 11
    assert report.release_class == "red"
    assert report.hard_failure_count > 0
    assert report.decision == "hold release; missing scenario proof"
    assert {result.scenario_id for result in report.results} >= {"ONB-001", "SELL-001", "BI-001"}
    assert any(result.status == "partial" for result in report.results)
    assert all(result.missing for result in report.results)


@pytest.mark.parametrize(
    ("suite", "expected_ids"),
    [
        ("onboarding", {"ONB-001", "ONB-002"}),
        ("seller-agent", {"SELL-001", "SELL-002", "SELL-003", "SELL-004"}),
        ("grounding", {"SELL-001", "SELL-002", "SELL-004"}),
        ("intelligence", {"INTEL-001", "INTEL-002"}),
        ("autopilot", {"SELL-002", "SELL-004", "AUTO-001"}),
        ("bi", {"BI-001"}),
        ("learning-loop", {"LEARN-001"}),
    ],
)
def test_quality_eval_named_suites_select_expected_scenarios(
    suite: str,
    expected_ids: set[str],
) -> None:
    report = run_quality_eval_suite(suite=suite)

    assert {result.scenario_id for result in report.results} == expected_ids


def test_quality_eval_rejects_unknown_suite() -> None:
    with pytest.raises(ValueError, match="Unknown quality eval suite"):
        run_quality_eval_suite(suite="not-real")
