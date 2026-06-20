from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models.commerce_catalog import (
    CatalogMediaRecord,
    CatalogMissingFieldRecord,
    CatalogOfferRecord,
    CatalogProductRecord,
    CatalogSourceFactRecord,
)
from app.models.commercial_spine import BusinessBrainFactRecord, BusinessBrainIndexRecord
from app.modules.commerce_catalog.service import CommerceCatalogCoreService
from app.modules.knowledge_mcp.contracts import (
    KnowledgeCandidateInput,
    KnowledgeCatalogSearchRequest,
    KnowledgeSaveInput,
    KnowledgeScope,
)
from app.modules.knowledge_mcp.service import KnowledgeMCPService
from app.modules.retrieval_core.indexing import RetrievalIndexEmbeddingResult

pytestmark = pytest.mark.asyncio


def _catalog_fact(
    *,
    workspace_id: int,
    fact_id: str,
    fact_type: str,
    entity_ref: str,
    value: dict,
    source_refs: list[str] | None = None,
    status: str = "approved",
) -> BusinessBrainFactRecord:
    now = datetime.now(UTC)
    return BusinessBrainFactRecord(
        fact_id=fact_id,
        workspace_id=workspace_id,
        fact_type=fact_type,
        entity_ref=entity_ref,
        value=value,
        confidence=0.92,
        status=status,
        risk_tier="low",
        valid_from=now,
        source_refs=source_refs or [f"source:{fact_id}"],
        idempotency_key=f"idem:{fact_id}",
        raw_fact={
            "approval_state": "approved",
            "source_refs": source_refs or [f"source:{fact_id}"],
            "value": value,
        },
    )


async def test_projects_approved_source_facts_into_typed_catalog_authority(db_session):
    workspace_id = 99101
    db_session.add_all(
        [
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_product:starter",
                fact_type="catalog_product",
                entity_ref="product:starter",
                value={
                    "product_ref": "product:starter",
                    "name": "Starter Coins",
                    "aliases": ["starter coin"],
                    "description": "SATStation starter coin package",
                },
                source_refs=["telegram_channel:post:1"],
            ),
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_offer:starter:uzs",
                fact_type="catalog_offer",
                entity_ref="product:starter",
                value={
                    "product_ref": "product:starter",
                    "price": "50000",
                    "currency": "UZS",
                    "stock": "available",
                },
                source_refs=["telegram_channel:post:1"],
            ),
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_media:starter:hero",
                fact_type="catalog_media",
                entity_ref="product:starter",
                value={
                    "product_ref": "product:starter",
                    "media_ref": "media:starter:hero",
                    "url": "https://example.test/starter.webp",
                    "caption": "Starter Coins visual",
                    "ocr_text": "Starter Coins 50000 UZS",
                    "approved": True,
                },
                source_refs=["telegram_channel:post:media:1"],
            ),
        ]
    )
    await db_session.commit()

    service = CommerceCatalogCoreService(db_session)
    projection = await service.project_from_business_brain(workspace_id=workspace_id)

    assert projection.products == 1
    assert projection.offers == 1
    assert projection.media == 1
    assert projection.missing_fields == 0

    result = await service.search_authority(
        workspace_id=workspace_id,
        query="starter coins narxi rasmi",
        include_media=True,
    )

    assert result.products[0].product_ref == "product:starter"
    assert result.products[0].name == "Starter Coins"
    assert result.offers[0].price == "50000"
    assert result.offers[0].currency == "UZS"
    assert result.media[0].media_ref == "media:starter:hero"
    assert result.media[0].url == "https://example.test/starter.webp"
    assert "price" not in [field.field for field in result.missing_fields]
    assert result.telemetry.source_fact_count == 3

    assert await db_session.scalar(select(func.count()).select_from(CatalogProductRecord)) == 1
    assert await db_session.scalar(select(func.count()).select_from(CatalogOfferRecord)) == 1
    assert await db_session.scalar(select(func.count()).select_from(CatalogMediaRecord)) == 1


