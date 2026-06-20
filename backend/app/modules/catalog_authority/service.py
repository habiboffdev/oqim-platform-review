from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog_authority.contracts import (
    CatalogAuthorityBundle,
    CatalogAuthorityMedia,
    CatalogAuthorityOffer,
    CatalogAuthorityProduct,
    CatalogAuthorityWarning,
)
from app.modules.commerce_catalog.service import CommerceCatalogCoreService
from app.modules.knowledge_mcp.contracts import KnowledgeCatalogSearchRequest
from app.modules.knowledge_mcp.service import KnowledgeMCPService

_PRODUCT_FACT_TYPES = {"catalog_product", "catalog_variant"}


def _coerce_state(value: str) -> str:
    return value if value in {"approved", "candidate", "source"} else "source"


def _value_str(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        raw = value.get(key)
        if raw not in (None, ""):
            return str(raw)
    return None


def _offer_price(item: Any, value: dict[str, Any]) -> str | None:
    """Prefer the typed value dict (tests / 5b enrichment); fall back to the
    rendered body_text the production projection actually stores."""
    typed = _value_str(value, "price", "amount", "narx")
    if typed is not None:
        return typed
    body = str(getattr(item, "body_text", "") or "").strip()
    if _is_contextual_source_unit(body):
        return None
    return body[:120] or None


def _is_contextual_source_unit(text: str) -> bool:
    return text.strip().startswith("Contextual source unit")


class CatalogAuthorityService:
    """Stable catalog-authority boundary. Internals NOW query the
    business_brain_facts store via KnowledgeMCPService.search_catalog (which
    returns only approved authority, include_proposed=False). Phase 5b swaps
    internals to typed catalog tables without changing callers."""

    def __init__(
        self,
        session: AsyncSession | None,
        *,
        knowledge: Any | None = None,
        catalog_core: CommerceCatalogCoreService | None = None,
    ) -> None:
        self._session = session
        self._knowledge = knowledge or (KnowledgeMCPService(session) if session is not None else None)
        self._catalog_core = catalog_core or (
            CommerceCatalogCoreService(session) if session is not None else None
        )

    async def resolve(self, *, workspace_id: int, query: str, limit: int = 8) -> CatalogAuthorityBundle:
        if self._catalog_core is not None:
            typed = await self._catalog_core.search_authority(
                workspace_id=workspace_id,
                query=query,
                include_media=True,
                limit=limit,
            )
            if (
                typed.products
                or typed.offers
                or typed.media
                or typed.missing_fields
                or typed.conflicts
                or typed.source_refs
            ):
                product_titles = {product.product_ref: product.name for product in typed.products}
                states = {
                    _coerce_state(item.authority_state)
                    for collection in (typed.products, typed.offers, typed.media, typed.missing_fields)
                    for item in collection
                }
                missing_fields = [field.field for field in typed.missing_fields]
                return CatalogAuthorityBundle(
                    query=query,
                    products=[
                        CatalogAuthorityProduct(
                            fact_id=(product.source_fact_ids[0] if product.source_fact_ids else product.product_ref),
                            title=product.name,
                            authority_state=_coerce_state(product.authority_state),  # type: ignore[arg-type]
                            source_refs=product.source_refs,
                        )
                        for product in typed.products
                    ],
                    offers=[
                        CatalogAuthorityOffer(
                            fact_id=(offer.source_fact_ids[0] if offer.source_fact_ids else offer.offer_ref),
                            product_title=product_titles.get(offer.product_ref, offer.product_ref),
                            price=offer.price,
                            currency=offer.currency,
                            stock_state=offer.stock_state or offer.availability,
                            authority_state=_coerce_state(offer.authority_state),  # type: ignore[arg-type]
                            source_refs=offer.source_refs,
                        )
                        for offer in typed.offers
                    ],
                    media=[
                        CatalogAuthorityMedia(
                            media_ref=media.media_ref,
                            product_title=product_titles.get(media.product_ref, media.product_ref),
                            url=media.url,
                            caption=media.caption,
                            ocr_text=media.ocr_text,
                            visual_summary=media.visual_summary,
                            authority_state=_coerce_state(media.authority_state),  # type: ignore[arg-type]
                            source_refs=media.source_refs,
                        )
                        for media in typed.media
                    ],
                    warnings=[
                        *[
                            CatalogAuthorityWarning(code="missing_field", field=field)
                            for field in missing_fields
                        ],
                        *[
                            CatalogAuthorityWarning(
                                code="conflict",
                                field=conflict.field,
                                detail=conflict.conflict_ref,
                            )
                            for conflict in typed.conflicts
                        ],
                    ],
                    missing_fields=missing_fields,
                    source_refs=typed.source_refs,
                    authority_states=sorted(states),
                )
        if self._knowledge is None:
            return CatalogAuthorityBundle(query=query)
        result = await self._knowledge.search_catalog(
            KnowledgeCatalogSearchRequest(
                workspace_id=workspace_id,
                query=query,
                include_media=False,
                limit=limit,
            )
        )
        products: list[CatalogAuthorityProduct] = []
        offers: list[CatalogAuthorityOffer] = []
        source_refs: list[str] = []
        states: set[str] = set()
        for hit in result.hits:
            item = hit.item
            metadata = item.metadata or {}
            fact_type = str(metadata.get("fact_type") or "")
            value = metadata.get("value") if isinstance(metadata.get("value"), dict) else {}
            state = _coerce_state(item.authority_state)
            states.add(state)
            source_refs.extend(item.source_refs)
            if fact_type == "catalog_offer":
                offers.append(
                    CatalogAuthorityOffer(
                        fact_id=item.item_id or None,
                        product_title=item.title,
                        price=_offer_price(item, value),
                        currency=_value_str(value, "currency"),
                        stock_state=_value_str(value, "stock", "availability"),
                        authority_state=state,  # type: ignore[arg-type]
                        source_refs=list(item.source_refs),
                    )
                )
            elif fact_type in _PRODUCT_FACT_TYPES:
                products.append(
                    CatalogAuthorityProduct(
                        fact_id=item.item_id or None,
                        title=item.title,
                        authority_state=state,  # type: ignore[arg-type]
                        source_refs=list(item.source_refs),
                    )
                )
        warnings: list[CatalogAuthorityWarning] = []
        missing_fields: list[str] = []
        if any(p.authority_state == "approved" for p in products) and not any(
            _has_usable_offer_detail(o) for o in offers if o.authority_state == "approved"
        ):
            missing_fields.append("price")
            warnings.append(CatalogAuthorityWarning(code="missing_field", field="price"))
        return CatalogAuthorityBundle(
            query=query,
            products=products,
            offers=offers,
            warnings=warnings,
            missing_fields=missing_fields,
            source_refs=list(dict.fromkeys(source_refs)),
            authority_states=sorted(states),
        )


def _has_usable_offer_detail(offer: CatalogAuthorityOffer) -> bool:
    return bool((offer.price or "").strip() or (offer.stock_state or "").strip())
