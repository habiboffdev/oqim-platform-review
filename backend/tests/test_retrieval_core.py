from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import (
    ContextualRetrievalCandidate,
    ContextualRetrievalResult,
    ContextualRetrievalTrace,
    MemoryFactWriteInput,
)
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.retrieval_core.contracts import (
    RetrievalAgentGroundingRequest,
    RetrievalContextRequest,
)
from app.modules.retrieval_core.service import RetrievalCoreService
from app.modules.retrieval_core.service import _fuse_contextual_result


def _fact_input(
    *,
    workspace: Workspace,
    fact_id: str,
    title: str,
    description: str,
) -> MemoryFactWriteInput:
    return MemoryFactWriteInput(
        workspace_id=workspace.id,
        fact_id=fact_id,
        fact_type="catalog_product",
        entity_ref=fact_id,
        value={"title": title, "description": description},
        source_refs=[f"source:{fact_id}"],
        source="manual",
        status="active",
        approval_state="confirmed",
        confidence=0.9,
        risk_tier="low",
        correlation_id=f"corr:{fact_id}",
        idempotency_key=f"idem:{fact_id}",
    )


def _media_fact_input(
    *,
    workspace: Workspace,
    fact_id: str,
    product_ref: str,
    alt_text: str,
) -> MemoryFactWriteInput:
    return MemoryFactWriteInput(
        workspace_id=workspace.id,
        fact_id=fact_id,
        fact_type="catalog_media",
        entity_ref=product_ref,
        value={
            "media_ref": fact_id,
            "product_ref": product_ref,
            "alt_text": alt_text,
            "approved": True,
        },
        source_refs=[f"source:{fact_id}"],
        source="manual",
        status="active",
        approval_state="confirmed",
        confidence=0.9,
        risk_tier="low",
        correlation_id=f"corr:{fact_id}",
        idempotency_key=f"idem:{fact_id}",
    )


def _source_media_fact_input(
    *,
    workspace: Workspace,
    fact_id: str,
    source_ref: str,
    alt_text: str,
) -> MemoryFactWriteInput:
    return MemoryFactWriteInput(
        workspace_id=workspace.id,
        fact_id=fact_id,
        fact_type="business_source_media_fact",
        entity_ref=f"workspace:source_media:{fact_id}",
        value={
            "media_ref": fact_id.removeprefix("business_source_media:"),
            "source_ref": source_ref,
            "alt_text": alt_text,
            "source_kind": "customer_photo",
        },
        source_refs=[source_ref, fact_id],
        source="manual",
        status="active",
        approval_state="confirmed",
        confidence=0.84,
        risk_tier="medium",
        correlation_id=f"corr:{fact_id}",
        idempotency_key=f"idem:{fact_id}",
    )


def _knowledge_fact_input(
    *,
    workspace: Workspace,
    fact_id: str,
    question: str,
    answer: str,
) -> MemoryFactWriteInput:
    return MemoryFactWriteInput(
        workspace_id=workspace.id,
        fact_id=fact_id,
        fact_type="knowledge_fact",
        entity_ref=f"business:faq:{fact_id}",
        value={"question": question, "answer": answer, "topic": question},
        source_refs=[f"source:{fact_id}"],
        source="manual",
        status="active",
        approval_state="confirmed",
        confidence=0.9,
        risk_tier="low",
        correlation_id=f"corr:{fact_id}",
        idempotency_key=f"idem:{fact_id}",
    )


def _autocrm_noise_fact_input(
    *,
    workspace: Workspace,
    index: int,
) -> MemoryFactWriteInput:
    return MemoryFactWriteInput(
        workspace_id=workspace.id,
        fact_id=f"autocrm_customer:noise:{index}",
        fact_type="autocrm_customer",
        entity_ref=f"customer:{index}",
        value={"stage": "unknown", "note": f"noise {index}"},
        source_refs=[f"message:{index}"],
        source="integration",
        status="active",
        approval_state="confirmed",
        confidence=0.9,
        risk_tier="low",
        correlation_id=f"corr:noise:{index}",
        idempotency_key=f"idem:noise:{index}",
    )