async def test_projects_approved_catalog_media_into_retrieval_vector_index(
    db_session,
    monkeypatch,
):
    workspace_id = 99111
    embedded_texts: list[str] = []

    async def fake_embed_texts(self, texts, *, enabled, context_prefix):
        embedded_texts.extend(texts)
        assert enabled is True
        assert context_prefix == "business_brain_index"
        return [
            RetrievalIndexEmbeddingResult(
                embedding=[0.42, *([0.0] * 3071)],
                embedding_model="gemini-embedding-2",
                embedding_state="ready",
                degraded_reason=None,
            )
            for _text in texts
        ]

    monkeypatch.setattr(
        "app.modules.retrieval_core.indexing.RetrievalIndexEmbeddingService.embed_texts",
        fake_embed_texts,
    )
    db_session.add(
        _catalog_fact(
            workspace_id=workspace_id,
            fact_id="catalog_media:semantic-wallet",
            fact_type="catalog_media",
            entity_ref="product:semantic-wallet",
            value={
                "product_ref": "product:semantic-wallet",
                "media_ref": "media:semantic-wallet:hero",
                "caption": "Wallet catalog hero image",
                "ocr_text": "Emerald wallet limited offer",
                "visual_summary": "green compact wallet next to a brass key",
            },
            source_refs=["telegram_channel:@shop:778:photo"],
            status="active",
        )
    )
    await db_session.commit()

    projection = await CommerceCatalogCoreService(db_session).project_from_business_brain(
        workspace_id=workspace_id,
        rebuild_retrieval_index=True,
    )

    assert projection.media == 1
    assert projection.indexed_source_units == 1
    row = await db_session.scalar(
        select(BusinessBrainIndexRecord).where(
            BusinessBrainIndexRecord.workspace_id == workspace_id,
            BusinessBrainIndexRecord.fact_id == "catalog_media:semantic-wallet",
        )
    )
    assert row is not None
    assert row.embedding_state == "ready"
    assert row.embedding_model == "gemini-embedding-2"
    assert row.embedding is not None
    assert row.source_refs == ["telegram_channel:@shop:778:photo"]
    assert "green compact wallet" in (row.source_text or "")
    assert embedded_texts and "Emerald wallet limited offer" in embedded_texts[0]


async def test_stale_catalog_media_demotes_retrieval_index_record(
    db_session,
    monkeypatch,
):
    workspace_id = 99113

    async def fake_embed_texts(self, texts, *, enabled, context_prefix):
        assert enabled is True
        assert context_prefix == "business_brain_index"
        return [
            RetrievalIndexEmbeddingResult(
                embedding=[0.33, *([0.0] * 3071)],
                embedding_model="gemini-embedding-2",
                embedding_state="ready",
                degraded_reason=None,
            )
            for _text in texts
        ]

    monkeypatch.setattr(
        "app.modules.retrieval_core.indexing.RetrievalIndexEmbeddingService.embed_texts",
        fake_embed_texts,
    )
    media_fact = _catalog_fact(
        workspace_id=workspace_id,
        fact_id="catalog_media:stale-wallet",
        fact_type="catalog_media",
        entity_ref="product:stale-wallet",
        value={
            "product_ref": "product:stale-wallet",
            "media_ref": "media:stale-wallet:hero",
            "caption": "Stale wallet catalog hero image",
            "ocr_text": "Stale wallet 88000 UZS",
            "visual_summary": "red wallet on a marble desk",
        },
        source_refs=["telegram_channel:@shop:889:photo"],
        status="active",
    )
    db_session.add(media_fact)
    await db_session.commit()

    service = CommerceCatalogCoreService(db_session)
    await service.project_from_business_brain(
        workspace_id=workspace_id,
        rebuild_retrieval_index=True,
    )
    index_row = await db_session.scalar(
        select(BusinessBrainIndexRecord).where(
            BusinessBrainIndexRecord.workspace_id == workspace_id,
            BusinessBrainIndexRecord.fact_id == "catalog_media:stale-wallet",
        )
    )
    assert index_row is not None
    assert index_row.state == "ready"
    assert index_row.embedding_state == "ready"

    media_fact.status = "stale"
    media_fact.raw_fact = {**media_fact.raw_fact, "approval_state": "stale"}
    await db_session.commit()

    await service.project_from_business_brain(
        workspace_id=workspace_id,
        rebuild_retrieval_index=True,
    )
    stale_index_row = await db_session.scalar(
        select(BusinessBrainIndexRecord).where(
            BusinessBrainIndexRecord.workspace_id == workspace_id,
            BusinessBrainIndexRecord.fact_id == "catalog_media:stale-wallet",
        )
    )

    assert stale_index_row is not None
    assert stale_index_row.state == "stale"
    assert stale_index_row.embedding_state == "degraded"
    assert stale_index_row.embedding is None
    assert stale_index_row.degraded_reason == "catalog_authority_stale"


