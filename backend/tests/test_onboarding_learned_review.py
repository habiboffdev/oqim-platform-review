from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.business_brain.contracts import BusinessBrainIndexRecordContract
from app.modules.business_brain.source_learning import (
    BusinessSourceLearningRequest,
    BusinessSourceLearningService,
)
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
from app.modules.onboarding_learning.learned_review import (
    build_onboarding_learned_review_projection,
)
from app.modules.onboarding_learning.review_actions import (
    OnboardingLearnedReviewActionRequest,
    OnboardingLearnedReviewActionService,
)
from app.services.channel_sync_models import ChannelMessageRecord


async def test_learned_review_groups_products_media_offers_and_faqs(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    async def fetch_source(url: str) -> SourceFetchResult:
        assert url == "https://nafis.example/catalog"
        return SourceFetchResult(
            content=(
                b"<html><body>"
                b"<h1>Binafsha charm sumka</h1>"
                b"<img src='/sumka.jpg' alt='Binafsha sumka rasmi'>"
                b"<p>Narxi 180000 UZS. Toshkent ichida yetkazib berish bor.</p>"
                b"</body></html>"
            ),
            content_type="text/html",
            final_url=url,
        )

    async def provider(request) -> LLMProviderResponse:
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        media_ref = request.input_payload["media_assets"][0]["media_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "product_ref": "catalog_product:binafsha-sumka",
                            "product": {
                                "title": "Binafsha charm sumka",
                                "category": "sumka",
                                "description": "Binafsha rang charm sumka.",
                                "identity_ref": "catalog_product:binafsha-sumka",
                            },
                            "variants": [],
                            "offers": [
                                {
                                    "offer_ref": "catalog_offer:binafsha-sumka:main",
                                    "product_ref": "catalog_product:binafsha-sumka",
                                    "price": {"amount": 180000, "currency": "UZS"},
                                    "stock": {"state": "unknown"},
                                    "active": True,
                                }
                            ],
                            "media": [
                                {
                                    "media_ref": "catalog_media:binafsha-sumka:main",
                                    "product_ref": "catalog_product:binafsha-sumka",
                                    "source_media_ref": media_ref,
                                    "media_type": "image",
                                    "url": "https://nafis.example/sumka.jpg",
                                }
                            ],
                            "source_fact": {
                                "source_ref": "onboarding:web:catalog",
                                "source_type": "website",
                                "content_refs": [unit_ref, media_ref],
                            },
                            "confidence": 0.86,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref, media_ref],
                        }
                    ],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:delivery:website",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:delivery",
                            "value": {
                                "topic": "yetkazib berish",
                                "answer": "Toshkent ichida yetkazib berish bor.",
                            },
                            "confidence": 0.78,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    repository = CommercialSpineRepository(db_session)
    source = await OnboardingSourceIngestionService(
        repository=repository,
        fetch_source=fetch_source,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:web:catalog",
            source_kind="website",
            source_payload={"url": "https://nafis.example/catalog"},
            correlation_id="corr-review-ingest",
            idempotency_key="review-ingest",
        )
    )
    await BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:web:catalog",
            source_kind="website",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-review-learn",
            idempotency_key="review-learn",
        )
    )

    review = build_onboarding_learned_review_projection(
        facts=await repository.list_facts(workspace_id=workspace.id, limit=50),
    )

    assert review["schema_version"] == "onboarding_learned_review.v1"
    assert review["status"] == "needs_review"
    assert review["summary"] == {
        "products": 1,
        "knowledge": 1,
        "rules": 0,
        "voice": 0,
        "integrations": 0,
        "media": 1,
        "offers": 1,
        "total_review_items": 2,
    }
    assert review["products"][0]["product_ref"] == "catalog_product:binafsha-sumka"
    assert review["products"][0]["title"] == "Binafsha charm sumka"
    assert review["products"][0]["source_evidence"][0]["label"] == "nafis.example"
    assert review["products"][0]["offers"][0]["price"] == {
        "amount": 180000,
        "currency": "UZS",
    }
    assert review["products"][0]["media"][0]["source_media_ref"] == source.media_assets[0].media_ref
    assert review["knowledge"][0]["answer"] == "Toshkent ichida yetkazib berish bor."
    assert review["knowledge"][0]["source_evidence"][0]["label"] == "nafis.example"


