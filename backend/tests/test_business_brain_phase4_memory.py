from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.workspace import Workspace
from app.modules.business_brain.contracts import BusinessBrainIndexRecordContract
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import (
    AgentGroundingRequest,
    ContextualRetrievalResult,
    ContextualRetrievalRequest,
    ContextualRetrievalTrace,
    ConversationPairMiningInput,
    CorrectionEpisodeInput,
    MemoryFactWriteInput,
    RuleCompilationRequest,
    SourceUnitRebuildRequest,
    VoiceProjectionRequest,
)
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.retrieval_core.service import RetrievalCoreService


def _memory_service(
    db_session: AsyncSession,
    *,
    provider: AsyncMock | None = None,
) -> BusinessBrainMemoryService:
    repository = CommercialSpineRepository(db_session)
    gateway = LLMGateway(repository=repository, provider=provider) if provider else None
    return BusinessBrainMemoryService(repository=repository, gateway=gateway)


def _fact_input(
    *,
    workspace: Workspace,
    fact_id: str,
    fact_type: str,
    entity_ref: str,
    value: dict[str, Any],
    source_refs: list[str],
    source: str = "manual",
    status: str = "active",
    approval_state: str = "confirmed",
    confidence: float = 0.9,
    risk_tier: str = "low",
    supersedes_fact_id: str | None = None,
) -> MemoryFactWriteInput:
    return MemoryFactWriteInput(
        workspace_id=workspace.id,
        fact_id=fact_id,
        fact_type=fact_type,
        entity_ref=entity_ref,
        value=value,
        source_refs=source_refs,
        source=source,
        status=status,
        approval_state=approval_state,
        confidence=confidence,
        risk_tier=risk_tier,
        correlation_id=f"corr:{fact_id}",
        idempotency_key=f"idem:{fact_id}",
        supersedes_fact_id=supersedes_fact_id,
    )


async def test_knowledge_update_and_onboarding_live_equivalence(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = _memory_service(db_session)

    onboarding = await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="knowledge:delivery:onboarding",
            fact_type="knowledge_fact",
            entity_ref="business:delivery",
            value={
                "topic": "delivery",
                "answer": "Toshkent ichida 24 soat ichida yetkazamiz.",
            },
            source_refs=["onboarding:answer:delivery"],
            source="onboarding",
        )
    )
    live = await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="knowledge:delivery:live",
            fact_type="knowledge_fact",
            entity_ref="business:delivery",
            value={
                "topic": "delivery",
                "answer": "Toshkent ichida 12 soat ichida yetkazamiz.",
            },
            source_refs=["owner_message:delivery-update"],
            supersedes_fact_id="knowledge:delivery:onboarding",
        )
    )
    bundle = await service.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact"],
            entity_refs=["business:delivery"],
            requested_slots=["knowledge_fact", "seller_rule_fact"],
        )
    )

    assert onboarding.fact.fact_type == live.fact.fact_type == "knowledge_fact"
    assert onboarding.update.source == "onboarding"
    assert live.update.source == "manual"
    assert bundle.candidates[0].fact_id == "knowledge:delivery:live"
    assert bundle.candidates[0].value["answer"] == "Toshkent ichida 12 soat ichida yetkazamiz."
    assert bundle.missing_evidence == ["seller_rule_fact"]
    assert "knowledge:delivery:onboarding" in bundle.trace.rejected_fact_ids


async def test_retrieval_fetches_each_requested_fact_type_before_limit(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = _memory_service(db_session)
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="knowledge:warranty:stable",
            fact_type="knowledge_fact",
            entity_ref="business:faq:warranty",
            value={
                "topic": "warranty",
                "question": "kafolat bormi?",
                "answer": "7 kunlik tekshiruv kafolati bor.",
            },
            source_refs=["source:warranty"],
        )
    )
    for index in range(260):
        await service.write_memory_fact(
            _fact_input(
                workspace=workspace,
                fact_id=f"autocrm_customer:noise:{index}",
                fact_type="autocrm_customer",
                entity_ref=f"customer:{index}",
                value={"stage": "unknown", "note": f"noise {index}"},
                source_refs=[f"message:{index}"],
            )
        )

    bundle = await service.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["autocrm_customer", "knowledge_fact"],
            requested_slots=["knowledge_fact"],
            query_text="kafolat bormi?",
            include_source_units=True,
        )
    )

    assert [candidate.fact_id for candidate in bundle.candidates] == [
        "knowledge:warranty:stable"
    ]
    assert bundle.missing_evidence == []