async def _seed_adapter_facts(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    memory = BusinessBrainMemoryService(
        repository=CommercialSpineRepository(db_session),
    )
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:basic-adapter",
            title="Basic adapter",
            description="Simple adapter with low margin.",
        )
    )
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:premium-adapter",
            title="Premium adapter",
            description="Adapter with HDMI support and better customer fit.",
        )
    )


def _candidate(fact_id: str) -> ContextualRetrievalCandidate:
    return ContextualRetrievalCandidate(
        fact_id=fact_id,
        fact_type="catalog_product",
        entity_ref=fact_id,
        value={"title": fact_id},
        source_refs=[f"source:{fact_id}"],
        confidence=0.9,
        risk_tier="low",
        status="active",
        freshness={"state": "fresh"},
        contextual_text=fact_id,
        retrieval_scores={},
        source_units=[],
    )


def test_retrieval_core_fuses_channel_ranks_without_raw_score_scale() -> None:
    result = ContextualRetrievalResult(
        workspace_id=1,
        candidates=[
            _candidate("catalog:semantic-only"),
            _candidate("catalog:both"),
            _candidate("catalog:keyword-only"),
        ],
        trace=ContextualRetrievalTrace(
            selected_fact_ids=[
                "catalog:semantic-only",
                "catalog:both",
                "catalog:keyword-only",
            ],
            retrieval_channels=["semantic", "keyword"],
            candidate_scores={
                "catalog:semantic-only": {"semantic": 100.0},
                "catalog:both": {"semantic": 1.0, "keyword": 1.0},
                "catalog:keyword-only": {"keyword": 2.0},
            },
        ),
    )

    fused = _fuse_contextual_result(result, limit=3)

    assert [candidate.fact_id for candidate in fused.candidates] == [
        "catalog:both",
        "catalog:semantic-only",
        "catalog:keyword-only",
    ]
    assert fused.candidates[0].retrieval_scores["fusion"] > (
        fused.candidates[1].retrieval_scores["fusion"]
    )
    assert fused.trace.candidate_scores["catalog:both"]["fusion"] > 0
    assert "fusion" in fused.trace.retrieval_channels


async def test_retrieval_core_default_search_finds_kb_after_projection_noise(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    await memory.write_memory_fact(
        _knowledge_fact_input(
            workspace=workspace,
            fact_id="knowledge:warranty:default-search",
            question="kafolat bormi?",
            answer="7 kunlik tekshiruv kafolati bor.",
        )
    )
    for index in range(260):
        await memory.write_memory_fact(
            _autocrm_noise_fact_input(workspace=workspace, index=index)
        )

    result = await RetrievalCoreService(repository=repository).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            query_text="kafolat bormi?",
            enable_semantic=False,
            limit=5,
        )
    )

    assert result.candidates[0].fact_id == "knowledge:warranty:default-search"
    assert result.trace.selected_fact_ids == ["knowledge:warranty:default-search"]


async def test_retrieval_core_query_rewrite_broadens_keyword_recall(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:lightning-digital-av-adapter",
            title="Apple Lightning Digital AV Adapter",
            description="Works with TV display.",
        )
    )

    async def provider(request) -> LLMProviderResponse:
        assert request.workflow_name == "retrieval_query_rewrite"
        assert request.prompt_id == "retrieval_core.query_rewrite"
        assert request.input_payload["query_text"] == "hdmi perehodnik"
        prompt = request.input_payload["prompt"]
        assert prompt["prompt_id"] == "retrieval_core.query_rewrite"
        assert prompt["registry_state"] == "loaded"
        assert "Return only JSON matching `RetrievalQueryRewriteOutput`" in prompt["body"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "retrieval_query_rewrite_output.v1",
                    "rewrites": ["lightning digital av adapter"],
                }
            ),
            model_used="test-rewriter",
        )

    result = await RetrievalCoreService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="hdmi perehodnik",
            enable_semantic=False,
            enable_query_rewrite=True,
            limit=5,
        )
    )

    assert [candidate.fact_id for candidate in result.candidates] == [
        "catalog:lightning-digital-av-adapter"
    ]
    assert result.trace.query_rewrites == ["lightning digital av adapter"]
    assert len(result.trace.llm_trace_ids) == 1
    assert "query_rewrite" in result.trace.retrieval_channels
    assert (
        result.trace.candidate_scores["catalog:lightning-digital-av-adapter"][
            "query_rewrite_keyword"
        ]
        > 0
    )
    assert result.degraded_reasons == []


