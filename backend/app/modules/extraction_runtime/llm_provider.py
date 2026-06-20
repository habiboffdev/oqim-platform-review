from __future__ import annotations

import asyncio
from typing import Any, Literal

from app.brain.prompt_registry import get_prompt_registry
from pydantic import BaseModel, Field

from app.modules.commercial_spine.contracts import LLMGatewayRequest
from app.modules.commercial_spine.llm_gateway import LLMGateway
from app.modules.extraction_runtime.contracts import (
    ExtractionCandidate,
    ExtractionModel,
    ExtractionRequest,
)
from app.modules.extraction_runtime.profiles import ExtractionProfile


class ExtractionCandidateProviderOutput(ExtractionModel):
    schema_version: str = "extraction_candidate_provider_output.v1"
    candidates: list[ExtractionCandidate] = Field(default_factory=list)


class LLMExtractionCandidate(ExtractionModel):
    """Gemini-compatible candidate schema.

    The strict `ExtractionCandidate` contract uses Pydantic constraints such as
    `gt=0`; Gemini's response schema rejects some of those JSON Schema keywords.
    LLM output is generated against this provider-safe shape, then immediately
    validated against the strict contract below.
    """

    schema_version: str = "extraction_candidate.v1"
    candidate_id: str
    workspace_id: int
    owner: str
    profile_ref: str
    kind: str
    entity_ref: str
    operation: str
    value: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    risk_tier: str
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_state: str
    requires_review: bool
    reason_code: str
    degraded_reasons: list[str] = Field(default_factory=list)


class LLMExtractionCandidateProviderOutput(ExtractionModel):
    schema_version: str = "extraction_candidate_provider_output.v1"
    candidates: list[LLMExtractionCandidate] = Field(default_factory=list)


class LLMBuyerIntentValue(ExtractionModel):
    detected_intent: Literal[
        "faq",
        "order",
        "payment",
        "negotiation",
        "media_inquiry",
        "support",
        "other",
        "unknown",
    ]
    response_strategy: Literal[
        "answer_directly",
        "clarify_variant",
        "confirm_next_step",
        "seller_confirmation",
        "safe_escalation",
        "no_reply",
        "unknown",
    ]
    answer_shape: Literal[
        "direct_answer",
        "one_clarifying_question",
        "safe_check",
        "receipt_request",
        "no_reply",
        "unknown",
    ]
    sales_moment: str
    customer_owned_missing_info: list[str] = Field(default_factory=list)
    business_owned_missing_info: list[str] = Field(default_factory=list)
    latest_intent_refs: list[str] = Field(default_factory=list)


class LLMBuyerIntentCandidate(ExtractionModel):
    schema_version: str = "extraction_candidate.v1"
    candidate_id: str
    workspace_id: int
    owner: Literal["action_runtime"]
    profile_ref: Literal["buyer_intent.v1"]
    kind: Literal["buyer_intent"]
    entity_ref: str
    operation: Literal["signal", "noop"] = "signal"
    value: LLMBuyerIntentValue
    confidence: float
    risk_tier: Literal["low", "medium", "high", "critical"]
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_state: Literal["valid", "missing", "unsupported", "conflicted"]
    requires_review: bool
    reason_code: str
    degraded_reasons: list[str] = Field(default_factory=list)


class LLMBuyerIntentProviderOutput(ExtractionModel):
    schema_version: str = "extraction_candidate_provider_output.v1"
    candidates: list[LLMBuyerIntentCandidate] = Field(default_factory=list)


