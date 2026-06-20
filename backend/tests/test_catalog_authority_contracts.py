from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.catalog_authority.contracts import (
    CatalogAuthorityBundle,
    CatalogAuthorityOffer,
    CatalogAuthorityProduct,
    CatalogAuthorityWarning,
)


def test_bundle_round_trips_and_renders_approved_lines():
    bundle = CatalogAuthorityBundle(
        query="starter coins narxi",
        products=[CatalogAuthorityProduct(fact_id="1", title="Starter Coins", authority_state="approved")],
        offers=[CatalogAuthorityOffer(fact_id="2", product_title="Starter Coins", price="50000 UZS", authority_state="approved")],
        warnings=[CatalogAuthorityWarning(code="missing_field", field="price")],
        missing_fields=["price"],
        source_refs=["catalog_offer:2"],
        authority_states=["approved"],
    )
    restored = CatalogAuthorityBundle.model_validate(bundle.model_dump(mode="json"))
    assert restored.offers[0].price == "50000 UZS"
    lines = bundle.approved_authority_lines()
    assert any("Starter Coins" in line for line in lines)


def test_approved_authority_lines_do_not_render_source_unit_wrappers():
    wrapper = (
        "Contextual source unit Fact type: catalog_offer "
        "Evidence text: Fact type: catalog_offer Entity: starter "
        '{"summary": "debug wrapper"} Source refs: message:1'
    )
    bundle = CatalogAuthorityBundle(
        query="starter coins narxi",
        products=[
            CatalogAuthorityProduct(
                fact_id="1",
                title="Starter Coins",
                authority_state="approved",
            )
        ],
        offers=[
            CatalogAuthorityOffer(
                fact_id="2",
                product_title="Starter Coins",
                price=wrapper,
                authority_state="approved",
            )
        ],
        source_refs=["catalog_offer:2"],
        authority_states=["approved"],
    )

    rendered = "\n".join(bundle.approved_authority_lines())
    assert "Contextual source unit" not in rendered
    assert "debug wrapper" not in rendered
    assert "[OFFER] Starter Coins" in rendered


def test_authority_state_is_constrained():
    with pytest.raises(ValidationError):
        CatalogAuthorityProduct(title="x", authority_state="bogus")