async def test_rule_compiler_emits_approval_proposal_without_execution(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    repository = CommercialSpineRepository(db_session)
    service = BusinessBrainMemoryService(repository=repository)
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="rule:calendar:meeting",
            fact_type="seller_rule_fact",
            entity_ref="business:meetings",
            value={
                "trigger": "meeting_confirmed",
                "instruction": "Create a calendar event after both sides confirm time.",
                "capability": "calendar_event",
                "mode": "automation_candidate",
            },
            source_refs=["owner_message:meeting-rule"],
            risk_tier="medium",
        )
    )

    proposal = await service.compile_rule_to_proposal(
        RuleCompilationRequest(
            workspace_id=workspace.id,
            rule_fact_id="rule:calendar:meeting",
            conversation_id=conversation.id,
            customer_id=customer.id,
            correlation_id="corr-rule-compile",
        )
    )

    assert proposal.action_type == "compile_automation_rule"
    assert proposal.execution_mode == "ask_seller_confirmation"
    assert proposal.requires_approval is True
    assert proposal.payload["rule_fact_id"] == "rule:calendar:meeting"
    assert proposal.payload["capability"] == "calendar_event"


async def test_voice_projection_excludes_low_quality_examples(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = _memory_service(db_session)
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="voice:trait:warm",
            fact_type="voice_fact",
            entity_ref="seller_voice",
            value={"trait": "warm", "guidance": "Use short friendly replies."},
            source_refs=["conversation_pair:good"],
        )
    )
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="voice:example:bad",
            fact_type="voice_fact",
            entity_ref="seller_voice",
            value={
                "trait": "pushy",
                "guidance": "Pressure the customer.",
                "quality_label": "low",
            },
            source_refs=["conversation_pair:bad"],
            confidence=0.2,
        )
    )

    projection = await service.rebuild_voice_projection(
        VoiceProjectionRequest(workspace_id=workspace.id)
    )

    assert projection.state["traits"] == [
        {"trait": "warm", "guidance": "Use short friendly replies."}
    ]
    assert projection.state["excluded_fact_ids"] == ["voice:example:bad"]
    assert projection.source_refs == ["conversation_pair:good"]


async def test_pair_miner_is_rebuildable_and_learning_export_is_quality_gated(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
) -> None:
    service = _memory_service(db_session)
    first = await service.mine_conversation_pairs(
        ConversationPairMiningInput(
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            source_refs=["conversation:history:1"],
            turns=[
                {
                    "message_ref": "msg:customer:1",
                    "sender_type": "customer",
                    "content": "Yetkazib berish bormi?",
                    "created_at": "2026-05-05T09:00:00+00:00",
                },
                {
                    "message_ref": "msg:seller:2",
                    "sender_type": "seller",
                    "content": "Bor, tumaningizni ayting.",
                    "created_at": "2026-05-05T09:01:00+00:00",
                    "quality_label": "approved",
                    "outcome": "continued",
                },
                {
                    "message_ref": "msg:customer:3",
                    "sender_type": "customer",
                    "content": "Rahmat",
                    "created_at": "2026-05-05T09:02:00+00:00",
                },
                {
                    "message_ref": "msg:seller:4",
                    "sender_type": "seller",
                    "content": "Shoshiling.",
                    "created_at": "2026-05-05T09:03:00+00:00",
                    "quality_label": "low",
                    "outcome": "lost",
                },
            ],
            correlation_id="corr-pair-mine",
        )
    )
    second = await service.mine_conversation_pairs(
        ConversationPairMiningInput(
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            source_refs=["conversation:history:1"],
            turns=[
                {
                    "message_ref": "msg:customer:1",
                    "sender_type": "customer",
                    "content": "Yetkazib berish bormi?",
                    "created_at": "2026-05-05T09:00:00+00:00",
                },
                {
                    "message_ref": "msg:seller:2",
                    "sender_type": "seller",
                    "content": "Bor, tumaningizni ayting.",
                    "created_at": "2026-05-05T09:01:00+00:00",
                    "quality_label": "approved",
                    "outcome": "continued",
                },
            ],
            correlation_id="corr-pair-mine",
        )
    )

    export = await service.export_learning_lab(workspace_id=workspace.id)

    assert [item.fact.value["seller_turn"] for item in first.pairs] == [
        "Bor, tumaningizni ayting.",
        "Shoshiling.",
    ]
    assert second.pairs[0].fact_created is False
    assert [item["fact_id"] for item in export.training_candidates] == [
        f"conversation_pair:{conversation.id}:msg:customer:1:msg:seller:2"
    ]
    assert export.excluded_fact_ids == [
        f"conversation_pair:{conversation.id}:msg:customer:3:msg:seller:4"
    ]