async def test_retrieval_core_agentic_search_expands_fact_types_and_queries(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:lightning-digital-av-adapter",
            title="Apple Lightning Digital AV Adapter",
            description="Works with TV display.",
        )
    )

    async def provider(request) -> LLMProviderResponse:
        assert request.workflow_name == "retrieval_agentic_search_plan"
        assert request.prompt_id == "retrieval_core.agentic_search_plan"
        assert request.input_payload["query_text"] == "hdmi perehodnik"
        prompt = request.input_payload["prompt"]
        assert prompt["prompt_id"] == "retrieval_core.agentic_search_plan"
        assert prompt["registry_state"] == "loaded"
        assert "Return only JSON matching `RetrievalAgenticSearchOutput`" in prompt["body"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "retrieval_agentic_search_output.v1",
                    "queries": ["lightning digital av adapter"],
                    "fact_types": ["catalog_product"],
                    "query_modalities": ["image"],
                }
            ),
            model_used="test-agentic-planner",
        )

    result = await RetrievalCoreService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact"],
            query_text="hdmi perehodnik",
            enable_semantic=False,
            enable_agentic_search=True,
            limit=5,
        )
    )

    assert [candidate.fact_id for candidate in result.candidates] == [
        "catalog:lightning-digital-av-adapter"
    ]
    assert result.trace.agentic_queries == ["lightning digital av adapter"]
    assert result.trace.agentic_fact_types == ["catalog_product"]
    assert result.trace.agentic_modalities == ["image"]
    assert "agentic_search" in result.trace.retrieval_channels
    assert (
        result.trace.candidate_scores["catalog:lightning-digital-av-adapter"][
            "agentic_keyword"
        ]
        > 0
    )


async def test_retrieval_core_agentic_media_alias_recalls_source_media_facts(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    await memory.write_memory_fact(
        _source_media_fact_input(
            workspace=workspace,
            fact_id="business_source_media:imported-pdf-page-7",
            source_ref="source:pdf:catalog:page:7",
            alt_text="PDF page shows ruby ring warranty and front side photo",
        )
    )

    async def provider(request) -> LLMProviderResponse:
        assert request.workflow_name == "retrieval_agentic_search_plan"
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "retrieval_agentic_search_output.v1",
                    "queries": ["ruby ring warranty front side photo"],
                    "fact_types": ["media_evidence_fact"],
                    "query_modalities": ["pdf", "image"],
                }
            ),
            model_used="test-agentic-planner",
        )

    result = await RetrievalCoreService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            query_text="PDFdagi ruby ring rasmi va kafolati",
            enable_semantic=False,
            enable_agentic_search=True,
            limit=5,
        )
    )

    assert result.candidates[0].fact_id == "business_source_media:imported-pdf-page-7"
    assert result.trace.agentic_fact_types == [
        "business_source_media_fact",
        "catalog_media",
    ]
    assert result.trace.agentic_modalities == ["pdf", "image"]
    assert "agentic_search" in result.trace.retrieval_channels


async def test_retrieval_core_agentic_search_timeout_degrades_but_uses_original_query(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:hdmi-perehodnik",
            title="HDMI perehodnik",
            description="Adapter for TV display.",
        )
    )

    async def provider(_request) -> LLMProviderResponse:
        raise TimeoutError("planner timeout")

    result = await RetrievalCoreService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="hdmi perehodnik",
            enable_semantic=False,
            enable_agentic_search=True,
            limit=5,
        )
    )

    assert [candidate.fact_id for candidate in result.candidates] == [
        "catalog:hdmi-perehodnik"
    ]
    assert result.trace.agentic_queries == []
    assert "agentic_search:timeout" in result.degraded_reasons


