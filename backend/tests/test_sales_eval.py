from datetime import datetime, timezone

import pytest

from app.modules.evals.sales_eval import run_sales_eval_suite


def test_sales_eval_suite_passes_core_intelligence_action_cases() -> None:
    report = run_sales_eval_suite(
        now=datetime(2026, 4, 29, 1, 30, tzinfo=timezone.utc),
    )

    assert report.total_cases == 8
    assert report.passed_cases == 8
    assert report.pass_rate == pytest.approx(1.0)
    assert {result.case_id for result in report.results} >= {
        "seller_attention_wins",
        "ai_off_blocks_reply_readiness",
        "media_hydration_blocks_reply",
        "follow_up_waiting_on_customer",
        "follow_up_not_due_yet",
    }


def test_sales_eval_suite_rejects_unknown_suite() -> None:
    with pytest.raises(ValueError, match="Unknown or empty sales eval suite"):
        run_sales_eval_suite(suite="missing")