async def test_projects_missing_price_as_typed_catalog_missing_field(db_session):
    workspace_id = 99102
    db_session.add(
        _catalog_fact(
            workspace_id=workspace_id,
            fact_id="catalog_product:no-price",
            fact_type="catalog_product",
            entity_ref="product:no-price",
            value={"product_ref": "product:no-price", "name": "No Price Product"},
        )
    )
    await db_session.commit()

    service = CommerceCatalogCoreService(db_session)
    projection = await service.project_from_business_brain(workspace_id=workspace_id)
    result = await service.search_authority(
        workspace_id=workspace_id,
        query="no price product narxi",
    )

    assert projection.products == 1
    assert projection.missing_fields == 1
    assert [field.field for field in result.missing_fields] == ["price"]

    missing = await db_session.scalar(
        select(CatalogMissingFieldRecord).where(
            CatalogMissingFieldRecord.workspace_id == workspace_id,
            CatalogMissingFieldRecord.product_ref == "product:no-price",
            CatalogMissingFieldRecord.field == "price",
        )
    )
    assert missing is not None
    assert missing.authority_state == "candidate"


async def test_projects_conflicting_approved_offer_prices_as_typed_conflict(db_session):
    workspace_id = 99105
    db_session.add_all(
        [
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_product:conflict",
                fact_type="catalog_product",
                entity_ref="product:conflict",
                value={"product_ref": "product:conflict", "name": "Conflict Product"},
            ),
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_offer:conflict:a",
                fact_type="catalog_offer",
                entity_ref="product:conflict",
                value={
                    "offer_ref": "offer:conflict:a",
                    "product_ref": "product:conflict",
                    "price": "50000",
                    "currency": "UZS",
                },
                source_refs=["source:price-a"],
            ),
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_offer:conflict:b",
                fact_type="catalog_offer",
                entity_ref="product:conflict",
                value={
                    "offer_ref": "offer:conflict:b",
                    "product_ref": "product:conflict",
                    "price": "60000",
                    "currency": "UZS",
                },
                source_refs=["source:price-b"],
            ),
        ]
    )
    await db_session.commit()

    service = CommerceCatalogCoreService(db_session)
    projection = await service.project_from_business_brain(workspace_id=workspace_id)
    result = await service.search_authority(
        workspace_id=workspace_id,
        query="conflict product narxi",
    )

    assert projection.conflicts == 1
    assert result.telemetry.conflict_count == 1
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.product_ref == "product:conflict"
    assert conflict.field == "price"
    assert conflict.status == "open"
    assert {value["price"] for value in conflict.candidate_values} == {"50000", "60000"}
    assert conflict.source_refs == ["source:price-a", "source:price-b"]