async def test_retrieval_core_expands_media_modalities_to_media_fact_recall(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:ruby-ring",
            title="Ruby ring",
            description="Formal jewelry product.",
        )
    )
    await memory.write_memory_fact(
        _media_fact_input(
            workspace=workspace,
            fact_id="catalog_media:ruby-ring:main",
            product_ref="catalog:ruby-ring",
            alt_text="customer photo shows ruby ring front side",
        )
    )

    result = await RetrievalCoreService(repository=repository).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="customer photo front side",
            query_modalities=["image"],
            enable_semantic=False,
            limit=5,
        )
    )

    assert [candidate.fact_id for candidate in result.candidates] == [
        "catalog_media:ruby-ring:main"
    ]
    assert result.trace.expanded_fact_types == [
        "catalog_product",
        "catalog_media",
        "business_source_media_fact",
    ]
    assert "multimodal" in result.trace.retrieval_channels


async def test_retrieval_core_expands_image_queries_to_source_media_recall(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:plain-wallet",
            title="Plain wallet",
            description="Unrelated leather wallet.",
        )
    )
    await memory.write_memory_fact(
        _source_media_fact_input(
            workspace=workspace,
            fact_id="business_source_media:customer-photo-ruby",
            source_ref="source_media:customer:photo:1",
            alt_text="customer photo shows ruby ring front side",
        )
    )

    result = await RetrievalCoreService(repository=repository).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="customer photo ruby ring front side",
            query_modalities=["image"],
            enable_semantic=False,
            limit=5,
        )
    )

    assert result.candidates[0].fact_id == "business_source_media:customer-photo-ruby"
    assert result.candidates[0].fact_type == "business_source_media_fact"
    assert result.trace.expanded_fact_types == [
        "catalog_product",
        "catalog_media",
        "business_source_media_fact",
    ]
    assert "multimodal" in result.trace.retrieval_channels


async def test_retrieval_core_is_workspace_scoped_for_same_query(
    db_session: AsyncSession,
    workspace: Workspace,
    workspace_b: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:workspace-a-adapter",
            title="Shared adapter",
            description="Workspace A private product.",
        )
    )
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace_b,
            fact_id="catalog:workspace-b-adapter",
            title="Shared adapter",
            description="Workspace B private product.",
        )
    )

    service = RetrievalCoreService(repository=repository)
    result_a = await service.retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="shared adapter",
            enable_semantic=False,
        )
    )
    result_b = await service.retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace_b.id,
            requested_fact_types=["catalog_product"],
            query_text="shared adapter",
            enable_semantic=False,
        )
    )

    assert [candidate.fact_id for candidate in result_a.candidates] == [
        "catalog:workspace-a-adapter"
    ]
    assert [candidate.fact_id for candidate in result_b.candidates] == [
        "catalog:workspace-b-adapter"
    ]


async def test_retrieval_core_query_rewrite_timeout_degrades_but_uses_original_query(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    await _seed_adapter_facts(db_session, workspace)
    repository = CommercialSpineRepository(db_session)

    async def provider(request) -> LLMProviderResponse:
        raise TimeoutError()

    result = await RetrievalCoreService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="premium adapter",
            enable_semantic=False,
            enable_query_rewrite=True,
            limit=5,
        )
    )

    assert result.candidates[0].fact_id == "catalog:premium-adapter"
    assert result.trace.query_rewrites == []
    assert len(result.trace.llm_trace_ids) == 1
    assert "keyword" in result.trace.retrieval_channels
    assert "query_rewrite:timeout" in result.degraded_reasons


