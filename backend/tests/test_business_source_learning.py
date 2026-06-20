from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.workspace import Workspace
from app.modules.business_brain.contracts import BusinessBrainIndexRecordContract
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import (
    ContextualRetrievalRequest,
    SourceUnitRebuildRequest,
)
from app.modules.business_brain.source_learning import (
    BusinessSourceLearningRequest,
    BusinessSourceLearningService,
)
from app.modules.business_brain.source_learning import BusinessSourceLearningOutput
from app.modules.channel_runtime.source import (
    ChannelRuntimeCore,
    ChannelSourceSubscription,
)
from app.modules.channel_runtime.source_queue import ChannelSourceLearningQueueService
from app.modules.commerce_catalog.service import CommerceCatalogCoreService
from app.modules.commercial_spine.llm_gateway import LLMGateway, LLMProviderResponse
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.source_ingestion import (
    OnboardingSourceIngestionRequest,
    OnboardingSourceIngestionService,
    SourceFetchResult,
)
from app.modules.onboarding_learning.review_actions import (
    OnboardingLearnedReviewActionRequest,
    OnboardingLearnedReviewActionService,
)
from app.services.channel_sync_models import ChannelMessageRecord


class _EmbeddingStub:
    async def embed_query(self, _text: str) -> list[float]:
        return [0.01] * 3072


def _image_only_pdf_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), (80, 35, 145)).save(buffer, format="PDF")
    return buffer.getvalue()


def _text_pdf_bytes(text: str) -> bytes:
    escaped = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("ascii", "ignore")
    )
    stream = b"BT /F1 12 Tf 72 720 Td (" + escaped + b") Tj ET"
    return b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >> endobj