async def test_mine_conversation_pairs_scopes_to_trigger_message(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
) -> None:
    """A seller turn must mine ONLY the pair it just completed, not re-mine every
    historical pair. Whole-conversation re-mining on every seller message was the
    O(n) write + re-embed storm that pinned DB connections (see the 2026-05-25
    concurrency/pool-collapse audit, F2)."""
    service = _memory_service(db_session)
    turns = [
        {
            "message_ref": "msg:customer:1",
            "sender_type": "customer",
            "content": "Yetkazib berish bormi?",
            "created_at": "2026-05-05T09:00:00+00:00",
        },
        {
            "message_ref": "msg:seller:2",
            "sender_type": "seller",
            "content": "Bor, tumaningizni ayting.",
            "created_at": "2026-05-05T09:01:00+00:00",
        },
        {
            "message_ref": "msg:customer:3",
            "sender_type": "customer",
            "content": "Toshkent",
            "created_at": "2026-05-05T09:02:00+00:00",
        },
        {
            "message_ref": "msg:seller:4",
            "sender_type": "seller",
            "content": "Ertaga yetkazamiz.",
            "created_at": "2026-05-05T09:03:00+00:00",
        },
    ]
    result = await service.mine_conversation_pairs(
        ConversationPairMiningInput(
            workspace_id=workspace.id,
            conversation_id=conversation.id,
            source_refs=["conversation:history:1"],
            turns=turns,
            correlation_id="corr-pair-scope",
            trigger_message_ref="msg:seller:4",
        )
    )

    # Only the pair completed by the trigger seller turn is written.
    assert [pair.fact.value["seller_turn"] for pair in result.pairs] == [
        "Ertaga yetkazamiz."
    ]
    assert [pair.fact.value["customer_turn"] for pair in result.pairs] == [
        "Toshkent"
    ]


async def test_correction_episode_and_contextual_retrieval_trace(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = _memory_service(db_session)
    episode = await service.write_correction_episode(
        CorrectionEpisodeInput(
            workspace_id=workspace.id,
            episode_ref="correction:1",
            situation={"customer_message": "Narxi qancha?"},
            candidate_output="Narx noma'lum.",
            human_feedback="Catalog narxini tekshirib yoz.",
            final_output="Narxi 120 000 so'm.",
            outcome="approved",
            quality_label="approved",
            source_refs=["draft:1", "owner_edit:1"],
            correlation_id="corr-correction",
        )
    )
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="knowledge:payment:proposed",
            fact_type="knowledge_fact",
            entity_ref="business:payment",
            value={"topic": "payment", "answer": "Card accepted."},
            source_refs=["owner_message:payment"],
            status="proposed",
            approval_state="proposed",
            source="ai_proposal",
        )
    )
    result = await service.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["correction_episode_fact", "knowledge_fact"],
            include_source_units=True,
        )
    )

    assert episode.fact.fact_type == "correction_episode_fact"
    assert result.candidates[0].fact_id == "correction:1"
    assert "knowledge:payment:proposed" in result.trace.rejected_fact_ids
    assert result.trace.selected_fact_ids == ["correction:1"]