async def test_retrieval_core_applies_reranker_scores_to_contextual_results(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    await _seed_adapter_facts(db_session, workspace)

    async def fake_rerank(
        query: str,
        candidates: list[dict[str, Any]],
        *,
        text_field: str,
        top_n: int,
    ) -> list[dict[str, Any]]:
        assert query == "adapter kerak"
        assert text_field == "text"
        by_id = {candidate["fact_id"]: candidate for candidate in candidates}
        return [
            {**by_id["catalog:premium-adapter"], "relevance_score": 0.97},
            {**by_id["catalog:basic-adapter"], "relevance_score": 0.41},
        ][:top_n]

    monkeypatch.setattr(
        "app.modules.retrieval_core.service.reranker.rerank",
        fake_rerank,
    )

    result = await RetrievalCoreService(
        repository=CommercialSpineRepository(db_session),
    ).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="adapter kerak",
            enable_semantic=False,
            enable_rerank=True,
            limit=5,
        )
    )

    assert [candidate.fact_id for candidate in result.candidates] == [
        "catalog:premium-adapter",
        "catalog:basic-adapter",
    ]
    assert result.candidates[0].retrieval_scores["rerank"] == 0.97
    assert result.trace.candidate_scores["catalog:premium-adapter"]["rerank"] == 0.97
    assert result.trace.rerank_state == "requested"
    assert "rerank" in result.trace.retrieval_channels
    assert "rerank_unavailable" not in result.degraded_reasons


async def test_agent_grounding_uses_retrieval_core_rerank_boundary(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    await _seed_adapter_facts(db_session, workspace)

    async def fake_rerank(
        query: str,
        candidates: list[dict[str, Any]],
        *,
        text_field: str,
        top_n: int,
    ) -> list[dict[str, Any]]:
        by_id = {candidate["fact_id"]: candidate for candidate in candidates}
        return [
            {**by_id["catalog:premium-adapter"], "relevance_score": 0.99},
            {**by_id["catalog:basic-adapter"], "relevance_score": 0.4},
        ][:top_n]

    monkeypatch.setattr(
        "app.modules.retrieval_core.service.reranker.rerank",
        fake_rerank,
    )

    bundle = await RetrievalCoreService(
        repository=CommercialSpineRepository(db_session),
    ).build_agent_grounding(
        RetrievalAgentGroundingRequest(
            workspace_id=workspace.id,
            agent_kind="seller_agent",
            requested_fact_types=["catalog_product"],
            query_text="adapter kerak",
            enable_semantic=False,
            enable_rerank=True,
        )
    )

    products = bundle.families["catalog_product"]
    assert [product["fact_id"] for product in products] == [
        "catalog:premium-adapter",
        "catalog:basic-adapter",
    ]
    assert products[0]["retrieval_scores"]["rerank"] == 0.99
    assert bundle.trace.rerank_state == "requested"


async def test_agent_grounding_defaults_to_contextual_rank_system(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    await memory.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:lightning-digital-av-adapter",
            title="Apple Lightning Digital AV Adapter",
            description="HDMI TV display adapter from the imported PDF catalog.",
        )
    )
    calls = {"rewrite": 0, "rerank": 0}

    async def provider(request) -> LLMProviderResponse:
        calls["rewrite"] += 1
        assert request.workflow_name == "retrieval_query_rewrite"
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "retrieval_query_rewrite_output.v1",
                    "rewrites": ["lightning digital av adapter"],
                }
            ),
            model_used="test-rewriter",
        )

    async def fake_rerank(
        query: str,
        candidates: list[dict[str, Any]],
        *,
        text_field: str,
        top_n: int,
    ) -> list[dict[str, Any]]:
        calls["rerank"] += 1
        assert query == "hdmi perehodnik"
        assert text_field == "text"
        assert "imported PDF catalog" in candidates[0]["text"]
        return [
            {**candidates[0], "relevance_score": 0.98},
        ][:top_n]

    monkeypatch.setattr(
        "app.modules.retrieval_core.service.reranker.rerank",
        fake_rerank,
    )

    bundle = await RetrievalCoreService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).build_agent_grounding(
        RetrievalAgentGroundingRequest(
            workspace_id=workspace.id,
            agent_kind="seller_agent",
            requested_fact_types=["catalog_product"],
            query_text="hdmi perehodnik",
            enable_semantic=False,
        )
    )

    products = bundle.families["catalog_product"]
    assert [product["fact_id"] for product in products] == [
        "catalog:lightning-digital-av-adapter"
    ]
    assert products[0]["retrieval_scores"]["query_rewrite_keyword"] > 0
    assert products[0]["retrieval_scores"]["rerank"] == 0.98
    assert calls == {"rewrite": 1, "rerank": 1}
    assert bundle.trace.query_rewrites == ["lightning digital av adapter"]
    assert bundle.trace.rerank_state == "requested"
    assert "query_rewrite" in bundle.trace.retrieval_channels
    assert "rerank" in bundle.trace.retrieval_channels


