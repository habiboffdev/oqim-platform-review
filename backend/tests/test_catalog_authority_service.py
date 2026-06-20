from __future__ import annotations

import pytest

from app.models.commerce_catalog import (
    CatalogConflictRecord,
    CatalogMissingFieldRecord,
    CatalogOfferRecord,
    CatalogProductRecord,
)
from app.modules.catalog_authority.contracts import CatalogAuthorityBundle
from app.modules.catalog_authority.service import CatalogAuthorityService
from app.modules.knowledge_mcp.contracts import (
    KnowledgeItem,
    KnowledgeScope,
    KnowledgeSearchHit,
    KnowledgeSearchResult,
)

pytestmark = pytest.mark.asyncio


def _hit(fact_type: str, *, fact_id: str, title: str, value: dict, body: str = "") -> KnowledgeSearchHit:
    item = KnowledgeItem(
        item_id=fact_id,
        scope=KnowledgeScope(owner_type="workspace", owner_id="1", workspace_id=1),
        kind="catalog",
        title=title,
        body_text=body,
        source_refs=[f"{fact_type}:{fact_id}"],
        authority_state="approved",
        visibility="workspace",
        created_by="system",
        created_by_ref=f"catalog_fact:{fact_id}",
        metadata={"fact_type": fact_type, "value": value},
    )
    return KnowledgeSearchHit(item=item, score=1.0)


class _FakeKnowledge:
    def __init__(self, hits):
        self._hits = hits
        self.last_request = None

    async def search_catalog(self, request):
        self.last_request = request
        return KnowledgeSearchResult(hits=self._hits)


async def test_resolve_maps_approved_product_and_offer():
    knowledge = _FakeKnowledge([
        _hit("catalog_product", fact_id="1", title="Starter Coins", value={"name": "Starter Coins"}),
        # Production stores price text in body_text, NOT metadata["value"] — empty
        # value dict here proves the body_text fallback path.
        _hit("catalog_offer", fact_id="2", title="Starter Coins narx", value={}, body="50000 UZS"),
    ])
    service = CatalogAuthorityService(session=None, knowledge=knowledge)
    bundle = await service.resolve(workspace_id=1, query="starter coins narxi")
    assert isinstance(bundle, CatalogAuthorityBundle)
    assert bundle.products[0].title == "Starter Coins"
    assert bundle.offers[0].authority_state == "approved"
    assert bundle.offers[0].price == "50000 UZS"  # pulled from body_text fallback
    assert "price" not in bundle.missing_fields
    assert knowledge.last_request.workspace_id == 1


async def test_resolve_flags_missing_price_when_product_has_no_offer():
    knowledge = _FakeKnowledge([
        _hit("catalog_product", fact_id="1", title="Starter Coins", value={"name": "Starter Coins"}),
    ])
    service = CatalogAuthorityService(session=None, knowledge=knowledge)
    bundle = await service.resolve(workspace_id=1, query="starter coins narxi")
    assert "price" in bundle.missing_fields
    assert any(w.code == "missing_field" and w.field == "price" for w in bundle.warnings)


async def test_resolve_does_not_treat_contextual_source_unit_as_offer_price():
    knowledge = _FakeKnowledge(
        [
            _hit(
                "catalog_product",
                fact_id="1",
                title="Starter Coins",
                value={"name": "Starter Coins"},
            ),
            _hit(
                "catalog_offer",
                fact_id="2",
                title="Starter Coins offer",
                value={},
                body=(
                    "Contextual source unit\n"
                    "Fact type: catalog_offer\n"
                    "Entity ref: catalog:satstation:starter-coins\n"
                    "Fact ref: catalog_offer:sat"
                ),
            ),
        ]
    )
    service = CatalogAuthorityService(session=None, knowledge=knowledge)

    bundle = await service.resolve(workspace_id=1, query="what is price")

    assert bundle.offers[0].price is None
    assert "price" in bundle.missing_fields
    rendered = "\n".join(bundle.approved_authority_lines())
    assert "Contextual source unit" not in rendered
    assert "[OFFER] Starter Coins offer" in rendered


