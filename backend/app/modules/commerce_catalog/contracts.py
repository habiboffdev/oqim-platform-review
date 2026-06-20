from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CatalogAuthorityState = Literal["approved", "candidate", "source", "stale"]


class _CommerceCatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommerceCatalogProduct(_CommerceCatalogModel):
    product_ref: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    authority_state: CatalogAuthorityState
    source_refs: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)


class CommerceCatalogOffer(_CommerceCatalogModel):
    offer_ref: str = Field(min_length=1)
    product_ref: str = Field(min_length=1)
    variant_ref: str | None = None
    price: str | None = None
    currency: str | None = None
    stock_state: str | None = None
    availability: str | None = None
    authority_state: CatalogAuthorityState
    source_refs: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)


class CommerceCatalogMedia(_CommerceCatalogModel):
    media_ref: str = Field(min_length=1)
    product_ref: str = Field(min_length=1)
    media_kind: str = "image"
    url: str | None = None
    caption: str = ""
    ocr_text: str = ""
    visual_summary: str = ""
    authority_state: CatalogAuthorityState
    source_refs: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)


class CommerceCatalogMissingField(_CommerceCatalogModel):
    product_ref: str = Field(min_length=1)
    field: str = Field(min_length=1)
    authority_state: CatalogAuthorityState = "candidate"
    source_refs: list[str] = Field(default_factory=list)


class CommerceCatalogConflict(_CommerceCatalogModel):
    conflict_ref: str = Field(min_length=1)
    product_ref: str = Field(min_length=1)
    field: str = Field(min_length=1)
    candidate_values: list[dict] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    status: str = "open"
    resolution: dict = Field(default_factory=dict)


class CommerceCatalogTelemetry(_CommerceCatalogModel):
    source_fact_count: int = 0
    product_count: int = 0
    offer_count: int = 0
    media_count: int = 0
    missing_field_count: int = 0
    conflict_count: int = 0
    search_latency_ms: int = 0


class CommerceCatalogSearchResult(_CommerceCatalogModel):
    schema_version: Literal["commerce_catalog_search_result.v1"] = (
        "commerce_catalog_search_result.v1"
    )
    query: str
    products: list[CommerceCatalogProduct] = Field(default_factory=list)
    offers: list[CommerceCatalogOffer] = Field(default_factory=list)
    media: list[CommerceCatalogMedia] = Field(default_factory=list)
    missing_fields: list[CommerceCatalogMissingField] = Field(default_factory=list)
    conflicts: list[CommerceCatalogConflict] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    telemetry: CommerceCatalogTelemetry = Field(default_factory=CommerceCatalogTelemetry)


class CommerceCatalogProjectionReport(_CommerceCatalogModel):
    schema_version: Literal["commerce_catalog_projection_report.v1"] = (
        "commerce_catalog_projection_report.v1"
    )
    workspace_id: int
    source_facts: int = 0
    products: int = 0
    variants: int = 0
    offers: int = 0
    media: int = 0
    missing_fields: int = 0
    conflicts: int = 0
    indexed_source_units: int = 0