async def test_contextual_rank_system_recalls_mixed_onboarding_sources(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    memory = BusinessBrainMemoryService(repository=repository)
    mixed_facts = [
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="catalog:pdf:sat-premium",
            fact_type="catalog_product",
            entity_ref="catalog:program:sat-premium",
            value={
                "title": "SAT Premium program",
                "description": "Imported PDF program: Premium costs 60 000 so'm for 30 days.",
            },
            source_refs=["pdf:startup-program:page-2"],
            source="onboarding",
            status="active",
            approval_state="confirmed",
            confidence=0.92,
            risk_tier="low",
            correlation_id="corr:mixed:pdf",
            idempotency_key="idem:mixed:pdf",
        ),
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="knowledge:website:payment",
            fact_type="knowledge_fact",
            entity_ref="business:faq:payment",
            value={
                "question": "To'lovdan keyin nima bo'ladi?",
                "answer": "Website FAQ says payment receipt activates the program account.",
            },
            source_refs=["website:satstatino.io:faq"],
            source="onboarding",
            status="active",
            approval_state="confirmed",
            confidence=0.9,
            risk_tier="low",
            correlation_id="corr:mixed:website",
            idempotency_key="idem:mixed:website",
        ),
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="seller_rule:telegram:delivery-phone",
            fact_type="seller_rule_fact",
            entity_ref="business:rule:delivery-phone",
            value={
                "rule": "Telegram channel history says delivery questions require district and phone before promise.",
            },
            source_refs=["telegram:channel:satstation:message:44"],
            source="onboarding",
            status="active",
            approval_state="confirmed",
            confidence=0.88,
            risk_tier="medium",
            correlation_id="corr:mixed:telegram",
            idempotency_key="idem:mixed:telegram",
        ),
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="business_source_media:pdf-page-payment",
            fact_type="business_source_media_fact",
            entity_ref="workspace:source_media:pdf-page-payment",
            value={
                "media_ref": "pdf-page-payment",
                "source_ref": "pdf:startup-program:page-3",
                "alt_text": "PDF page image shows UZCARD/HUMO payment receipt instruction.",
                "source_kind": "pdf_page_image",
            },
            source_refs=["pdf:startup-program:page-3", "source_media:pdf-page-payment"],
            source="onboarding",
            status="active",
            approval_state="confirmed",
            confidence=0.84,
            risk_tier="medium",
            correlation_id="corr:mixed:media",
            idempotency_key="idem:mixed:media",
        ),
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id="conversation_pair:telegram-dm:activation",
            fact_type="conversation_pair_fact",
            entity_ref="conversation:273",
            value={
                "customer_turn": "Pk",
                "seller_turn": "Aktivlashtirildi, loyiha davomida fikr va takliflaringizni yozib qoldiring.",
            },
            source_refs=["conversation:273:messages", "message:478"],
            source="onboarding",
            status="active",
            approval_state="confirmed",
            confidence=0.86,
            risk_tier="low",
            correlation_id="corr:mixed:conversation",
            idempotency_key="idem:mixed:conversation",
        ),
    ]
    for fact in mixed_facts:
        await memory.write_memory_fact(fact)

    async def provider(request) -> LLMProviderResponse:
        assert request.workflow_name == "retrieval_agentic_search_plan"
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "retrieval_agentic_search_output.v1",
                    "queries": [
                        "payment receipt activation",
                        "premium 60 000 sat program",
                        "delivery district phone rule",
                        "aktivlashtirildi seller reply",
                    ],
                    "fact_types": [
                        "catalog_product",
                        "knowledge_fact",
                        "seller_rule_fact",
                        "conversation_pair_fact",
                        "media_evidence_fact",
                    ],
                    "query_modalities": ["pdf", "image"],
                }
            ),
            model_used="test-agentic-planner",
        )

    async def fake_rerank(
        query: str,
        candidates: list[dict[str, Any]],
        *,
        text_field: str,
        top_n: int,
    ) -> list[dict[str, Any]]:
        assert query == "chek yubordim, aktivlashtirish va yetkazish qoidasi kerak"
        assert text_field == "text"
        by_id = {candidate["fact_id"]: candidate for candidate in candidates}
        expected = {
            "catalog:pdf:sat-premium",
            "knowledge:website:payment",
            "seller_rule:telegram:delivery-phone",
            "business_source_media:pdf-page-payment",
            "conversation_pair:telegram-dm:activation",
        }
        assert expected.issubset(by_id)
        assert (
            "delivery questions require district and phone"
            in by_id["seller_rule:telegram:delivery-phone"]["text"]
        )
        ordered = [
            ("knowledge:website:payment", 0.99),
            ("conversation_pair:telegram-dm:activation", 0.97),
            ("business_source_media:pdf-page-payment", 0.95),
            ("catalog:pdf:sat-premium", 0.93),
            ("seller_rule:telegram:delivery-phone", 0.91),
        ]
        return [
            {**by_id[fact_id], "relevance_score": score}
            for fact_id, score in ordered
        ][:top_n]

    monkeypatch.setattr(
        "app.modules.retrieval_core.service.reranker.rerank",
        fake_rerank,
    )

    result = await RetrievalCoreService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            query_text="chek yubordim, aktivlashtirish va yetkazish qoidasi kerak",
            enable_semantic=False,
            enable_agentic_search=True,
            enable_rerank=True,
            limit=10,
        )
    )

    assert [candidate.fact_id for candidate in result.candidates[:5]] == [
        "knowledge:website:payment",
        "conversation_pair:telegram-dm:activation",
        "business_source_media:pdf-page-payment",
        "catalog:pdf:sat-premium",
        "seller_rule:telegram:delivery-phone",
    ]
    assert result.candidates[0].retrieval_scores["rerank"] == 0.99
    assert result.trace.agentic_modalities == ["pdf", "image"]
    assert result.trace.agentic_fact_types == [
        "catalog_product",
        "knowledge_fact",
        "seller_rule_fact",
        "conversation_pair_fact",
        "business_source_media_fact",
        "catalog_media",
    ]
    assert result.trace.rerank_state == "requested"
    assert "agentic_search" in result.trace.retrieval_channels
    assert "rerank" in result.trace.retrieval_channels
    assert "rerank_unavailable" not in result.degraded_reasons


