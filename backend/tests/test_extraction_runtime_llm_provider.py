import json
from types import SimpleNamespace

import pytest

from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.extraction_runtime.adapters import build_business_source_extraction_request
from app.modules.extraction_runtime.llm_provider import LLMGatewayCandidateProvider
from app.modules.extraction_runtime.profiles import default_profile_registry
from app.modules.extraction_runtime.runtime import (
    UniversalExtractionRuntime,
)


def test_default_extraction_profile_prompts_are_registered():
    from app.brain.prompt_registry import get_prompt_registry

    registry = get_prompt_registry()
    profiles = default_profile_registry().resolve(
        list(default_profile_registry().profile_refs())
    )

    for profile in profiles:
        prompt = registry.load(profile.prompt_id, version="1.0.0")
        assert prompt.status == "active"
        assert prompt.owner == "extraction-runtime"
        assert prompt.model_policy == profile.route_key
        assert prompt.output_schema == profile.output_schema_name
        assert prompt.body.strip()


class TraceRepository:
    def __init__(self) -> None:
        self.traces = []

    async def persist_llm_trace(self, trace):
        self.traces.append(trace)
        return True


def _request():
    return build_business_source_extraction_request(
        workspace_id=7,
        source_ref="source:catalog-note",
        source_kind="text",
        source_units=[
            SimpleNamespace(
                unit_ref="source_unit:catalog-note:1",
                source_text="Qizil ko'ylak narxi 120000 UZS",
                source_refs=["source:catalog-note"],
                state="ready",
                embedding_state="pending",
            ),
        ],
        media_assets=[],
        correlation_id="corr-llm-provider",
        idempotency_key="idem-llm-provider",
        max_source_units=10,
        max_media_assets=10,
    )


@pytest.mark.asyncio
async def test_llm_gateway_candidate_provider_uses_profile_route_and_schema():
    calls = []

    async def provider(request):
        calls.append(request)
        candidates = []
        if request.prompt_id == "extraction.commerce_generic":
            candidates = [
                {
                    "schema_version": "extraction_candidate.v1",
                    "candidate_id": "candidate:commerce:qizil-koylak",
                    "workspace_id": 7,
                    "owner": "commerce_core",
                    "profile_ref": "commerce_generic.v1",
                    "kind": "catalog_family",
                    "entity_ref": "catalog_product:qizil-koylak",
                    "operation": "create",
                    "value": {
                        "product_ref": "catalog_product:qizil-koylak",
                        "title": "Qizil ko'ylak",
                        "price": {"amount": 120000, "currency": "UZS"},
                    },
                    "confidence": 0.76,
                    "risk_tier": "medium",
                    "evidence_refs": ["source_unit:catalog-note:1"],
                    "evidence_state": "valid",
                    "requires_review": True,
                    "reason_code": "source_catalog_candidate",
                    "degraded_reasons": [],
                }
            ]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "extraction_candidate_provider_output.v1",
                    "candidates": candidates,
                }
            ),
            model_used="fixture-model",
        )

    repository = TraceRepository()
    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=LLMGatewayCandidateProvider(
            gateway=LLMGateway(repository=repository, provider=provider),
        ),
    )

    result = await runtime.extract(_request())

    assert result.status == "ok"
    assert result.accepted_candidates[0].kind == "catalog_family"
    assert result.accepted_candidates[0].evidence_refs == ["source_unit:catalog-note:1"]
    assert calls[0].route_key == "structured_fast"
    assert calls[0].prompt_id == "extraction.commerce_generic"
    assert calls[0].output_schema_name == "CommerceExtractionOutput"
    assert calls[0].input_payload["prompt"]["prompt_id"] == "extraction.commerce_generic"
    assert len(calls[0].input_payload["prompt"]["digest"]) == 64
    assert "generic commerce extractor" in calls[0].input_payload["prompt"]["body"].lower()
    assert calls[0].input_payload["allowed_evidence_refs"] == [
        "source_unit:catalog-note:1",
    ]
    assert calls[1].route_key == "structured_fast"
    assert calls[1].prompt_id == "extraction.generic_kb"
    assert calls[1].output_schema_name == "KnowledgeExtractionOutput"
    assert len(result.accepted_candidates) == 1
    assert repository.traces[0].status == "ok"
    assert repository.traces[1].status == "ok"


@pytest.mark.asyncio
async def test_runtime_degrades_when_llm_provider_returns_invalid_schema():
    async def provider(_request):
        return LLMProviderResponse(text="not-json", model_used="fixture-model")

    runtime = UniversalExtractionRuntime(
        profile_registry=default_profile_registry(),
        candidate_provider=LLMGatewayCandidateProvider(
            gateway=LLMGateway(repository=TraceRepository(), provider=provider),
        ),
    )

    result = await runtime.extract(_request())

    assert result.status == "degraded"
    assert result.accepted_candidates == []
    assert result.degraded_reasons == ["provider_error"]
