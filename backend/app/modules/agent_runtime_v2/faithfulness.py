"""Per-reply faithfulness judge (Phase 5 Slice 5.1A).

Replaces the hardcoded 0.8 model-confidence baseline. A cheap FLASH_LITE call
checks each authority claim (price/stock/offer/delivery/refund/policy) in the
drafted reply against the APPROVED catalog authority bundle. Any unsupported
authority claim caps confidence below the send floor (see confidence.py). The
LLM owns the semantic match; deterministic code owns the gate."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.brain.llm import generate_structured_json
from app.brain.llm_policy import FLASH_LITE_CHAIN
from app.brain.prompt_payload import prompt_cache_payload_for_asset
from app.brain.prompt_registry import PromptAsset, get_prompt_registry
from app.modules.catalog_authority.contracts import CatalogAuthorityBundle

logger = logging.getLogger(__name__)

# Must stay in sync with FaithfulnessClaim.claim_type (every value except "other").
_AUTHORITY_CLAIM_TYPES = {"price", "stock", "offer", "delivery", "refund", "policy"}


class FaithfulnessClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    claim_type: Literal["price", "stock", "offer", "delivery", "refund", "policy", "other"]
    supported: bool
    supporting_fact_ref: str | None = None


class FaithfulnessVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[FaithfulnessClaim] = Field(default_factory=list)
    judge_failed: bool = False  # set when the judge call/parse failed (fail-safe)

    @property
    def unsupported_authority_claims(self) -> int:
        # A judge failure is treated as an unsupported authority condition so the
        # confidence cap fires and the reply escalates to PROPOSE.
        if self.judge_failed:
            return 1
        return sum(
            1
            for claim in self.claims
            if claim.claim_type in _AUTHORITY_CLAIM_TYPES and not claim.supported
        )


_FAITHFULNESS_PROMPT_ID = "agent_runtime.faithfulness_judge"
_FAITHFULNESS_PROMPT_VERSION = "1.0.0"


def _failsafe(reason: str, *, workspace_id: int) -> FaithfulnessVerdict:
    # A judge failure is itself an unsupported-authority condition. Log it so a
    # silent fail-safe (which forces PROPOSE) is visible in production.
    logger.warning(
        "faithfulness judge failed (%s); failing safe to PROPOSE", reason,
        extra={"workspace_id": workspace_id},
    )
    return FaithfulnessVerdict(judge_failed=True)


async def judge_faithfulness(
    *,
    reply_text: str,
    authority: CatalogAuthorityBundle,
    workspace_id: int,
) -> FaithfulnessVerdict:
    if not reply_text.strip():
        return FaithfulnessVerdict(claims=[])
    approved = "\n".join(authority.approved_authority_lines()) or "(no approved authority)"
    prompt = f"APPROVED AUTHORITY:\n{approved}\n\nSELLER REPLY:\n{reply_text}"
    try:
        result = await generate_structured_json(
            chain=FLASH_LITE_CHAIN,
            system=_faithfulness_system_prompt(),
            prompt=prompt,
            response_schema=FaithfulnessVerdict,
            operation="faithfulness_judge",
            workspace_id=workspace_id,
            temperature=0.0,
            prompt_cache=_faithfulness_prompt_cache(),
        )
    except Exception:
        logger.warning(
            "faithfulness judge raised; failing safe to PROPOSE",
            exc_info=True, extra={"workspace_id": workspace_id},
        )
        return _failsafe("exception", workspace_id=workspace_id)
    if not result:
        return _failsafe("empty_result", workspace_id=workspace_id)
    try:
        return FaithfulnessVerdict.model_validate(result)
    except Exception:
        return _failsafe("invalid_schema", workspace_id=workspace_id)


def _faithfulness_system_prompt() -> str:
    return _faithfulness_prompt_asset().body.strip()


def _faithfulness_prompt_cache() -> dict | None:
    return prompt_cache_payload_for_asset(
        _faithfulness_prompt_asset(),
        cache_scope="agent_runtime.faithfulness_judge",
    )


@lru_cache(maxsize=1)
def _faithfulness_prompt_asset() -> PromptAsset:
    return get_prompt_registry().load(
        _FAITHFULNESS_PROMPT_ID,
        version=_FAITHFULNESS_PROMPT_VERSION,
    )