async def test_contextual_source_units_agent_grounding_and_workspace_api(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_headers_b: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
    workspace_b: Workspace,
) -> None:
    service = _memory_service(db_session)
    for item in [
        ("knowledge:delivery", "knowledge_fact", "business:delivery", {"answer": "24 soat"}),
        ("rule:delivery", "seller_rule_fact", "business:delivery", {"instruction": "Ask district"}),
        ("voice:warm", "voice_fact", "seller_voice", {"trait": "warm"}),
        (
            "catalog_product:ring-phase4",
            "catalog_product",
            "catalog_product:ring-phase4",
            {"title": "Silver ring"},
        ),
    ]:
        await service.write_memory_fact(
            _fact_input(
                workspace=workspace,
                fact_id=item[0],
                fact_type=item[1],
                entity_ref=item[2],
                value=item[3],
                source_refs=[f"source:{item[0]}"],
            )
        )
    await _memory_service(db_session).write_memory_fact(
        _fact_input(
            workspace=workspace_b,
            fact_id="knowledge:other-workspace",
            fact_type="knowledge_fact",
            entity_ref="business:delivery",
            value={"answer": "Wrong workspace"},
            source_refs=["source:other"],
        )
    )

    units = await service.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=[
                "knowledge_fact",
                "seller_rule_fact",
                "voice_fact",
                "catalog_product",
            ],
            degraded_units={"source:rule:delivery": "embedding_unavailable"},
        )
    )
    bundle = await service.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=[
                "knowledge_fact",
                "seller_rule_fact",
                "voice_fact",
                "catalog_product",
                "conversation_pair_fact",
            ],
            include_source_units=True,
            requested_slots=["knowledge_fact", "conversation_pair_fact"],
        )
    )
    grounding = await service.build_agent_grounding(
        AgentGroundingRequest(
            workspace_id=workspace.id,
            agent_kind="seller_agent",
            requested_fact_types=[
                "knowledge_fact",
                "seller_rule_fact",
                "voice_fact",
                "catalog_product",
                "conversation_pair_fact",
            ],
            requested_slots=["knowledge_fact", "conversation_pair_fact"],
        )
    )
    own = await client.post(
        "/api/business-brain/memory/agent-grounding",
        headers=auth_headers,
        json={
            "agent_kind": "seller_agent",
            "requested_fact_types": ["knowledge_fact", "seller_rule_fact"],
            "requested_slots": ["knowledge_fact"],
        },
    )
    other = await client.post(
        "/api/business-brain/memory/agent-grounding",
        headers=auth_headers_b,
        json={
            "agent_kind": "seller_agent",
            "requested_fact_types": ["knowledge_fact", "seller_rule_fact"],
            "requested_slots": ["knowledge_fact"],
        },
    )

    assert any(unit.state == "degraded" for unit in units.source_units)
    assert bundle.trace.retrieval_channels == ["structured", "index"]
    assert bundle.degraded_reasons == ["embedding_unavailable"]
    assert bundle.missing_evidence == ["conversation_pair_fact"]
    assert grounding.families["knowledge_fact"][0]["fact_id"] == "knowledge:delivery"
    assert grounding.unavailable_families == ["conversation_pair_fact"]
    assert own.status_code == 200
    assert own.json()["families"]["knowledge_fact"][0]["fact_id"] == "knowledge:delivery"
    assert other.status_code == 200
    assert other.json()["families"]["knowledge_fact"][0]["fact_id"] == (
        "knowledge:other-workspace"
    )


async def test_business_brain_memory_retrieve_api_passes_query_rewrite_flag(
    monkeypatch,
    client: AsyncClient,
    auth_headers: dict[str, str],
    workspace: Workspace,
) -> None:
    captured: dict[str, ContextualRetrievalRequest] = {}

    async def fake_retrieve_contextual(
        self: RetrievalCoreService,
        request: ContextualRetrievalRequest,
    ) -> ContextualRetrievalResult:
        captured["request"] = request
        return ContextualRetrievalResult(
            workspace_id=request.workspace_id,
            candidates=[],
            trace=ContextualRetrievalTrace(
                query_text=request.query_text,
                query_rewrites=["lightning digital av adapter"],
                llm_trace_ids=["llm-trace-test"],
            ),
        )

    monkeypatch.setattr(
        RetrievalCoreService,
        "retrieve_contextual",
        fake_retrieve_contextual,
    )

    response = await client.post(
        "/api/business-brain/memory/retrieve",
        headers=auth_headers,
        json={
            "requested_fact_types": ["catalog_product"],
            "query_text": "hdmi perehodnik",
            "enable_query_rewrite": True,
            "include_proposed": True,
        },
    )

    assert response.status_code == 200
    assert captured["request"].workspace_id == workspace.id
    assert captured["request"].enable_query_rewrite is True
    assert captured["request"].include_proposed is True
    assert response.json()["trace"]["query_rewrites"] == [
        "lightning digital av adapter"
    ]