async def test_resolving_price_conflict_demotes_losing_offer_and_stays_resolved(db_session):
    workspace_id = 99112
    db_session.add_all(
        [
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_product:resolve-conflict",
                fact_type="catalog_product",
                entity_ref="product:resolve-conflict",
                value={
                    "product_ref": "product:resolve-conflict",
                    "name": "Resolved Conflict Product",
                },
            ),
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_offer:resolve-conflict:chosen",
                fact_type="catalog_offer",
                entity_ref="product:resolve-conflict",
                value={
                    "offer_ref": "offer:resolve-conflict:chosen",
                    "product_ref": "product:resolve-conflict",
                    "price": "50000",
                    "currency": "UZS",
                },
                source_refs=["source:chosen-price"],
            ),
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_offer:resolve-conflict:losing",
                fact_type="catalog_offer",
                entity_ref="product:resolve-conflict",
                value={
                    "offer_ref": "offer:resolve-conflict:losing",
                    "product_ref": "product:resolve-conflict",
                    "price": "60000",
                    "currency": "UZS",
                },
                source_refs=["source:losing-price"],
            ),
        ]
    )
    await db_session.commit()

    service = CommerceCatalogCoreService(db_session)
    projection = await service.project_from_business_brain(workspace_id=workspace_id)
    assert projection.conflicts == 1

    resolved = await service.resolve_price_conflict(
        workspace_id=workspace_id,
        conflict_ref="catalog_conflict:product:resolve-conflict:default:price",
        winning_source_fact_id="catalog_offer:resolve-conflict:chosen",
        actor_ref="owner:test",
    )
    assert resolved.status == "resolved"
    assert resolved.resolution["winning_source_fact_id"] == "catalog_offer:resolve-conflict:chosen"
    assert resolved.resolution["actor_ref"] == "owner:test"

    projection = await service.project_from_business_brain(workspace_id=workspace_id)
    result = await service.search_authority(
        workspace_id=workspace_id,
        query="resolved conflict product narxi",
    )

    assert projection.conflicts == 0
    assert [offer.offer_ref for offer in result.offers] == ["offer:resolve-conflict:chosen"]
    assert [offer.price for offer in result.offers] == ["50000"]
    assert result.conflicts == []

    losing_offer = await db_session.scalar(
        select(CatalogOfferRecord).where(
            CatalogOfferRecord.workspace_id == workspace_id,
            CatalogOfferRecord.offer_ref == "offer:resolve-conflict:losing",
        )
    )
    assert losing_offer is not None
    assert losing_offer.authority_state == "stale"

    losing_source = await db_session.scalar(
        select(CatalogSourceFactRecord).where(
            CatalogSourceFactRecord.workspace_id == workspace_id,
            CatalogSourceFactRecord.source_fact_id == "catalog_offer:resolve-conflict:losing",
        )
    )
    assert losing_source is not None
    assert losing_source.authority_state == "stale"


async def test_resolving_stock_conflict_demotes_losing_offer_and_stays_resolved(db_session):
    workspace_id = 99113
    db_session.add_all(
        [
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_product:stock-conflict",
                fact_type="catalog_product",
                entity_ref="product:stock-conflict",
                value={
                    "product_ref": "product:stock-conflict",
                    "name": "Stock Conflict Product",
                },
            ),
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_offer:stock-conflict:chosen",
                fact_type="catalog_offer",
                entity_ref="product:stock-conflict",
                value={
                    "offer_ref": "offer:stock-conflict:chosen",
                    "product_ref": "product:stock-conflict",
                    "price": "50000",
                    "currency": "UZS",
                    "stock_state": "available",
                },
                source_refs=["source:stock-available"],
            ),
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_offer:stock-conflict:losing",
                fact_type="catalog_offer",
                entity_ref="product:stock-conflict",
                value={
                    "offer_ref": "offer:stock-conflict:losing",
                    "product_ref": "product:stock-conflict",
                    "price": "50000",
                    "currency": "UZS",
                    "stock_state": "out_of_stock",
                },
                source_refs=["source:stock-empty"],
            ),
        ]
    )
    await db_session.commit()

    service = CommerceCatalogCoreService(db_session)
    projection = await service.project_from_business_brain(workspace_id=workspace_id)
    result = await service.search_authority(
        workspace_id=workspace_id,
        query="stock conflict product bormi",
    )

    assert projection.conflicts == 1
    assert [(conflict.field, conflict.status) for conflict in result.conflicts] == [
        ("stock_state", "open")
    ]
    conflict = result.conflicts[0]
    assert {value["stock_state"] for value in conflict.candidate_values} == {
        "available",
        "out_of_stock",
    }

    resolved = await service.resolve_catalog_conflict(
        workspace_id=workspace_id,
        conflict_ref="catalog_conflict:product:stock-conflict:default:stock_state",
        winning_source_fact_id="catalog_offer:stock-conflict:chosen",
        actor_ref="owner:test",
    )
    assert resolved.status == "resolved"
    assert resolved.field == "stock_state"

    projection = await service.project_from_business_brain(workspace_id=workspace_id)
    result = await service.search_authority(
        workspace_id=workspace_id,
        query="stock conflict product bormi",
    )

    assert projection.conflicts == 0
    assert result.conflicts == []
    assert [offer.offer_ref for offer in result.offers] == [
        "offer:stock-conflict:chosen"
    ]
    assert [offer.stock_state for offer in result.offers] == ["available"]

    losing_offer = await db_session.scalar(
        select(CatalogOfferRecord).where(
            CatalogOfferRecord.workspace_id == workspace_id,
            CatalogOfferRecord.offer_ref == "offer:stock-conflict:losing",
        )
    )
    assert losing_offer is not None
    assert losing_offer.authority_state == "stale"

    losing_source = await db_session.scalar(
        select(CatalogSourceFactRecord).where(
            CatalogSourceFactRecord.workspace_id == workspace_id,
            CatalogSourceFactRecord.source_fact_id
            == "catalog_offer:stock-conflict:losing",
        )
    )
    assert losing_source is not None
    assert losing_source.authority_state == "stale"