async def test_learned_review_approve_product_family_makes_catalog_and_media_active(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    source = await _learn_sample_product_and_knowledge(repository, workspace)

    result = await OnboardingLearnedReviewActionService(
        repository=repository,
    ).apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action="approve",
            target_type="product",
            target_ref="catalog_product:binafsha-sumka",
            correlation_id="corr-review-approve-product",
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    product = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:binafsha-sumka",
    )
    offer = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_offer:catalog_product:binafsha-sumka:catalog_offer:binafsha-sumka:main",
    )
    media = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_media:catalog_product:binafsha-sumka:catalog_media:binafsha-sumka:main",
    )
    review = build_onboarding_learned_review_projection(
        facts=await repository.list_facts(workspace_id=workspace.id, limit=50),
    )
    extraction_candidates = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="extraction_candidate",
    )
    product_candidate = next(
        item
        for item in extraction_candidates
        if item.state["candidate"]["candidate_id"]
        == "business_source_catalog:catalog_product:binafsha-sumka"
    )

    assert source.source_fact_id
    assert result.applied_count == 4
    assert result.rejected_count == 0
    assert product is not None and product.status == "active"
    assert offer is not None and offer.status == "active"
    assert media is not None and media.status == "active"
    assert media.value["approved"] is True
    assert review["summary"]["products"] == 0
    assert review["summary"]["knowledge"] == 1
    assert product_candidate.state["lifecycle_state"] == "approved"
    assert product_candidate.state["owner_review_action"] == "approve"
    assert product_candidate.state["owner_reviewed_fact_id"] == (
        "catalog_product:binafsha-sumka"
    )


async def test_learned_review_approve_product_family_updates_typed_catalog_authority(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    await _learn_sample_product_and_knowledge(repository, workspace)
    catalog = CommerceCatalogCoreService(db_session)

    before = await catalog.search_authority(
        workspace_id=workspace.id,
        query="binafsha charm sumka narxi",
    )
    assert before.products == []

    await OnboardingLearnedReviewActionService(
        repository=repository,
    ).apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action="approve",
            target_type="product",
            target_ref="catalog_product:binafsha-sumka",
            correlation_id="corr-review-approve-product-typed-catalog",
            actor_ref=f"workspace:{workspace.id}",
        )
    )

    after = await catalog.search_authority(
        workspace_id=workspace.id,
        query="binafsha charm sumka narxi",
        include_media=True,
    )
    assert [product.product_ref for product in after.products] == [
        "catalog_product:binafsha-sumka"
    ]
    assert after.products[0].authority_state == "approved"
    assert after.offers[0].price == "180000"
    assert after.offers[0].currency == "UZS"
    assert after.media[0].authority_state == "approved"
    assert after.media[0].url == "https://nafis.example/sumka.jpg"