async def test_agent_grounding_does_not_rerank_without_query_text(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    await _seed_adapter_facts(db_session, workspace)

    bundle = await RetrievalCoreService(
        repository=CommercialSpineRepository(db_session),
    ).build_agent_grounding(
        RetrievalAgentGroundingRequest(
            workspace_id=workspace.id,
            agent_kind="seller_agent",
            requested_fact_types=["catalog_product"],
            enable_semantic=False,
        )
    )

    assert bundle.families["catalog_product"]
    assert bundle.trace.rerank_state == "not_requested"
    assert "rerank_query_missing" not in bundle.degraded_reasons


async def test_retrieval_core_marks_rerank_degraded_when_ranker_falls_back(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    await _seed_adapter_facts(db_session, workspace)

    async def fallback_rerank(
        query: str,
        candidates: list[dict[str, Any]],
        *,
        text_field: str,
        top_n: int,
    ) -> list[dict[str, Any]]:
        return candidates[:top_n]

    monkeypatch.setattr(
        "app.modules.retrieval_core.service.reranker.rerank",
        fallback_rerank,
    )

    result = await RetrievalCoreService(
        repository=CommercialSpineRepository(db_session),
    ).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="adapter kerak",
            enable_semantic=False,
            enable_rerank=True,
            limit=5,
        )
    )

    assert result.trace.rerank_state == "degraded"
    assert "rerank_unavailable" in result.degraded_reasons
    assert "rerank" in result.trace.retrieval_channels