async def test_stale_source_offer_demotes_typed_offer_authority(db_session):
    workspace_id = 99106
    offer_fact = _catalog_fact(
        workspace_id=workspace_id,
        fact_id="catalog_offer:edit-price:old",
        fact_type="catalog_offer",
        entity_ref="product:edit-price",
        value={
            "offer_ref": "offer:edit-price:old",
            "product_ref": "product:edit-price",
            "price": "50000",
            "currency": "UZS",
        },
        source_refs=["telegram_channel:post:old-price"],
    )
    db_session.add_all(
        [
            _catalog_fact(
                workspace_id=workspace_id,
                fact_id="catalog_product:edit-price",
                fact_type="catalog_product",
                entity_ref="product:edit-price",
                value={"product_ref": "product:edit-price", "name": "Edited Price Product"},
            ),
            offer_fact,
        ]
    )
    await db_session.commit()

    service = CommerceCatalogCoreService(db_session)
    await service.project_from_business_brain(workspace_id=workspace_id)

    before = await service.search_authority(workspace_id=workspace_id, query="edited price product narxi")
    assert [offer.price for offer in before.offers] == ["50000"]
    assert before.missing_fields == []

    offer_fact.status = "stale"
    offer_fact.raw_fact = {
        **offer_fact.raw_fact,
        "approval_state": "stale",
    }
    await db_session.commit()

    projection = await service.project_from_business_brain(workspace_id=workspace_id)
    after = await service.search_authority(workspace_id=workspace_id, query="edited price product narxi")

    assert projection.missing_fields == 1
    assert after.offers == []
    assert [field.field for field in after.missing_fields] == ["price"]

    typed_offer = await db_session.scalar(
        select(CatalogOfferRecord).where(
            CatalogOfferRecord.workspace_id == workspace_id,
            CatalogOfferRecord.offer_ref == "offer:edit-price:old",
        )
    )
    assert typed_offer is not None
    assert typed_offer.authority_state == "stale"

    source_fact = await db_session.scalar(
        select(CatalogSourceFactRecord).where(
            CatalogSourceFactRecord.workspace_id == workspace_id,
            CatalogSourceFactRecord.source_fact_id == "catalog_offer:edit-price:old",
        )
    )
    assert source_fact is not None
    assert source_fact.authority_state == "stale"