async def test_learned_review_approves_edited_channel_update_into_typed_catalog(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    await _learn_sample_product_and_knowledge(repository, workspace)
    review_service = OnboardingLearnedReviewActionService(repository=repository)
    await review_service.apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action="approve",
            target_type="product",
            target_ref="catalog_product:binafsha-sumka",
            correlation_id="corr-review-initial-product",
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    catalog = CommerceCatalogCoreService(db_session)
    before = await catalog.search_authority(
        workspace_id=workspace.id,
        query="binafsha charm sumka narxi",
    )
    assert [offer.price for offer in before.offers] == ["180000"]

    subscription = ChannelSourceSubscription(
        subscription_id="source-sub:telegram:@nafis",
        workspace_id=workspace.id,
        channel_account_id="telegram:user:owner",
        channel_kind="telegram_channel",
        external_channel_ref="@nafis",
        source_scope="catalog",
        sync_policy={"mode": "background", "max_posts": 50},
        freshness_state="fresh",
        last_cursor="42",
        status="active",
    )
    plan = ChannelRuntimeCore().plan_channel_source_ingestion(
        subscription=subscription,
        messages=[
            ChannelMessageRecord(
                external_message_id="42",
                sender_external_id="@nafis",
                text="Binafsha charm sumka yangi narxi 199000 UZS",
                sent_at=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
                edited_at=datetime(2026, 6, 5, 12, 5, tzinfo=UTC),
                edit_version="2",
                supersedes_external_message_id="42",
                is_outgoing=False,
            )
        ],
    )
    queued = await ChannelSourceLearningQueueService(repository).queue_ingestion_plan(
        plan=plan,
        correlation_id="corr-binafsha-edit-queue",
    )
    unit_ref = plan.items[0].source_evidence_ref
    await repository.persist_index_record(
        BusinessBrainIndexRecordContract(
            index_id="idx:binafsha-edit:42",
            workspace_id=workspace.id,
            fact_id=queued.source_fact_id,
            unit_ref=unit_ref,
            state="ready",
            embedding_state="pending",
            source_text="Binafsha charm sumka yangi narxi 199000 UZS",
            source_refs=[unit_ref, queued.source_ref],
            idempotency_key="idx:binafsha-edit:42",
        )
    )

    async def provider(request) -> LLMProviderResponse:
        assert request.input_payload["source_ref"] == queued.source_ref
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "product_ref": "catalog_product:binafsha-sumka",
                            "product": {
                                "title": "Binafsha charm sumka",
                                "category": "sumka",
                                "description": "Binafsha rang charm sumka.",
                                "identity_ref": "catalog_product:binafsha-sumka",
                            },
                            "variants": [],
                            "offers": [
                                {
                                    "offer_ref": "catalog_offer:binafsha-sumka:main",
                                    "product_ref": "catalog_product:binafsha-sumka",
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
                            "confidence": 0.88,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                    "memory_candidates": [],
                }
            ),
            model_used="test-model",
        )

    learning = await BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref=queued.source_ref,
            source_kind="telegram_channel",
            source_fact_id=queued.source_fact_id,
            correlation_id="corr-binafsha-edit-learn",
            idempotency_key="binafsha-edit-learn",
        )
    )
    assert learning.extraction_proposal_refs

    review_result = await review_service.apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action="approve",
            target_type="product",
            target_ref="catalog_product:binafsha-sumka",
            correlation_id="corr-review-approve-binafsha-edit",
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    after = await catalog.search_authority(
        workspace_id=workspace.id,
        query="binafsha charm sumka narxi",
    )
    original_offer = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_offer:catalog_product:binafsha-sumka:catalog_offer:binafsha-sumka:main",
    )
    approved_update_offers = await repository.list_facts(
        workspace_id=workspace.id,
        entity_ref="catalog_product:binafsha-sumka",
        fact_type="catalog_offer",
        statuses=("active",),
        limit=20,
    )
    update_offer = next(item for item in approved_update_offers if item.supersedes_fact_id)
    extraction_candidates = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="extraction_candidate",
    )
    update_candidate = next(
        item
        for item in extraction_candidates
        if item.state["candidate"]["reason_code"]
        == "business_source_catalog_update_candidate"
    )

    assert review_result.applied_count >= 2
    assert [offer.price for offer in after.offers] == ["199000"]
    assert original_offer is not None
    assert original_offer.status == "superseded"
    assert update_offer.value["catalog_update_policy"] == "create_update_proposal"
    assert update_offer.value["source_change_events"] == plan.source_change_events
    assert update_candidate.state["lifecycle_state"] == "approved"
    assert update_candidate.state["owner_review_action"] == "approve"


async def test_learned_review_rejects_single_memory_fact_without_activating_it(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    await _learn_sample_product_and_knowledge(repository, workspace)

    result = await OnboardingLearnedReviewActionService(
        repository=repository,
    ).apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action="reject",
            target_type="fact",
            target_ref="knowledge:delivery:website",
            correlation_id="corr-review-reject-kb",
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    knowledge = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="knowledge:delivery:website",
    )
    review = build_onboarding_learned_review_projection(
        facts=await repository.list_facts(workspace_id=workspace.id, limit=50),
    )
    extraction_candidates = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="extraction_candidate",
    )
    knowledge_candidate = next(
        item
        for item in extraction_candidates
        if item.state["candidate"]["candidate_id"]
        == "business_source_memory:knowledge:delivery:website"
    )

    assert result.applied_count == 0
    assert result.rejected_count == 1
    assert knowledge is not None
    assert knowledge.status == "rejected"
    assert review["summary"]["knowledge"] == 0
    assert review["summary"]["products"] == 1
    assert knowledge_candidate.state["lifecycle_state"] == "rejected"
    assert knowledge_candidate.state["owner_review_action"] == "reject"