class LLMGatewayCandidateProvider:
    """Schema-bound Universal Extraction provider backed by the LLM Gateway."""

    def __init__(
        self,
        *,
        gateway: LLMGateway,
        prompt_version: str = "1.0.0",
        timeout_ms: int = 30_000,
    ) -> None:
        self._gateway = gateway
        self._prompt_version = prompt_version
        self._timeout_ms = timeout_ms

    async def extract_candidates(
        self,
        *,
        request: ExtractionRequest,
        profiles: list[ExtractionProfile],
    ) -> list[ExtractionCandidate]:
        semaphore = asyncio.Semaphore(request.max_parallelism)

        async def run(profile: ExtractionProfile) -> list[ExtractionCandidate]:
            async with semaphore:
                return await self._extract_profile(request=request, profile=profile)

        chunks = await asyncio.gather(*(run(profile) for profile in profiles))
        return [candidate for chunk in chunks for candidate in chunk]

    async def _extract_profile(
        self,
        *,
        request: ExtractionRequest,
        profile: ExtractionProfile,
    ) -> list[ExtractionCandidate]:
        gateway = await self._gateway.generate(
            LLMGatewayRequest(
                route_key=profile.route_key,
                workflow_name="universal_extraction",
                prompt_id=profile.prompt_id,
                prompt_version=self._prompt_version,
                input_payload=_profile_input_payload(request=request, profile=profile),
                output_schema_name=profile.output_schema_name,
                workspace_id=request.scope.workspace_id,
                correlation_id=f"{request.correlation_id}:{profile.profile_ref}",
                source_refs=[request.source_ref, *request.allowed_evidence_refs()],
                timeout_ms=self._timeout_ms,
            ),
            output_model=_provider_output_model(profile),
        )
        if gateway.status != "ok" or gateway.parsed_output is None:
            raise RuntimeError(f"llm_gateway_status:{gateway.status}")
        candidates = _strict_candidates(gateway.parsed_output)
        return [
            candidate
            for candidate in candidates
            if candidate.profile_ref == profile.profile_ref
        ]


def _provider_output_model(profile: ExtractionProfile) -> type[BaseModel]:
    if profile.profile_ref == "buyer_intent.v1":
        return LLMBuyerIntentProviderOutput
    return LLMExtractionCandidateProviderOutput


def _strict_candidates(parsed_output: dict[str, Any]) -> list[ExtractionCandidate]:
    output = parsed_output if isinstance(parsed_output, dict) else {}
    raw_candidates = output.get("candidates")
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    candidates: list[ExtractionCandidate] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, dict):
            continue
        normalized = dict(raw_candidate)
        normalized["schema_version"] = "extraction_candidate.v1"
        candidates.append(ExtractionCandidate.model_validate(normalized))
    return candidates


def _profile_input_payload(
    *,
    request: ExtractionRequest,
    profile: ExtractionProfile,
) -> dict[str, Any]:
    return {
        "extraction_request": request.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json"),
        "prompt": _profile_prompt_payload(profile),
        "allowed_evidence_refs": list(request.allowed_evidence_refs()),
        "instruction": _profile_instruction(profile),
    }


def _profile_prompt_payload(profile: ExtractionProfile) -> dict[str, Any]:
    prompt = get_prompt_registry().load(profile.prompt_id, version="1.0.0")
    return {
        "prompt_id": prompt.id,
        "version": prompt.version,
        "digest": prompt.digest,
        "body": prompt.body,
    }


def _profile_instruction(profile: ExtractionProfile) -> dict[str, Any]:
    return {
        "profile_ref": profile.profile_ref,
        "allowed_owners": list(profile.owners),
        "allowed_candidate_kinds": list(profile.candidate_kinds),
        "evidence_refs_field": "allowed_evidence_refs",
        "output_contract": "extraction_candidate_provider_output.v1",
        "truth_boundary": "candidate_output_only",
        "candidate_schema": {
            "candidate_id": "stable idempotent id",
            "owner": "one allowed owner",
            "profile_ref": profile.profile_ref,
            "kind": "one allowed candidate kind",
            "entity_ref": "stable entity reference",
            "operation": "create|update|merge|link|signal|noop",
            "value": "structured candidate payload",
            "confidence": "0..1",
            "risk_tier": "low|medium|high",
            "evidence_refs": "array of evidence refs",
            "evidence_state": "valid|missing|unsupported|conflicted",
            "requires_review": "boolean",
            "reason_code": "stable reason",
        },
    }