async def test_knowledge_mcp_catalog_search_reads_typed_catalog_authority(db_session):
    workspace_id = 99103
    db_session.add_all(
        [
            CatalogProductRecord(
                workspace_id=workspace_id,
                product_ref="product:typed-media",
                name="Typed Media Product",
                aliases=["typed-media"],
                description="A product that exists only in typed catalog core.",
                authority_state="approved",
                source_refs=["source:typed:product"],
                source_fact_ids=["catalog_product:typed-media"],
            ),
            CatalogOfferRecord(
                workspace_id=workspace_id,
                offer_ref="offer:typed-media",
                product_ref="product:typed-media",
                price="120000",
                currency="UZS",
                stock_state="available",
                authority_state="approved",
                source_refs=["source:typed:offer"],
                source_fact_ids=["catalog_offer:typed-media"],
            ),
            CatalogMediaRecord(
                workspace_id=workspace_id,
                media_ref="media:typed-media",
                product_ref="product:typed-media",
                url="https://example.test/typed-media.webp",
                caption="Typed media product image",
                authority_state="approved",
                source_refs=["source:typed:media"],
                source_fact_ids=["catalog_media:typed-media"],
            ),
        ]
    )
    await db_session.commit()

    result = await KnowledgeMCPService(db_session).search_catalog(
        KnowledgeCatalogSearchRequest(
            workspace_id=workspace_id,
            query="typed media product narxi rasmi",
            include_media=True,
            enable_semantic=False,
            enable_rerank=False,
        )
    )

    assert [hit.item.metadata["fact_type"] for hit in result.hits] == [
        "catalog_product",
        "catalog_offer",
        "catalog_media",
    ]
    assert result.hits[0].item.metadata["catalog_core"] == "typed"
    assert result.hits[1].item.body_text == "120000 UZS"
    assert result.hits[2].item.kind == "media"


async def test_catalog_authority_search_matches_approved_media_ocr_and_visual_summary(db_session):
    workspace_id = 99107
    db_session.add_all(
        [
            CatalogProductRecord(
                workspace_id=workspace_id,
                product_ref="product:silent-sku",
                name="SKU 17",
                aliases=[],
                description="",
                authority_state="approved",
                source_refs=["source:silent:product"],
                source_fact_ids=["catalog_product:silent-sku"],
            ),
            CatalogProductRecord(
                workspace_id=workspace_id,
                product_ref="product:other-sku",
                name="SKU 18",
                aliases=[],
                description="",
                authority_state="approved",
                source_refs=["source:other:product"],
                source_fact_ids=["catalog_product:other-sku"],
            ),
            CatalogOfferRecord(
                workspace_id=workspace_id,
                offer_ref="offer:silent-sku",
                product_ref="product:silent-sku",
                price="91000",
                currency="UZS",
                stock_state="available",
                authority_state="approved",
                source_refs=["source:silent:offer"],
                source_fact_ids=["catalog_offer:silent-sku"],
            ),
            CatalogMediaRecord(
                workspace_id=workspace_id,
                media_ref="media:silent-sku:hero",
                product_ref="product:silent-sku",
                url="https://example.test/silent-sku.webp",
                caption="",
                ocr_text="waterproof cobalt pouch",
                visual_summary="front-facing blue travel pouch with zipper",
                authority_state="approved",
                source_refs=["source:silent:media"],
                source_fact_ids=["catalog_media:silent-sku"],
            ),
        ]
    )
    await db_session.commit()

    result = await CommerceCatalogCoreService(db_session).search_authority(
        workspace_id=workspace_id,
        query="blue travel pouch image",
        include_media=True,
    )

    assert [product.product_ref for product in result.products] == ["product:silent-sku"]
    assert [offer.price for offer in result.offers] == ["91000"]
    assert [media.media_ref for media in result.media] == ["media:silent-sku:hero"]