async def test_contextual_retrieval_uses_query_text_and_source_units(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = _memory_service(db_session)
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="knowledge:payment-card",
            fact_type="knowledge_fact",
            entity_ref="business:payment",
            value={
                "topic": "payment",
                "answer": "Karta orqali to'lov qabul qilamiz.",
            },
            source_refs=["owner:payment-card"],
        )
    )
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="knowledge:delivery-city",
            fact_type="knowledge_fact",
            entity_ref="business:delivery",
            value={
                "topic": "delivery",
                "answer": "Toshkent bo'ylab yetkazamiz.",
            },
            source_refs=["owner:delivery-city"],
        )
    )
    await service.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["knowledge_fact"],
        )
    )

    result = await service.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact"],
            query_text="karta orqali to'lov",
            include_source_units=True,
            limit=5,
        )
    )

    assert [candidate.fact_id for candidate in result.candidates] == [
        "knowledge:payment-card"
    ]
    candidate = result.candidates[0]
    assert "Karta orqali" in candidate.contextual_text
    assert candidate.retrieval_scores["lexical"] > 0
    assert candidate.source_units[0].source_text is not None
    assert result.trace.query_text == "karta orqali to'lov"
    assert "lexical" in result.trace.retrieval_channels
    assert "knowledge:delivery-city" in result.trace.rejected_fact_ids


async def test_contextual_source_units_can_use_llm_chunk_context_for_recall(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    calls: list[Any] = []

    async def provider(request) -> LLMProviderResponse:
        calls.append(request)
        assert request.prompt_id == "business_brain.source_unit_contextualization"
        prompt = request.input_payload["prompt"]
        assert prompt["prompt_id"] == "business_brain.source_unit_contextualization"
        assert prompt["registry_state"] == "loaded"
        assert "Return only JSON matching `SourceUnitContextualizationOutput`" in prompt["body"]
        assert request.source_refs == [
            "source:adapter-pdf",
            "fact:catalog:lightning-digital-av-adapter",
        ]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "source_unit_contextualization_output.v1",
                    "context": (
                        "Apple Lightning Digital AV Adapter from a PDF catalog; "
                        "customers may call it HDMI perehodnik for TV display."
                    ),
                }
            ),
            model_used="test-contextualizer",
        )

    service = _memory_service(db_session, provider=provider)
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:lightning-digital-av-adapter",
            fact_type="catalog_product",
            entity_ref="catalog:lightning-digital-av-adapter",
            value={
                "title": "Apple Lightning Digital AV Adapter",
                "description": "TV display adapter.",
            },
            source_refs=["source:adapter-pdf"],
        )
    )

    units = await service.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["catalog_product"],
            contextualize_source_units=True,
        )
    )
    result = await service.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="hdmi perehodnik",
            include_source_units=True,
        )
    )

    assert len(calls) == 1
    assert len(units.llm_trace_ids) == 1
    assert units.degraded_reasons == []
    assert units.source_units[0].source_text is not None
    assert units.source_units[0].source_text.startswith(
        "LLM contextualized source unit"
    )
    assert "HDMI perehodnik" in units.source_units[0].source_text
    assert "Original contextual source unit" in units.source_units[0].source_text
    assert "TV display adapter." in units.source_units[0].source_text
    assert [candidate.fact_id for candidate in result.candidates] == [
        "catalog:lightning-digital-av-adapter"
    ]
    assert "HDMI perehodnik" in result.candidates[0].source_units[0].source_text


async def test_contextual_source_unit_llm_failure_keeps_deterministic_embedding(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    class FakeEmbeddingService:
        batch_calls: ClassVar[list[list[str]]] = []

        async def embed_texts_batch(self, texts: list[str]) -> list[list[float]]:
            self.batch_calls.append(list(texts))
            return [[0.5, *([0.0] * 3071)] for _text in texts]

    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService",
        FakeEmbeddingService,
    )

    async def provider(request) -> LLMProviderResponse:  # noqa: ARG001
        raise TimeoutError()

    service = _memory_service(db_session, provider=provider)
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="knowledge:delivery-timeout",
            fact_type="knowledge_fact",
            entity_ref="business:delivery",
            value={"topic": "delivery", "answer": "Toshkent ichida 24 soat."},
            source_refs=["source:delivery-timeout"],
        )
    )

    units = await service.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["knowledge_fact"],
            contextualize_source_units=True,
            embed_source_units=True,
        )
    )

    assert len(units.llm_trace_ids) == 1
    assert units.degraded_reasons == ["contextualization:timeout"]
    assert len(FakeEmbeddingService.batch_calls) == 1
    assert FakeEmbeddingService.batch_calls[0][0].startswith("Contextual source unit")
    assert "LLM contextualized source unit" not in FakeEmbeddingService.batch_calls[0][0]
    assert units.source_units[0].embedding_state == "ready"
    assert units.source_units[0].source_text is not None
    assert units.source_units[0].source_text.startswith("Contextual source unit")