async def test_resolve_prefers_typed_catalog_core_over_knowledge_fallback(db_session):
    workspace_id = 99201
    db_session.add_all(
        [
            CatalogProductRecord(
                workspace_id=workspace_id,
                product_ref="product:typed-starter",
                name="Typed Starter Coins",
                aliases=["typed starter"],
                description="Approved typed catalog product",
                authority_state="approved",
                source_refs=["typed:product"],
                source_fact_ids=["catalog_product:typed-starter"],
            ),
            CatalogOfferRecord(
                workspace_id=workspace_id,
                offer_ref="offer:typed-starter",
                product_ref="product:typed-starter",
                price="75000",
                currency="UZS",
                stock_state="available",
                authority_state="approved",
                source_refs=["typed:offer"],
                source_fact_ids=["catalog_offer:typed-starter"],
            ),
        ]
    )
    await db_session.commit()
    knowledge = _FakeKnowledge(
        [
            _hit(
                "catalog_offer",
                fact_id="generic-wrong",
                title="Generic fallback",
                value={"price": "1", "currency": "UZS"},
            )
        ]
    )

    service = CatalogAuthorityService(session=db_session, knowledge=knowledge)
    bundle = await service.resolve(workspace_id=workspace_id, query="typed starter coins narxi")

    assert bundle.products[0].title == "Typed Starter Coins"
    assert bundle.offers[0].price == "75000"
    assert knowledge.last_request is None


async def test_resolve_surfaces_typed_catalog_conflicts_as_authority_warnings(db_session):
    workspace_id = 99202
    db_session.add_all(
        [
            CatalogProductRecord(
                workspace_id=workspace_id,
                product_ref="product:typed-conflict",
                name="Typed Conflict Product",
                aliases=["typed conflict"],
                description="Approved typed catalog product with price conflict",
                authority_state="approved",
                source_refs=["typed:product"],
                source_fact_ids=["catalog_product:typed-conflict"],
            ),
            CatalogOfferRecord(
                workspace_id=workspace_id,
                offer_ref="offer:typed-conflict:a",
                product_ref="product:typed-conflict",
                price="50000",
                currency="UZS",
                stock_state="available",
                authority_state="approved",
                source_refs=["typed:offer:a"],
                source_fact_ids=["catalog_offer:typed-conflict:a"],
            ),
            CatalogConflictRecord(
                workspace_id=workspace_id,
                conflict_ref="catalog_conflict:typed-conflict:price",
                product_ref="product:typed-conflict",
                field="price",
                candidate_values=[
                    {"price": "50000", "currency": "UZS", "source_refs": ["typed:offer:a"]},
                    {"price": "60000", "currency": "UZS", "source_refs": ["typed:offer:b"]},
                ],
                source_refs=["typed:offer:a", "typed:offer:b"],
                status="open",
            ),
        ]
    )
    await db_session.commit()

    service = CatalogAuthorityService(session=db_session, knowledge=_FakeKnowledge([]))
    bundle = await service.resolve(workspace_id=workspace_id, query="typed conflict narxi")

    assert any(
        warning.code == "conflict" and warning.field == "price"
        for warning in bundle.warnings
    )
    assert "typed:offer:b" in bundle.source_refs


async def test_resolve_scopes_platform_price_query_away_from_related_starter_offer(db_session):
    workspace_id = 99203
    db_session.add_all(
        [
            CatalogProductRecord(
                workspace_id=workspace_id,
                product_ref="catalog:satstation:platform",
                name="SATStation Digital SAT Prep Platform",
                aliases=["satstation platform", "digital sat platform"],
                description="Students prepare for Digital SAT with practice tests and score tracking.",
                authority_state="approved",
                source_refs=["typed:platform"],
                source_fact_ids=["catalog_product:platform"],
            ),
            CatalogProductRecord(
                workspace_id=workspace_id,
                product_ref="catalog:satstation:starter-coins",
                name="Starter coins",
                aliases=["starter coins", "starter coin", "starter"],
                description="SATStation starter coin package.",
                authority_state="approved",
                source_refs=["typed:starter"],
                source_fact_ids=["catalog_product:starter"],
            ),
            CatalogOfferRecord(
                workspace_id=workspace_id,
                offer_ref="catalog_offer:satstation:starter-coins",
                product_ref="catalog:satstation:starter-coins",
                price="40 000",
                currency="UZS",
                stock_state="available",
                availability="available",
                authority_state="approved",
                source_refs=["typed:starter-offer"],
                source_fact_ids=["catalog_offer:starter"],
            ),
            CatalogMissingFieldRecord(
                workspace_id=workspace_id,
                product_ref="catalog:satstation:platform",
                field="price",
                authority_state="candidate",
                source_refs=["typed:platform"],
            ),
        ]
    )
    await db_session.commit()

    service = CatalogAuthorityService(session=db_session, knowledge=_FakeKnowledge([]))
    bundle = await service.resolve(workspace_id=workspace_id, query="satstation platform narxi")

    assert [product.title for product in bundle.products] == [
        "SATStation Digital SAT Prep Platform"
    ]
    assert bundle.offers == []
    assert bundle.missing_fields == ["price"]
    assert "Starter coins" not in "\n".join(bundle.approved_authority_lines())