async def test_knowledge_mcp_catalog_search_reads_media_matched_typed_catalog_authority(db_session):
    workspace_id = 99108
    db_session.add_all(
        [
            CatalogProductRecord(
                workspace_id=workspace_id,
                product_ref="product:visual-only",
                name="SKU 29",
                aliases=[],
                description="",
                authority_state="approved",
                source_refs=["source:visual:product"],
                source_fact_ids=["catalog_product:visual-only"],
            ),
            CatalogProductRecord(
                workspace_id=workspace_id,
                product_ref="product:visual-decoy",
                name="SKU 30",
                aliases=[],
                description="",
                authority_state="approved",
                source_refs=["source:visual-decoy:product"],
                source_fact_ids=["catalog_product:visual-decoy"],
            ),
            CatalogOfferRecord(
                workspace_id=workspace_id,
                offer_ref="offer:visual-only",
                product_ref="product:visual-only",
                price="144000",
                currency="UZS",
                stock_state="available",
                authority_state="approved",
                source_refs=["source:visual:offer"],
                source_fact_ids=["catalog_offer:visual-only"],
            ),
            CatalogMediaRecord(
                workspace_id=workspace_id,
                media_ref="media:visual-only:hero",
                product_ref="product:visual-only",
                url="https://example.test/visual-only.webp",
                caption="",
                ocr_text="emerald compact wallet",
                visual_summary="green wallet photographed next to a brass key",
                authority_state="approved",
                source_refs=["source:visual:media"],
                source_fact_ids=["catalog_media:visual-only"],
            ),
        ]
    )
    await db_session.commit()

    result = await KnowledgeMCPService(db_session).search_catalog(
        KnowledgeCatalogSearchRequest(
            workspace_id=workspace_id,
            query="green wallet photo",
            include_media=True,
            enable_semantic=False,
            enable_rerank=False,
        )
    )

    assert [hit.item.metadata["fact_type"] for hit in result.hits] == [
        "catalog_product",
        "catalog_offer",
        "catalog_media",
    ]
    assert result.hits[0].item.metadata["product_ref"] == "product:visual-only"
    assert result.hits[1].item.body_text == "144000 UZS"
    assert result.hits[2].item.metadata["media_ref"] == "media:visual-only:hero"


async def test_approved_knowledge_catalog_candidate_projects_to_typed_catalog(
    db_session,
    workspace,
):
    workspace_id = workspace.id
    knowledge = KnowledgeMCPService(db_session)
    scope = KnowledgeScope(
        owner_type="workspace",
        owner_id=f"workspace:{workspace_id}",
        workspace_id=workspace_id,
    )
    source_doc = await knowledge.save_item(
        KnowledgeSaveInput(
            scope=scope,
            kind="source",
            title="Owner pasted catalog note",
            body_text="Phase 5 Pack is a source-only catalog note.",
            tags=["catalog", "source"],
            authority_state="source",
            visibility="workspace",
            created_by="agent",
            created_by_ref="agent:catalog-update",
            source_kind="paste",
            correlation_id="corr-catalog-candidate-source",
            idempotency_key="catalog-candidate-source-1",
        )
    )
    proposal = await knowledge.propose_candidate(
        KnowledgeCandidateInput(
            scope=scope,
            source_id=source_doc.source_refs[0],
            proposed_kind="catalog_product",
            proposed_payload={
                "product_ref": "product:phase-5-pack",
                "name": "Phase 5 Pack",
                "aliases": ["phase pack"],
                "description": "Approved only after owner review.",
            },
            evidence_refs=source_doc.source_refs,
            confidence=0.93,
            created_by_ref="agent:catalog-update",
            correlation_id="corr-catalog-candidate",
            idempotency_key="catalog-candidate-product-1",
        )
    )
    catalog = CommerceCatalogCoreService(db_session)

    before = await catalog.search_authority(
        workspace_id=workspace_id,
        query="phase 5 pack",
    )
    assert before.products == []

    await knowledge.approve_candidate_action(
        workspace_id=workspace_id,
        action_id=proposal.action.action_id,
        actor_ref="owner:42",
        correlation_id="corr-catalog-candidate-approved",
    )

    after = await catalog.search_authority(
        workspace_id=workspace_id,
        query="phase 5 pack",
    )
    assert [product.product_ref for product in after.products] == ["product:phase-5-pack"]
    assert after.products[0].authority_state == "approved"
    assert after.products[0].source_refs == source_doc.source_refs
    assert after.telemetry.source_fact_count == 1

    fact = await db_session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace_id,
            BusinessBrainFactRecord.fact_id == "product:phase-5-pack",
        )
    )
    assert fact is not None
    assert fact.fact_type == "catalog_product"
    assert fact.value["product_ref"] == "product:phase-5-pack"