async def test_contextual_source_unit_rebuild_batches_embeddings(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    class FakeEmbeddingService:
        batch_calls: ClassVar[list[list[str]]] = []
        single_calls: ClassVar[list[str]] = []

        async def embed_text(self, text: str) -> list[float]:
            self.single_calls.append(text)
            raise AssertionError("source unit rebuild must use batch embeddings")

        async def embed_texts_batch(self, texts: list[str]) -> list[list[float]]:
            self.batch_calls.append(list(texts))
            return [[float(index + 1), *([0.0] * 3071)] for index, _ in enumerate(texts)]

    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService",
        FakeEmbeddingService,
    )

    service = _memory_service(db_session)
    for item in [
        ("knowledge:payment", "knowledge_fact", {"answer": "Karta orqali to'lov."}),
        ("knowledge:delivery", "knowledge_fact", {"answer": "Yetkazib beramiz."}),
        ("catalog:ring", "catalog_product", {"title": "Kumush uzuk"}),
    ]:
        await service.write_memory_fact(
            _fact_input(
                workspace=workspace,
                fact_id=item[0],
                fact_type=item[1],
                entity_ref=item[0],
                value=item[2],
                source_refs=[f"source:{item[0]}"],
            )
        )

    units = await service.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["knowledge_fact", "catalog_product"],
            embed_source_units=True,
        )
    )

    assert len(FakeEmbeddingService.batch_calls) == 1
    assert len(FakeEmbeddingService.batch_calls[0]) == 3
    assert all(
        text.startswith("Contextual source unit")
        for text in FakeEmbeddingService.batch_calls[0]
    )
    assert any(
        "Fact type: knowledge_fact" in text
        for text in FakeEmbeddingService.batch_calls[0]
    )
    assert all("Evidence text:" in text for text in FakeEmbeddingService.batch_calls[0])
    assert FakeEmbeddingService.single_calls == []
    assert [unit.embedding_state for unit in units.source_units] == [
        "ready",
        "ready",
        "ready",
    ]
    assert [unit.state for unit in units.source_units] == ["ready", "ready", "ready"]
    assert [unit.embedding[0] for unit in units.source_units if unit.embedding] == [
        1.0,
        2.0,
        3.0,
    ]


async def test_contextual_source_unit_rebuild_falls_back_to_single_embeddings(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    class FakeEmbeddingService:
        batch_calls: ClassVar[list[list[str]]] = []
        single_calls: ClassVar[list[str]] = []

        async def embed_text(self, text: str) -> list[float]:
            self.single_calls.append(text)
            return [float(len(self.single_calls)), *([0.0] * 3071)]

        async def embed_texts_batch(self, texts: list[str]) -> list[list[float]]:
            self.batch_calls.append(list(texts))
            raise TimeoutError("batch embedding timeout")

    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService",
        FakeEmbeddingService,
    )

    service = _memory_service(db_session)
    for item in [
        ("knowledge:payment", "knowledge_fact", {"answer": "Karta orqali to'lov."}),
        ("catalog:ring", "catalog_product", {"title": "Kumush uzuk"}),
    ]:
        await service.write_memory_fact(
            _fact_input(
                workspace=workspace,
                fact_id=item[0],
                fact_type=item[1],
                entity_ref=item[0],
                value=item[2],
                source_refs=[f"source:{item[0]}"],
            )
        )

    units = await service.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["knowledge_fact", "catalog_product"],
            embed_source_units=True,
        )
    )

    assert len(FakeEmbeddingService.batch_calls) == 1
    assert len(FakeEmbeddingService.batch_calls[0]) == 2
    assert len(FakeEmbeddingService.single_calls) == 2
    assert [unit.embedding_state for unit in units.source_units] == ["ready", "ready"]
    assert units.degraded_reasons == []


