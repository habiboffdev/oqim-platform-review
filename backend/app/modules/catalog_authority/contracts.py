from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CatalogAuthorityState = Literal["approved", "candidate", "source"]


class _CatalogAuthorityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogAuthorityProduct(_CatalogAuthorityModel):
    fact_id: str | None = None
    title: str
    authority_state: CatalogAuthorityState
    source_refs: list[str] = Field(default_factory=list)


class CatalogAuthorityOffer(_CatalogAuthorityModel):
    fact_id: str | None = None
    product_title: str | None = None
    price: str | None = None
    currency: str | None = None
    stock_state: str | None = None
    authority_state: CatalogAuthorityState
    source_refs: list[str] = Field(default_factory=list)


class CatalogAuthorityMedia(_CatalogAuthorityModel):
    media_ref: str
    product_title: str | None = None
    url: str | None = None
    caption: str = ""
    ocr_text: str = ""
    visual_summary: str = ""
    authority_state: CatalogAuthorityState
    source_refs: list[str] = Field(default_factory=list)


class CatalogAuthorityWarning(_CatalogAuthorityModel):
    code: str
    field: str | None = None
    detail: str | None = None


class CatalogAuthorityBundle(_CatalogAuthorityModel):
    schema_version: Literal["catalog_authority_bundle.v1"] = "catalog_authority_bundle.v1"
    query: str
    products: list[CatalogAuthorityProduct] = Field(default_factory=list)
    offers: list[CatalogAuthorityOffer] = Field(default_factory=list)
    media: list[CatalogAuthorityMedia] = Field(default_factory=list)
    warnings: list[CatalogAuthorityWarning] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    authority_states: list[CatalogAuthorityState] = Field(default_factory=list)

    def approved_authority_lines(self) -> list[str]:
        """Render only APPROVED products/offers as compact judge/grounding lines."""
        lines: list[str] = []
        for product in self.products:
            if product.authority_state == "approved":
                lines.append(f"[PRODUCT] {product.title}")
        for offer in self.offers:
            if offer.authority_state == "approved":
                price = " ".join(
                    p for p in [_prompt_safe(offer.price), _prompt_safe(offer.currency)] if p
                )
                label = offer.product_title or "offer"
                detail = price or (_prompt_safe(offer.stock_state) or "")
                lines.append(f"[OFFER] {label}: {detail}" if detail else f"[OFFER] {label}")
        for media in self.media:
            if media.authority_state == "approved":
                label = media.product_title or media.media_ref
                detail = (
                    _prompt_safe(media.caption)
                    or _prompt_safe(media.visual_summary)
                    or _prompt_safe(media.ocr_text)
                )
                lines.append(f"[MEDIA] {label}: {detail}" if detail else f"[MEDIA] {label}")
        return lines


def _prompt_safe(value: str | None) -> str:
    text = (value or "").strip()
    if "Contextual source unit" in text:
        return ""
    return text