4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
5 0 obj << /Length """ + str(len(stream)).encode() + b""" >> stream
""" + stream + b"""
endstream endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000241 00000 n
0000000311 00000 n
trailer << /Root 1 0 R /Size 6 >>
startxref
""" + str(311 + len(stream) + 32).encode() + b"""
%%EOF
"""


def test_source_learning_memory_value_schema_guides_structured_outputs() -> None:
    schema = BusinessSourceLearningOutput.model_json_schema()
    memory_candidate = schema["$defs"]["BusinessSourceMemoryCandidate"]
    value_ref = memory_candidate["properties"]["value"]["$ref"].split("/")[-1]
    value_schema = schema["$defs"][value_ref]
    fact_type_schema = memory_candidate["properties"]["fact_type"]

    assert {"summary", "answer", "details"}.issubset(value_schema["properties"])
    assert value_schema["additionalProperties"] is True
    assert "conversation_pair_fact" in fact_type_schema["enum"]


def test_source_learning_catalog_value_schema_guides_structured_outputs() -> None:
    schema = BusinessSourceLearningOutput.model_json_schema()
    catalog_candidate = schema["$defs"]["BusinessSourceCatalogCandidate"]
    product_ref = catalog_candidate["properties"]["product"]["$ref"].split("/")[-1]
    variant_ref = (
        catalog_candidate["properties"]["variants"]["items"]["$ref"].split("/")[-1]
    )
    offer_ref = catalog_candidate["properties"]["offers"]["items"]["$ref"].split("/")[-1]
    media_ref = catalog_candidate["properties"]["media"]["items"]["$ref"].split("/")[-1]
    source_ref = (
        catalog_candidate["properties"]["source_fact"]["$ref"].split("/")[-1]
    )

    assert {"title", "identity_ref", "category", "details"}.issubset(
        schema["$defs"][product_ref]["properties"]
    )
    assert {"variant_ref", "product_ref", "attributes"}.issubset(
        schema["$defs"][variant_ref]["properties"]
    )
    assert {"offer_ref", "product_ref", "price", "stock"}.issubset(
        schema["$defs"][offer_ref]["properties"]
    )
    assert {"media_ref", "source_media_ref", "media_type", "quality_state"}.issubset(
        schema["$defs"][media_ref]["properties"]
    )
    assert {"source_ref", "source_type", "content_refs"}.issubset(
        schema["$defs"][source_ref]["properties"]
    )


async def test_universal_source_learning_extracts_catalog_kb_and_media_from_web_and_channel(
    monkeypatch,
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    async def fetch_source(url: str) -> SourceFetchResult:
        assert url == "https://nafis.example/catalog"
        return SourceFetchResult(
            content=(
                b"<html><head><title>Nafis Catalog</title></head><body>"
                b"<h1>Binafsha charm sumka</h1>"
                b"<img src='/sumka.jpg' alt='Binafsha charm sumka rasmi'>"
                b"<p>Narxi 180000 UZS. Toshkent ichida yetkazib berish bor.</p>"
                b"</body></html>"
            ),
            content_type="text/html",
            final_url=url,
        )

    seller_agent_calls: list[str] = []

    async def provider(request) -> LLMProviderResponse:
        if request.prompt_id == "retrieval_core.agentic_search_plan":
            return LLMProviderResponse(
                text=json.dumps(
                    {
                        "schema_version": "retrieval_agentic_search_output.v1",
                        "queries": ["binafsha charm sumka narx yetkazib berish rasm"],
                        "fact_types": [
                            "catalog_product",
                            "knowledge_fact",
                            "media_evidence_fact",
                        ],
                        "query_modalities": ["image"],
                    }
                ),
                model_used="test-model",
            )
        if request.prompt_id == "retrieval_core.query_rewrite":
            return LLMProviderResponse(
                text=json.dumps(
                    {
                        "schema_version": "retrieval_query_rewrite_output.v1",
                        "rewrites": ["binafsha charm sumka narx yetkazib berish rasm"],
                    }
                ),
                model_used="test-model",
            )
        if request.prompt_id == "seller_agent.plan_and_compose":
            seller_agent_calls.append(request.prompt_id)
            grounding = request.input_payload["grounding"]
            families = grounding["families"]
            if request.correlation_id == "corr-source-learning-pre-review":
                assert "catalog_product" not in families
                assert "knowledge_fact" not in families
                assert families.get("business_source_media_fact")
                return LLMProviderResponse(
                    text=json.dumps(
                        {
                            "planner": {
                                "should_reply": False,
                                "sales_move": "wait_for_business_brain_review",
                                "customer_owned_missing_info": [],
                                "business_owned_missing_info": ["review_learned_catalog"],
                                "action_suggestions": [],
                                "risk_level": "medium",
                                "confidence": 0.7,
                                "source_refs": ["message:source-learning:pre-review:customer"],
                            },
                            "composer": {
                                "draft_text": "O'rganilgan ma'lumotlarni avval tasdiqlash kerak.",
                                "confidence": 0.7,
                                "used_source_refs": ["message:source-learning:pre-review:customer"],
                                "voice_notes": ["review_required"],
                            },
                        }
                    ),
                    model_used="test-model",
                )
            assert families["catalog_product"][0]["fact_id"] == "catalog_product:binafsha-sumka"
            assert families["catalog_product"][0]["value"]["title"] == "Binafsha charm sumka"
            assert families["catalog_offer"][0]["value"]["price"] == {
                "amount": 180000,
                "currency": "UZS",
            }
            assert families["catalog_media"][0]["value"]["approved"] is True
            assert families["knowledge_fact"][0]["fact_id"] == "knowledge:delivery:web"
            assert "fact:catalog_product:binafsha-sumka" in grounding["source_refs"]
            return LLMProviderResponse(
                text=json.dumps(
                    {
                        "planner": {
                            "should_reply": True,
                            "sales_move": "answer_price_delivery_and_offer_image",
                            "customer_owned_missing_info": [],
                            "business_owned_missing_info": [],
                            "action_suggestions": [
                                {
                                    "action_type": "send_catalog_media",
                                    "label": "Mahsulot rasmini yuborish",
                                    "payload": {"product_ref": "catalog_product:binafsha-sumka"},
                                    "risk_level": "low",
                                    "requires_approval": False,
                                    "source_refs": ["fact:catalog_product:binafsha-sumka"],
                                }
                            ],
                            "risk_level": "low",
                            "confidence": 0.91,
                            "source_refs": [
                                "fact:catalog_product:binafsha-sumka",
                                "fact:catalog_offer:catalog_product:binafsha-sumka:catalog_offer:binafsha-sumka:main",
                                "fact:knowledge:delivery:web",
                            ],
                        },
                        "composer": {
                            "draft_text": (
                                "Binafsha charm sumka bor, narxi 180 000 so'm. "
                                "Toshkent ichida yetkazib beramiz. Rasmini ham yuboraman."
                            ),
                            "confidence": 0.9,
                            "used_source_refs": [
                                "fact:catalog_product:binafsha-sumka",
                                "fact:catalog_offer:catalog_product:binafsha-sumka:catalog_offer:binafsha-sumka:main",
                                "fact:knowledge:delivery:web",
                            ],
                            "voice_notes": ["neutral_fallback_voice"],
                        },
                    }
                ),
                model_used="test-model",
            )
        if request.prompt_id == "seller_agent.verifier":
            seller_agent_calls.append(request.prompt_id)
            composer = request.input_payload["composer"]
            assert "180 000" in composer["draft_text"]
            return LLMProviderResponse(
                text=json.dumps(
                    {
                        "status": "approved",
                        "safe_reply": composer["draft_text"],
                        "blocked_claims": [],
                        "review_reason": None,
                        "confidence": 0.93,
                        "source_refs": [
                            "fact:catalog_product:binafsha-sumka",
                            "fact:catalog_offer:catalog_product:binafsha-sumka:catalog_offer:binafsha-sumka:main",
                            "fact:knowledge:delivery:web",
                        ],
                    }
                ),
                model_used="test-model",
            )

        source_ref = request.input_payload["source_ref"]
        allowed = set(request.input_payload["allowed_evidence_refs"])
        extraction_request = request.input_payload["extraction_request"]
        extraction_refs = {part["ref"] for part in extraction_request["parts"]}
        assert extraction_request["schema_version"] == "universal_extraction_request.v1"
        assert extraction_refs == allowed
        assert {
            "commerce_generic.v1",
            "generic_kb.v1",
            "seller_voice.v1",
        }.issubset(set(extraction_request["profile_refs"]))
        source_units = request.input_payload["source_units"]
        media_assets = request.input_payload["media_assets"]
        if source_ref == "onboarding:web:catalog":
            unit_ref = source_units[0]["unit_ref"]
            media_ref = media_assets[0]["media_ref"]
            assert {unit_ref, media_ref}.issubset(allowed)
            payload: dict[str, Any] = {
                "schema_version": "business_source_learning_output.v1",
                "catalog_candidates": [
                    {
                        "product_ref": "catalog_product:binafsha-sumka",
                        "product": {
                            "title": "Binafsha charm sumka",
                            "category": "sumka",
                            "identity_ref": "catalog_product:binafsha-sumka",
                        },
                        "variants": [
                            {
                                "variant_ref": "catalog_variant:binafsha-sumka:main",
                                "product_ref": "catalog_product:binafsha-sumka",
                                "attributes": {"rang": "binafsha", "material": "charm"},
                            }
                        ],
                        "offers": [
                            {
                                "offer_ref": "catalog_offer:binafsha-sumka:main",
                                "product_ref": "catalog_product:binafsha-sumka",
                                "variant_ref": "catalog_variant:binafsha-sumka:main",
                                "price": {"amount": 180000, "currency": "UZS"},
                                "stock": {"state": "unknown"},
                                "active": True,
                            }
                        ],
                        "media": [
                            {
                                "media_ref": "catalog_media:binafsha-sumka:source",
                                "product_ref": "catalog_product:binafsha-sumka",
                                "variant_ref": "catalog_variant:binafsha-sumka:main",
                                "source_media_ref": media_ref,
                                "media_type": "image",
                                "url": "https://nafis.example/sumka.jpg",
                                "approved": False,
                            }
                        ],
                        "source_fact": {
                            "source_ref": source_ref,
                            "source_type": "website",
                            "content_refs": [unit_ref, media_ref],
                        },
                        "confidence": 0.82,
                        "risk_tier": "medium",
                        "evidence_refs": [unit_ref, media_ref],
                    }
                ],
                "memory_candidates": [
                    {
                        "fact_id": "knowledge:delivery:web",
                        "fact_type": "knowledge_fact",
                        "entity_ref": "business:delivery",
                        "value": {
                            "topic": "delivery",
                            "answer": "Toshkent ichida yetkazib berish bor.",
                        },
                        "confidence": 0.76,
                        "risk_tier": "medium",
                        "evidence_refs": [unit_ref],
                    }
                ],
            }
            return LLMProviderResponse(text=json.dumps(payload), model_used="test-model")

        unit_ref = source_units[0]["unit_ref"]
        media_ref = media_assets[0]["media_ref"]
        assert {unit_ref, media_ref}.issubset(allowed)
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "product_ref": "catalog_product:yashil-atlas",
                            "product": {
                                "title": "Yashil atlas ko'ylak",
                                "category": "ko'ylak",
                                "identity_ref": "catalog_product:yashil-atlas",
                            },
                            "variants": [],
                            "offers": [
                                {
                                    "offer_ref": "catalog_offer:yashil-atlas:main",
                                    "product_ref": "catalog_product:yashil-atlas",
                                    "price": {"amount": 250000, "currency": "UZS"},
                                    "stock": {"state": "unknown"},
                                    "active": True,
                                }
                            ],
                            "media": [
                                {
                                    "media_ref": "catalog_media:yashil-atlas:channel",
                                    "product_ref": "catalog_product:yashil-atlas",
                                    "source_media_ref": media_ref,
                                    "media_type": "image",
                                    "approved": False,
                                }
                            ],
                            "source_fact": {
                                "source_ref": source_ref,
                                "source_type": "telegram_channel",
                                "content_refs": [unit_ref, media_ref],
                            },
                            "confidence": 0.78,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref, media_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    async def fake_rerank(
        _query: str,
        candidates: list[dict[str, Any]],
        *,
        text_field: str,
        top_n: int,
    ) -> list[dict[str, Any]]:
        assert text_field == "text"
        return [
            {**candidate, "relevance_score": 0.95 - (index * 0.01)}
            for index, candidate in enumerate(candidates[:top_n])
        ]

    monkeypatch.setattr(
        "app.modules.retrieval_core.service.reranker.rerank",
        fake_rerank,
    )

    repository = CommercialSpineRepository(db_session)
    ingestion = OnboardingSourceIngestionService(
        repository=repository,
        fetch_source=fetch_source,
    )
    website = await ingestion.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:web:catalog",
            source_kind="website",
            source_payload={"url": "https://nafis.example/catalog"},
            correlation_id="corr-web-ingest",
            idempotency_key="web-ingest",
        )
    )
    channel = await ingestion.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="telegram_channel:@nafis",
            source_kind="telegram_channel",
            source_payload={
                "channel_id": "@nafis",
                "messages": [
                    {
                        "message_id": "101",
                        "caption": "Yashil atlas ko'ylak yangi keldi.",
                        "media_type": "photo",
                        "media_metadata": {
                            "mime_type": "image/jpeg",
                            "url": "https://cdn.example/atlas.jpg",
                        },
                    }
                ],
            },
            correlation_id="corr-channel-ingest",
            idempotency_key="channel-ingest",
        )
    )
    learner = BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    web_learning = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:web:catalog",
            source_kind="website",
            source_fact_id=website.source_fact_id,
            correlation_id="corr-web-learn",
            idempotency_key="web-learn",
        )
    )
    channel_learning = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="telegram_channel:@nafis",
            source_kind="telegram_channel",
            source_fact_id=channel.source_fact_id,
            correlation_id="corr-channel-learn",
            idempotency_key="channel-learn",
        )
    )

    product = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:binafsha-sumka",
    )
    media = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_media:catalog_product:binafsha-sumka:catalog_media:binafsha-sumka:source",
    )
    knowledge = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:delivery:web",
    )
    channel_product = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:yashil-atlas",
    )

    assert web_learning.gateway_status == "ok"
    assert web_learning.catalog_candidate_count == 1
    assert web_learning.memory_candidate_count == 1
    assert web_learning.rejected_candidates == []
    assert web_learning.extraction_run_id == "extraction:web-learn:universal-extraction"
    assert web_learning.extraction_candidate_count == 2
    assert len(web_learning.extraction_proposal_refs) == 2
    assert product is not None
    assert product.status == "proposed"
    assert set(product.source_refs) == {
        "onboarding:web:catalog",
        website.source_units[0].unit_ref,
        website.media_assets[0].media_ref,
    }
    assert media is not None
    assert media.value["source_media_ref"] == website.media_assets[0].media_ref
    assert knowledge is not None
    assert knowledge.status == "proposed"
    assert knowledge.source_refs == [
        website.source_units[0].unit_ref,
        "onboarding:web:catalog",
    ]
    assert channel_learning.catalog_candidate_count == 1
    assert channel_learning.extraction_candidate_count == 1
    assert len(channel_learning.extraction_proposal_refs) == 1
    assert channel_product is not None
    assert channel_product.source_refs == [
        channel.source_units[0].unit_ref,
        channel.media_assets[0].media_ref,
        "telegram_channel:@nafis",
    ]
    extraction_candidates = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="extraction_candidate",
    )
    assert {
        item.state["candidate"]["candidate_id"]
        for item in extraction_candidates
        if item.state["candidate_state"] == "accepted"
    } >= {
        "business_source_catalog:catalog_product:binafsha-sumka",
        "business_source_memory:knowledge:delivery:web",
        "business_source_catalog:catalog_product:yashil-atlas",
    }
    assert {
        item.state["lifecycle_state"] for item in extraction_candidates
    } == {"proposed"}

    memory = BusinessBrainMemoryService(repository=repository)
    catalog = CommerceCatalogCoreService(db_session)
    hidden_web_price = await catalog.search_authority(
        workspace_id=workspace.id,
        query="binafsha sumka narxi qancha?",
        include_media=True,
    )
    hidden_channel_price = await catalog.search_authority(
        workspace_id=workspace.id,
        query="yashil atlas ko'ylak narxi qancha?",
        include_media=True,
    )
    await memory.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["knowledge_fact"],
        )
    )

    hidden_faq = await memory.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact"],
            query_text="Toshkent ichida yetkazib berish bormi?",
            include_source_units=True,
        )
    )
    reviewable_faq = await memory.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact"],
            query_text="Toshkent ichida yetkazib berish bormi?",
            include_proposed=True,
            include_source_units=True,
        )
    )
    assert hidden_faq.candidates == []
    assert "knowledge:delivery:web" in hidden_faq.trace.rejected_fact_ids
    assert hidden_web_price.products == []
    assert hidden_channel_price.products == []
    assert reviewable_faq.candidates[0].fact_id == "knowledge:delivery:web"
    assert reviewable_faq.candidates[0].status == "proposed"
    assert reviewable_faq.candidates[0].source_units[0].source_refs == [
        website.source_units[0].unit_ref
    ]
    review = OnboardingLearnedReviewActionService(repository=repository)
    await review.apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action="approve",
            target_type="product",
            target_ref="catalog_product:binafsha-sumka",
            correlation_id="corr-web-learn-approve-typed",
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    await review.apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action="approve",
            target_type="product",
            target_ref="catalog_product:yashil-atlas",
            correlation_id="corr-channel-learn-approve-typed",
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    web_price = await catalog.search_authority(
        workspace_id=workspace.id,
        query="binafsha sumka narxi qancha?",
        include_media=True,
    )
    channel_price = await catalog.search_authority(
        workspace_id=workspace.id,
        query="yashil atlas ko'ylak narxi qancha?",
        include_media=True,
    )

    assert web_price.products[0].product_ref == "catalog_product:binafsha-sumka"
    assert web_price.offers[0].price == "180000"
    assert web_price.offers[0].currency == "UZS"
    assert web_price.media[0].media_ref == "catalog_media:binafsha-sumka:source"
    assert channel_price.products[0].product_ref == "catalog_product:yashil-atlas"
    assert channel_price.offers[0].price == "250000"
    assert channel_price.offers[0].currency == "UZS"


async def test_source_learning_normalizes_llm_catalog_refs_for_retrieval(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:text:owner-products",
            source_kind="text",
            source_payload={
                "text": (
                    "Zarina Silk Scarf narxi 145000 so'm, ipak sharf. "
                    "Toshkent bo'ylab yetkazish 1 kunda."
                )
            },
            correlation_id="corr-unnamespaced-ingest",
            idempotency_key="unnamespaced-ingest",
        )
    )

    async def provider(request) -> LLMProviderResponse:
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "product_ref": "zarina-silk-scarf",
                            "product": {
                                "title": "Zarina Silk Scarf",
                                "identity_ref": "zarina-silk-scarf",
                                "category": "accessory",
                            },
                            "variants": [],
                            "offers": [
                                {
                                    "product_ref": "zarina-silk-scarf",
                                    "price": {"amount": 145000, "currency": "UZS"},
                                    "stock": {"state": "unknown"},
                                    "active": True,
                                }
                            ],
                            "media": [],
                            "source_fact": {
                                "source_ref": "onboarding:text:owner-products",
                                "source_type": "text",
                                "content_refs": [unit_ref],
                            },
                            "confidence": 0.84,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    result = await BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:text:owner-products",
            source_kind="text",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-unnamespaced-learn",
            idempotency_key="unnamespaced-learn",
        )
    )
    products = await repository.list_facts(
        workspace_id=workspace.id,
        fact_type="catalog_product",
        statuses=("proposed",),
    )
    product = next(
        fact for fact in products if fact.value.get("title") == "Zarina Silk Scarf"
    )
    offer = next(
        fact
        for fact in await repository.list_facts(
            workspace_id=workspace.id,
            fact_type="catalog_offer",
            statuses=("proposed",),
        )
        if fact.entity_ref == product.fact_id
    )

    assert result.catalog_candidate_count == 1
    assert result.rejected_candidates == []
    assert product.fact_id.startswith("catalog_product:source:zarina-silk-scarf:")
    assert product.value["identity_ref"] == product.fact_id
    assert offer.value["product_ref"] == product.fact_id
    assert offer.value["offer_ref"] == "offer:1"

    catalog = CommerceCatalogCoreService(db_session)
    before = await catalog.search_authority(
        workspace_id=workspace.id,
        query="Zarina Silk Scarf narxi",
    )
    assert before.products == []

    await OnboardingLearnedReviewActionService(repository=repository).apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action="approve",
            target_type="product",
            target_ref=product.fact_id,
            correlation_id="corr-unnamespaced-approve-typed",
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    retrieval = await catalog.search_authority(
        workspace_id=workspace.id,
        query="Zarina Silk Scarf narxi",
    )

    assert retrieval.products
    assert retrieval.products[0].product_ref == product.fact_id
    assert retrieval.products[0].name == "Zarina Silk Scarf"
    assert retrieval.offers[0].price == "145000"


async def test_edited_channel_source_creates_catalog_update_review_candidate(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    subscription = ChannelSourceSubscription(
        subscription_id="source-sub:telegram:@catalog",
        workspace_id=workspace.id,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref="@catalog",
        source_scope="catalog",
        sync_policy={"mode": "background", "max_posts": 50},
        freshness_state="fresh",
        last_cursor="41",
        status="active",
    )
    edited_at = datetime(2026, 6, 5, 12, 5, tzinfo=UTC)
    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=[
            ChannelMessageRecord(
                external_message_id="41",
                sender_external_id="@catalog",
                text="Atlas sumka yangi narxi 199000 UZS",
                sent_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
                edited_at=edited_at,
                edit_version="2",
                supersedes_external_message_id="41",
                is_outgoing=False,
            )
        ],
    )
    queued = await ChannelSourceLearningQueueService(repository).queue_ingestion_plan(
        plan=plan,
        correlation_id="corr:atlas-edit-queue",
    )
    unit_ref = plan.items[0].source_evidence_ref
    await repository.persist_index_record(
        BusinessBrainIndexRecordContract(
            index_id="idx:atlas-edit:41",
            workspace_id=workspace.id,
            fact_id=queued.source_fact_id,
            unit_ref=unit_ref,
            state="ready",
            embedding_state="pending",
            source_text="Atlas sumka yangi narxi 199000 UZS",
            source_refs=[unit_ref, queued.source_ref],
            idempotency_key="idx:atlas-edit:41",
        )
    )

    async def provider(request) -> LLMProviderResponse:
        assert request.input_payload["source_ref"] == queued.source_ref
        assert unit_ref in request.input_payload["allowed_evidence_refs"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "product_ref": "catalog_product:atlas-bag",
                            "product": {
                                "title": "Atlas sumka",
                                "identity_ref": "catalog_product:atlas-bag",
                                "category": "sumka",
                            },
                            "variants": [],
                            "offers": [
                                {
                                    "offer_ref": "catalog_offer:atlas-bag:main",
                                    "product_ref": "catalog_product:atlas-bag",
                                    "price": {"amount": 199000, "currency": "UZS"},
                                    "stock": {"state": "unknown"},
                                    "active": True,
                                }
                            ],
                            "media": [],
                            "source_fact": {
                                "source_ref": queued.source_ref,
                                "source_type": "telegram_channel",
                                "content_refs": [unit_ref],
                            },
                            "confidence": 0.86,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    result = await BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref=queued.source_ref,
            source_kind="telegram_channel",
            source_fact_id=queued.source_fact_id,
            correlation_id="corr:atlas-edit-learn",
            idempotency_key="atlas-edit-learn",
        )
    )

    offer = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_offer:catalog_product:atlas-bag:catalog_offer:atlas-bag:main",
    )
    extraction_candidates = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="extraction_candidate",
    )
    update_candidate = next(
        item
        for item in extraction_candidates
        if item.state["candidate"]["candidate_id"]
        == "business_source_catalog:catalog_product:atlas-bag"
    )

    assert result.catalog_candidate_count == 1
    assert result.extraction_proposal_refs
    assert offer is not None
    assert offer.status == "proposed"
    assert offer.value["catalog_update_policy"] == "create_update_proposal"
    assert offer.value["source_change_events"] == plan.source_change_events
    assert update_candidate.state["lifecycle_state"] == "proposed"
    assert update_candidate.state["candidate"]["operation"] == "update"
    assert update_candidate.state["candidate"]["reason_code"] == (
        "business_source_catalog_update_candidate"
    )
    assert update_candidate.state["candidate"]["value"]["source_change_events"] == (
        plan.source_change_events
    )


async def test_company_info_pdf_becomes_brain_kb_and_rules_for_seller_grounding(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        if request.prompt_id == "seller_agent.plan_and_compose":
            grounding = request.input_payload["grounding"]
            families = grounding["families"]
            assert "catalog_product" not in families
            assert families["knowledge_fact"][0]["fact_id"] == "knowledge:academy:mentor-sla"
            assert families["seller_rule_fact"][0]["fact_id"] == "rule:academy:billing-escalation"
            assert "fact:knowledge:academy:mentor-sla" in grounding["source_refs"]
            assert "fact:rule:academy:billing-escalation" in grounding["source_refs"]
            return LLMProviderResponse(
                text=json.dumps(
                    {
                        "planner": {
                            "should_reply": True,
                            "sales_move": "answer_support_policy_from_brain",
                            "customer_owned_missing_info": [],
                            "business_owned_missing_info": [],
                            "action_suggestions": [],
                            "risk_level": "low",
                            "confidence": 0.9,
                            "source_refs": [
                                "fact:knowledge:academy:mentor-sla",
                                "fact:rule:academy:billing-escalation",
                            ],
                        },
                        "composer": {
                            "draft_text": (
                                "Mentorlar odatda ish kunlarida 24 soat ichida javob beradi. "
                                "Billing masalalari bo'lsa, billing@oqim.test ga yo'naltiramiz."
                            ),
                            "confidence": 0.9,
                            "used_source_refs": [
                                "fact:knowledge:academy:mentor-sla",
                                "fact:rule:academy:billing-escalation",
                            ],
                            "voice_notes": ["clear_support_answer"],
                        },
                    }
                ),
                model_used="test-model",
            )
        if request.prompt_id == "seller_agent.verifier":
            composer = request.input_payload["composer"]
            return LLMProviderResponse(
                text=json.dumps(
                    {
                        "status": "approved",
                        "safe_reply": composer["draft_text"],
                        "blocked_claims": [],
                        "review_reason": None,
                        "confidence": 0.93,
                        "source_refs": [
                            "fact:knowledge:academy:mentor-sla",
                            "fact:rule:academy:billing-escalation",
                        ],
                    }
                ),
                model_used="test-model",
            )

        source_units = request.input_payload["source_units"]
        assert request.input_payload["source_kind"] == "pdf"
        assert request.input_payload["media_assets"] == []
        assert {
            "commerce_generic.v1",
            "generic_kb.v1",
            "seller_voice.v1",
        }.issubset(set(request.input_payload["extraction_request"]["profile_refs"]))
        unit_ref = source_units[0]["unit_ref"]
        assert unit_ref in request.input_payload["allowed_evidence_refs"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:academy:mentor-sla",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:support:mentor_sla",
                            "value": {
                                "topic": "mentor support SLA",
                                "question": "Mentorlar qancha vaqtda javob beradi?",
                                "answer": "Mentors reply within 24 hours on business days.",
                            },
                            "confidence": 0.87,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        },
                        {
                            "fact_id": "rule:academy:billing-escalation",
                            "fact_type": "seller_rule_fact",
                            "entity_ref": "business:rule:billing_escalation",
                            "value": {
                                "topic": "billing escalation",
                                "rule": "Escalate billing issues to billing@oqim.test.",
                            },
                            "confidence": 0.84,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        },
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:academy-info",
            source_kind="pdf",
            source_payload={"file_name": "academy-info.pdf"},
            content_bytes=_text_pdf_bytes(
                "OQIM Academy support information. "
                "Mentors reply within 24 hours on business days. "
                "Students can request a refund before the first live session. "
                "Escalate billing issues to billing@oqim.test."
            ),
            correlation_id="corr:academy-info:ingest",
            idempotency_key="academy-info:ingest",
        )
    )
    learning = await BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:academy-info",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            correlation_id="corr:academy-info:learn",
            idempotency_key="academy-info:learn",
        )
    )

    products = await repository.list_facts(
        workspace_id=workspace.id,
        fact_type="catalog_product",
        statuses=("proposed", "active"),
        limit=20,
    )
    hidden_kb = await BusinessBrainMemoryService(repository=repository).retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact", "seller_rule_fact", "catalog_product"],
            query_text="mentor javobi va billing masalasi",
            enable_semantic=False,
            include_source_units=True,
        )
    )
    review_kb = await BusinessBrainMemoryService(repository=repository).retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact", "seller_rule_fact"],
            query_text="mentor javobi va billing masalasi",
            enable_semantic=False,
            include_proposed=True,
            include_source_units=True,
        )
    )

    assert learning.catalog_candidate_count == 0
    assert learning.memory_candidate_count == 2
    assert products == ()
    assert hidden_kb.candidates == []
    assert {candidate.fact_id for candidate in review_kb.candidates} == {
        "knowledge:academy:mentor-sla",
        "rule:academy:billing-escalation",
    }
    assert all(candidate.status == "proposed" for candidate in review_kb.candidates)

    for fact_id in [
        "knowledge:academy:mentor-sla",
        "rule:academy:billing-escalation",
    ]:
        activated = await repository.mark_fact_status(
            workspace_id=workspace.id,
            fact_id=fact_id,
            status="active",
        )
        assert activated is not None

    retrieval = await BusinessBrainMemoryService(repository=repository).retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact", "seller_rule_fact", "catalog_product"],
            query_text="mentor qancha vaqtda javob beradi billing email",
            enable_semantic=False,
            include_source_units=True,
        )
    )
    assert {candidate.fact_id for candidate in retrieval.candidates} == {
        "knowledge:academy:mentor-sla",
        "rule:academy:billing-escalation",
    }
    assert all(candidate.fact_type != "catalog_product" for candidate in retrieval.candidates)


async def test_source_learning_uses_structured_shopify_json_without_llm(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def fetch_source(url: str) -> SourceFetchResult:
        assert url == "https://seller.example/products.json"
        return SourceFetchResult(
            content=json.dumps(
                {
                    "products": [
                        {
                            "id": 917,
                            "title": "Macbro Clear Case",
                            "handle": "macbro-clear-case",
                            "vendor": "Macbro",
                            "product_type": "case",
                            "body_html": "<p>Transparent MagSafe case.</p>",
                            "options": [{"name": "Color", "values": ["Clear"]}],
                            "variants": [
                                {
                                    "title": "Default",
                                    "sku": "MB-CASE",
                                    "price": "299000.00",
                                    "available": True,
                                    "option1": "Clear",
                                }
                            ],
                            "images": [
                                {
                                    "id": 91,
                                    "src": "https://seller.example/case.jpg",
                                    "position": 1,
                                }
                            ],
                        }
                    ]
                }
            ).encode("utf-8"),
            content_type="application/json",
            final_url=url,
        )

    async def provider(_request) -> LLMProviderResponse:
        raise AssertionError("structured Shopify JSON should not call the LLM")

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(
        repository=repository,
        fetch_source=fetch_source,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:shopify:macbro-products-json",
            source_kind="website",
            source_payload={"url": "https://seller.example/products.json"},
            correlation_id="corr-shopify-json-ingest",
            idempotency_key="shopify-json-ingest",
        )
    )
    result = await BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:shopify:macbro-products-json",
            source_kind="website",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-shopify-json-learn",
            idempotency_key="shopify-json-learn",
        )
    )

    products = await repository.list_facts(
        workspace_id=workspace.id,
        fact_type="catalog_product",
        statuses=("proposed", "active", "confirmed"),
    )
    media = await repository.list_facts(
        workspace_id=workspace.id,
        fact_type="catalog_media",
        statuses=("proposed", "active", "confirmed"),
    )

    assert result.catalog_candidate_count == 1
    assert result.rejected_candidates == []
    assert products
    assert products[0].value["title"] == "Macbro Clear Case"
    assert media
    assert media[0].value["source_media_ref"] == source.media_assets[0].media_ref
    assert media[0].value["quality_state"] == "product_media"


async def test_source_learning_routes_past_conversations_through_universal_extraction(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        extraction_request = request.input_payload["extraction_request"]
        assert "conversation_pairs.v1" in extraction_request["profile_refs"]
        assert "conversation_pair" in extraction_request["target_kinds"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "schema_version": "business_source_memory_candidate.v1",
                            "fact_id": "conversation_pair:imported-course-price",
                            "fact_type": "conversation_pair_fact",
                            "entity_ref": "business:conversation_pairs",
                            "value": {
                                "customer_turn": "Kurs narxi va davomiyligi qancha?",
                                "seller_turn": (
                                    "Boshlang'ich kurs 6 hafta, narxi 700 ming so'm."
                                ),
                                "intent": "course_pricing",
                                "source_refs": [unit_ref],
                            },
                            "confidence": 0.88,
                            "risk_tier": "low",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:conversation:course-pricing",
            source_kind="past_conversation",
            source_payload={
                "conversation_id": 44,
                "turns": [
                    {
                        "message_ref": "conv:44:customer:1",
                        "sender_type": "customer",
                        "content": "Kurs narxi va davomiyligi qancha?",
                    },
                    {
                        "message_ref": "conv:44:seller:2",
                        "sender_type": "seller",
                        "content": "Boshlang'ich kurs 6 hafta, narxi 700 ming so'm.",
                        "quality_label": "approved",
                    },
                ],
            },
            correlation_id="corr-past-conversation-ingest",
            idempotency_key="past-conversation-ingest",
        )
    )
    learner = BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:conversation:course-pricing",
            source_kind="past_conversation",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-past-conversation-learn",
            idempotency_key="past-conversation-learn",
        )
    )
    pair = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="conversation_pair:imported-course-price",
    )
    extraction_candidates = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="extraction_candidate",
    )

    assert result.gateway_status == "ok"
    assert result.memory_candidate_count == 1
    assert result.extraction_run_id == (
        "extraction:past-conversation-learn:universal-extraction"
    )
    assert result.extraction_candidate_count == 1
    assert len(result.extraction_proposal_refs) == 1
    assert pair is not None
    assert pair.status == "proposed"
    assert pair.fact_type == "conversation_pair_fact"
    assert pair.value["customer_turn"] == "Kurs narxi va davomiyligi qancha?"
    assert pair.source_refs == [
        source.source_units[0].unit_ref,
        "onboarding:conversation:course-pricing",
    ]
    assert {
        item.state["candidate"]["profile_ref"]
        for item in extraction_candidates
        if item.state["candidate_state"] == "accepted"
    } == {"conversation_pairs.v1"}


async def test_source_learning_rejects_candidates_with_made_up_evidence_refs(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(_request) -> LLMProviderResponse:
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:made-up",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:faq",
                            "value": {"topic": "refund", "answer": "Refunds are always free."},
                            "confidence": 0.9,
                            "risk_tier": "low",
                            "evidence_refs": ["source_unit:hallucinated"],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    ingestion = OnboardingSourceIngestionService(repository=repository)
    source = await ingestion.ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:text:rules",
            source_kind="text",
            source_payload={"text": "Yetkazib berish Toshkent ichida bor."},
            correlation_id="corr-text-ingest",
            idempotency_key="text-ingest",
        )
    )
    learner = BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:text:rules",
            source_kind="text",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-text-learn",
            idempotency_key="text-learn",
        )
    )
    hallucinated = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:made-up",
    )
    projection = await repository.get_projection(
        workspace_id=workspace.id,
        projection_ref="business_source_learning:onboarding:text:rules",
    )

    assert result.memory_candidate_count == 0
    assert result.rejected_candidates == [
        {
            "candidate_ref": "knowledge:made-up",
            "candidate_type": "knowledge_fact",
            "reason": "unsupported_evidence_refs",
            "unsupported_refs": ["source_unit:hallucinated"],
        }
    ]
    assert result.degraded_reasons == ["unsupported_evidence_refs"]
    assert hallucinated is None
    assert projection is not None
    assert projection.degraded is True
    assert projection.state["rejected_candidate_count"] == 1


async def test_source_learning_repairs_catalog_source_fact_without_semantic_guessing(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        media_ref = request.input_payload["media_assets"][0]["media_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "schema_version": "business_source_catalog_candidate.v1",
                            "product_ref": "catalog_product:missing-source-ref",
                            "product": {
                                "title": "Visible product",
                                "identity_ref": "catalog_product:missing-source-ref",
                            },
                            "variants": [],
                            "offers": [],
                            "media": [],
                            "source_fact": {
                                "source_type": "pdf",
                                "content_refs": [media_ref],
                            },
                            "confidence": 0.74,
                            "risk_tier": "medium",
                            "evidence_refs": [media_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:source-repair",
            source_kind="pdf",
            source_payload={"file_name": "source-repair.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-source-repair-ingest",
            idempotency_key="source-repair-ingest",
        )
    )
    learner = BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:source-repair",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-source-repair-learn",
            idempotency_key="source-repair-learn",
        )
    )
    product = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:missing-source-ref",
    )
    source_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_source:catalog_product:missing-source-ref:onboarding:pdf:source-repair",
    )
    media_fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=(
            "catalog_media:catalog_product:missing-source-ref:"
            "catalog_media:catalog_product:missing-source-ref:source-page:0"
        ),
    )

    assert result.catalog_candidate_count == 1
    assert result.rejected_candidates == []
    assert result.degraded_reasons == []
    assert product is not None
    assert product.value["title"] == "Visible product"
    assert product.source_refs == [
        source.media_assets[0].media_ref,
        "onboarding:pdf:source-repair",
    ]
    assert source_fact is not None
    assert source_fact.value == {
        "source_type": "pdf",
        "content_refs": [source.media_assets[0].media_ref],
        "source_ref": "onboarding:pdf:source-repair",
    }
    assert media_fact is not None
    assert media_fact.value == {
        "media_ref": "catalog_media:catalog_product:missing-source-ref:source-page:0",
        "product_ref": "catalog_product:missing-source-ref",
        "source_media_ref": source.media_assets[0].media_ref,
        "media_type": "source_page",
        "quality_state": "page_media_only",
        "crop_state": "pending",
        "approved": False,
    }


async def test_source_learning_rejects_catalog_candidate_without_human_title(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        media_ref = request.input_payload["media_assets"][0]["media_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "schema_version": "business_source_catalog_candidate.v1",
                            "product_ref": "catalog_product:raw-sku-only",
                            "product": {
                                "title": "catalog_product:raw-sku-only",
                                "identity_ref": "catalog_product:raw-sku-only",
                            },
                            "variants": [],
                            "offers": [],
                            "media": [],
                            "source_fact": {
                                "source_ref": "onboarding:pdf:title-quality",
                                "source_type": "pdf",
                                "content_refs": [media_ref],
                            },
                            "confidence": 0.72,
                            "risk_tier": "medium",
                            "evidence_refs": [media_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:title-quality",
            source_kind="pdf",
            source_payload={"file_name": "title-quality.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-title-quality-ingest",
            idempotency_key="title-quality-ingest",
        )
    )
    learner = BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:title-quality",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-title-quality-learn",
            idempotency_key="title-quality-learn",
        )
    )
    product = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:raw-sku-only",
    )

    assert result.catalog_candidate_count == 0
    assert result.degraded_reasons == ["malformed_catalog_candidate"]
    assert result.rejected_candidates == [
        {
            "candidate_ref": "catalog_product:raw-sku-only",
            "candidate_type": "catalog_product",
            "reason": "malformed_catalog_candidate",
            "unsupported_refs": [],
            "validation_errors": ["product.title_human_readable"],
        }
    ]
    assert product is None


async def test_source_learning_repairs_missing_catalog_media_ref_from_source_media(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        media_ref = request.input_payload["media_assets"][0]["media_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "schema_version": "business_source_catalog_candidate.v1",
                            "product_ref": "catalog_product:media-ref-repair",
                            "product": {
                                "title": "Media ref repair product",
                                "identity_ref": "catalog_product:media-ref-repair",
                            },
                            "variants": [],
                            "offers": [],
                            "media": [
                                {
                                    "product_ref": "catalog_product:media-ref-repair",
                                    "source_media_ref": media_ref,
                                    "media_type": "image",
                                    "quality_state": "page_media_only",
                                    "crop_state": "pending",
                                    "approved": False,
                                }
                            ],
                            "source_fact": {
                                "source_ref": "onboarding:pdf:media-ref-repair",
                                "source_type": "pdf",
                                "content_refs": [media_ref],
                            },
                            "confidence": 0.72,
                            "risk_tier": "medium",
                            "evidence_refs": [media_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:media-ref-repair",
            source_kind="pdf",
            source_payload={"file_name": "media-ref-repair.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-media-ref-repair-ingest",
            idempotency_key="media-ref-repair-ingest",
        )
    )
    learner = BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:media-ref-repair",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-media-ref-repair-learn",
            idempotency_key="media-ref-repair-learn",
        )
    )
    media = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=(
            "catalog_media:catalog_product:media-ref-repair:"
            "catalog_media:catalog_product:media-ref-repair:source-media:0"
        ),
    )

    assert result.catalog_candidate_count == 1
    assert result.rejected_candidates == []
    assert media is not None
    assert media.value["media_ref"] == (
        "catalog_media:catalog_product:media-ref-repair:source-media:0"
    )
    assert media.value["source_media_ref"] == source.media_assets[0].media_ref
    assert media.value["quality_state"] == "page_media_only"
    assert media.value["crop_state"] == "pending"


async def test_source_learning_forces_ai_catalog_media_unapproved(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        media_ref = request.input_payload["media_assets"][0]["media_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "schema_version": "business_source_catalog_candidate.v1",
                            "product_ref": "catalog_product:unsafe-media-approval",
                            "product": {
                                "title": "Unsafe media approval product",
                                "identity_ref": "catalog_product:unsafe-media-approval",
                            },
                            "variants": [],
                            "offers": [],
                            "media": [
                                {
                                    "media_ref": "catalog_media:unsafe-media-approval:main",
                                    "product_ref": "catalog_product:unsafe-media-approval",
                                    "source_media_ref": media_ref,
                                    "media_type": "image",
                                    "approved": True,
                                }
                            ],
                            "source_fact": {
                                "source_ref": "onboarding:pdf:unsafe-media-approval",
                                "source_type": "pdf",
                                "content_refs": [media_ref],
                            },
                            "confidence": 0.82,
                            "risk_tier": "medium",
                            "evidence_refs": [media_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:unsafe-media-approval",
            source_kind="pdf",
            source_payload={"file_name": "unsafe-media-approval.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-unsafe-media-approval-ingest",
            idempotency_key="unsafe-media-approval-ingest",
        )
    )
    learner = BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:unsafe-media-approval",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-unsafe-media-approval-learn",
            idempotency_key="unsafe-media-approval-learn",
        )
    )
    media = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id=(
            "catalog_media:catalog_product:unsafe-media-approval:"
            "catalog_media:unsafe-media-approval:main"
        ),
    )

    assert result.catalog_candidate_count == 1
    assert media is not None
    assert media.value["approved"] is False


async def test_source_learning_rejects_empty_memory_candidate_values(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [],
                    "memory_candidates": [
                        {
                            "schema_version": "business_source_memory_candidate.v1",
                            "fact_id": "knowledge:empty-live-vision",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:faq",
                            "value": {},
                            "confidence": 0.8,
                            "risk_tier": "low",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:text:empty-value",
            source_kind="text",
            source_payload={"text": "Yetkazib berish Toshkent ichida bor."},
            correlation_id="corr-empty-value-ingest",
            idempotency_key="empty-value-ingest",
        )
    )
    learner = BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:text:empty-value",
            source_kind="text",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-empty-value-learn",
            idempotency_key="empty-value-learn",
        )
    )
    fact = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:empty-live-vision",
    )

    assert result.memory_candidate_count == 0
    assert result.rejected_candidates == [
        {
            "candidate_ref": "knowledge:empty-live-vision",
            "candidate_type": "knowledge_fact",
            "reason": "empty_candidate_value",
            "unsupported_refs": [],
        }
    ]
    assert result.degraded_reasons == ["empty_candidate_value"]
    assert fact is None


async def test_source_learning_can_accept_image_only_pdf_candidates_grounded_to_media_ref(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def provider(request) -> LLMProviderResponse:
        assert request.input_payload["source_units"] == []
        media_ref = request.input_payload["media_assets"][0]["media_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "product_ref": "catalog_product:pdf-visual-product",
                            "product": {
                                "title": "PDF visual product",
                                "identity_ref": "catalog_product:pdf-visual-product",
                            },
                            "variants": [],
                            "offers": [],
                            "media": [
                                {
                                    "media_ref": "catalog_media:pdf-visual-product:page-1",
                                    "product_ref": "catalog_product:pdf-visual-product",
                                    "source_media_ref": media_ref,
                                    "media_type": "image",
                                    "approved": False,
                                }
                            ],
                            "source_fact": {
                                "source_ref": "onboarding:pdf:scanned",
                                "source_type": "pdf",
                                "content_refs": [media_ref],
                            },
                            "confidence": 0.62,
                            "risk_tier": "medium",
                            "evidence_refs": [media_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(repository=repository).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:scanned",
            source_kind="pdf",
            source_payload={"file_name": "scanned.pdf"},
            content_bytes=_image_only_pdf_bytes(),
            correlation_id="corr-pdf-ingest",
            idempotency_key="pdf-ingest",
        )
    )
    learner = BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    )

    result = await learner.learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:pdf:scanned",
            source_kind="pdf",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-pdf-learn",
            idempotency_key="pdf-learn",
        )
    )
    product = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:pdf-visual-product",
    )

    assert result.catalog_candidate_count == 1
    assert result.evidence_summary == {
        "source_unit_count": 0,
        "media_asset_count": 1,
        "allowed_evidence_ref_count": 1,
    }
    assert product is not None
    assert product.source_refs == [
        source.media_assets[0].media_ref,
        "onboarding:pdf:scanned",
    ]