async def test_contextual_source_unit_rebuild_can_target_candidate_fact_ids(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    service = _memory_service(db_session)
    for item in [
        ("knowledge:payment", "knowledge_fact", {"answer": "Karta orqali to'lov."}),
        ("knowledge:delivery", "knowledge_fact", {"answer": "Yetkazib beramiz."}),
    ]:
        await service.write_memory_fact(
            _fact_input(
                workspace=workspace,
                fact_id=item[0],
                fact_type=item[1],
                entity_ref=item[0],
                value=item[2],
                source_refs=[f"source:{item[0]}"],
            )
        )

    units = await service.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["knowledge_fact"],
            candidate_fact_ids=["knowledge:delivery"],
        )
    )

    assert [unit.fact_id for unit in units.source_units] == ["knowledge:delivery"]


async def test_contextual_retrieval_can_use_vector_source_units(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    service = BusinessBrainMemoryService(repository=repository)
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:ring",
            fact_type="catalog_product",
            entity_ref="catalog:ring",
            value={"name": "Kumush uzuk", "price": "250000"},
            source_refs=["catalog:ring:photo"],
        )
    )
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="catalog:shoes",
            fact_type="catalog_product",
            entity_ref="catalog:shoes",
            value={"name": "Oyoq kiyim", "price": "400000"},
            source_refs=["catalog:shoes:photo"],
        )
    )
    await repository.persist_index_record(
        BusinessBrainIndexRecordContract(
            index_id="index:catalog:ring",
            workspace_id=workspace.id,
            fact_id="catalog:ring",
            unit_ref="catalog:ring:photo",
            state="ready",
            embedding_ref="gemini-embedding-2:test-ring",
            embedding_model="gemini-embedding-2",
            embedding_state="ready",
            embedding=[1.0, *([0.0] * 3071)],
            source_text="Kumush uzuk rasmi va katalog narxi.",
            source_refs=["catalog:ring:photo"],
            idempotency_key="index:catalog:ring",
        )
    )
    await repository.persist_index_record(
        BusinessBrainIndexRecordContract(
            index_id="index:catalog:shoes",
            workspace_id=workspace.id,
            fact_id="catalog:shoes",
            unit_ref="catalog:shoes:photo",
            state="ready",
            embedding_ref="gemini-embedding-2:test-shoes",
            embedding_model="gemini-embedding-2",
            embedding_state="ready",
            embedding=[0.0, 1.0, *([0.0] * 3070)],
            source_text="Oyoq kiyim katalog rasmi.",
            source_refs=["catalog:shoes:photo"],
            idempotency_key="index:catalog:shoes",
        )
    )

    result = await service.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["catalog_product"],
            query_text="mijoz rasm yubordi",
            query_embedding=[1.0, *([0.0] * 3071)],
            minimum_lexical_score=1.0,
            enable_semantic=True,
            include_source_units=True,
            limit=3,
        )
    )

    assert [candidate.fact_id for candidate in result.candidates] == ["catalog:ring"]
    assert result.candidates[0].retrieval_scores["semantic"] == 1.0
    assert "semantic" in result.trace.retrieval_channels
    assert result.candidates[0].source_units[0].embedding_model == "gemini-embedding-2"


def test_phase4_memory_guardrails_do_not_add_shortcut_semantics() -> None:
    root = Path(__file__).resolve().parents[1] / "app/modules/business_brain"
    banned_tokens = (
        "genai.Client(",
        ".models.generate_content(",
        "client.aio.models.generate_content(",
        "re.compile(",
        "re.search(",
        "keyword",
        "heuristic",
        "filename",
        "raw correction text",
    )
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name not in {"memory.py", "memory_contracts.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in banned_tokens):
            offenders.append(str(path.relative_to(root)))

    inventory = (
        Path(__file__).resolve().parents[2]
        / "docs/architecture/2026-05-04-legacy-deletion-inventory.md"
    ).read_text(encoding="utf-8")
    assert offenders == []
    assert "Phase 4 landed" in inventory
    assert "old `/api/knowledge` CRUD/model/table path is deleted" in inventory