async def test_learned_review_merges_duplicate_product_family_out_of_review_queue(
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    repository = CommercialSpineRepository(db_session)
    await _learn_sample_product_and_knowledge(repository, workspace)
    await repository.persist_fact(
        (await repository.get_fact(
            workspace_id=workspace.id,
            fact_id="catalog_product:binafsha-sumka",
        )).model_copy(
            update={
                "fact_id": "catalog_product:binafsha-sumka-copy",
                "entity_ref": "catalog_product:binafsha-sumka-copy",
                "value": {
                    "title": "Binafsha sumka",
                    "category": "sumka",
                    "description": "Takroriy mahsulot",
                    "identity_ref": "catalog_product:binafsha-sumka-copy",
                },
                "idempotency_key": "duplicate-product",
            }
        )
    )

    result = await OnboardingLearnedReviewActionService(
        repository=repository,
    ).apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action="merge",
            target_type="product",
            target_ref="catalog_product:binafsha-sumka-copy",
            merge_into_ref="catalog_product:binafsha-sumka",
            correlation_id="corr-review-merge-product",
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    duplicate = await repository.get_fact(
        workspace_id=workspace.id,
        fact_id="catalog_product:binafsha-sumka-copy",
    )

    assert result.merged_count == 1
    assert duplicate is not None
    assert duplicate.status == "rejected"
    assert duplicate.value["merged_into_ref"] == "catalog_product:binafsha-sumka"


async def _learn_sample_product_and_knowledge(
    repository: CommercialSpineRepository,
    workspace: Workspace,
):
    async def fetch_source(url: str) -> SourceFetchResult:
        assert url == "https://nafis.example/catalog"
        return SourceFetchResult(
            content=(
                b"<html><body>"
                b"<h1>Binafsha charm sumka</h1>"
                b"<img src='/sumka.jpg' alt='Binafsha sumka rasmi'>"
                b"<p>Narxi 180000 UZS. Toshkent ichida yetkazib berish bor.</p>"
                b"</body></html>"
            ),
            content_type="text/html",
            final_url=url,
        )

    async def provider(request) -> LLMProviderResponse:
        unit_ref = request.input_payload["source_units"][0]["unit_ref"]
        media_ref = request.input_payload["media_assets"][0]["media_ref"]
        return LLMProviderResponse(
            text=json.dumps(
                {
                    "schema_version": "business_source_learning_output.v1",
                    "catalog_candidates": [
                        {
                            "product_ref": "catalog_product:binafsha-sumka",
                            "product": {
                                "title": "Binafsha charm sumka",
                                "category": "sumka",
                                "description": "Binafsha rang charm sumka.",
                                "identity_ref": "catalog_product:binafsha-sumka",
                            },
                            "variants": [],
                            "offers": [
                                {
                                    "offer_ref": "catalog_offer:binafsha-sumka:main",
                                    "product_ref": "catalog_product:binafsha-sumka",
                                    "price": {"amount": 180000, "currency": "UZS"},
                                    "stock": {"state": "unknown"},
                                    "active": True,
                                }
                            ],
                            "media": [
                                {
                                    "media_ref": "catalog_media:binafsha-sumka:main",
                                    "product_ref": "catalog_product:binafsha-sumka",
                                    "source_media_ref": media_ref,
                                    "media_type": "image",
                                    "url": "https://nafis.example/sumka.jpg",
                                }
                            ],
                            "source_fact": {
                                "source_ref": "onboarding:web:catalog",
                                "source_type": "website",
                                "content_refs": [unit_ref, media_ref],
                            },
                            "confidence": 0.86,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref, media_ref],
                        }
                    ],
                    "memory_candidates": [
                        {
                            "fact_id": "knowledge:delivery:website",
                            "fact_type": "knowledge_fact",
                            "entity_ref": "business:delivery",
                            "value": {
                                "topic": "yetkazib berish",
                                "answer": "Toshkent ichida yetkazib berish bor.",
                            },
                            "confidence": 0.78,
                            "risk_tier": "medium",
                            "evidence_refs": [unit_ref],
                        }
                    ],
                }
            ),
            model_used="test-model",
        )

    source = await OnboardingSourceIngestionService(
        repository=repository,
        fetch_source=fetch_source,
    ).ingest(
        OnboardingSourceIngestionRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:web:catalog",
            source_kind="website",
            source_payload={"url": "https://nafis.example/catalog"},
            correlation_id="corr-review-ingest",
            idempotency_key="review-ingest",
        )
    )
    await BusinessSourceLearningService(
        repository=repository,
        gateway=LLMGateway(repository=repository, provider=provider),
    ).learn_from_source(
        BusinessSourceLearningRequest(
            workspace_id=workspace.id,
            source_ref="onboarding:web:catalog",
            source_kind="website",
            source_fact_id=source.source_fact_id,
            correlation_id="corr-review-learn",
            idempotency_key="review-learn",
        )
    )
    return source
