from __future__ import annotations

from unittest.mock import patch

import pytest

from app.modules.agent_runtime_v2.confidence import score_confidence
from app.modules.agent_runtime_v2.faithfulness import (
    FaithfulnessVerdict,
    judge_faithfulness,
)
from app.modules.catalog_authority.contracts import CatalogAuthorityBundle


def test_unsupported_authority_claim_caps_below_floor():
    score = score_confidence(
        grounding_hits=3, tool_errors=0, unsupported_authority_claims=1
    )
    assert score < 0.5  # too weak for normal autopilot thresholds


def test_verdict_counts_only_authority_claims():
    verdict = FaithfulnessVerdict.model_validate({
        "claims": [
            {"claim": "narxi 50000", "claim_type": "price", "supported": False},
            {"claim": "salom", "claim_type": "other", "supported": False},
        ]
    })
    assert verdict.unsupported_authority_claims == 1


@pytest.mark.asyncio
async def test_empty_reply_short_circuits_without_llm_call():
    bundle = CatalogAuthorityBundle(query="x")
    with patch("app.modules.agent_runtime_v2.faithfulness.generate_structured_json") as gen:
        verdict = await judge_faithfulness(reply_text="", authority=bundle, workspace_id=1)
    gen.assert_not_called()
    assert verdict.unsupported_authority_claims == 0


@pytest.mark.asyncio
async def test_judge_parses_llm_verdict():
    bundle = CatalogAuthorityBundle(query="narx")

    async def _fake(**kwargs):
        assert kwargs["operation"] == "faithfulness_judge"
        assert kwargs["response_schema"] is FaithfulnessVerdict
        return {"claims": [{"claim": "narxi 50000 UZS", "claim_type": "price", "supported": True, "supporting_fact_ref": "catalog_offer:2"}]}

    with patch("app.modules.agent_runtime_v2.faithfulness.generate_structured_json", _fake):
        verdict = await judge_faithfulness(reply_text="narxi 50000 UZS", authority=bundle, workspace_id=1)
    assert verdict.unsupported_authority_claims == 0
    assert verdict.claims[0].supporting_fact_ref == "catalog_offer:2"


@pytest.mark.asyncio
async def test_judge_failure_is_fail_safe():
    bundle = CatalogAuthorityBundle(query="narx")

    async def _fake(**_):
        return {}  # generate_structured_json returns {} on parse failure

    with patch("app.modules.agent_runtime_v2.faithfulness.generate_structured_json", _fake):
        verdict = await judge_faithfulness(reply_text="narxi 50000", authority=bundle, workspace_id=1)
    assert verdict.unsupported_authority_claims >= 1
