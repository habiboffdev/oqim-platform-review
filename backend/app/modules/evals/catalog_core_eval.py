from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commercial_spine import BusinessBrainFactRecord, BusinessBrainIndexRecord
from app.modules.commerce_catalog.service import CommerceCatalogCoreService


@dataclass(frozen=True, slots=True)
class CatalogCoreEvalCase:
    case_id: str
    description: str


class CatalogCoreEvalCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class CatalogCoreEvalResult(BaseModel):
    case_id: str
    description: str
    passed: bool
    product_count: int = Field(ge=0)
    offer_count: int = Field(ge=0)
    media_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    resolved_conflict_count: int = Field(ge=0, default=0)
    missing_field_count: int = Field(ge=0)
    indexed_source_unit_count: int = Field(ge=0)
    stale_index_record_count: int = Field(ge=0, default=0)
    search_latency_ms: int = Field(ge=0)
    checks: list[CatalogCoreEvalCheck] = Field(default_factory=list)


class CatalogCoreEvalSuiteReport(BaseModel):
    suite: str = "catalog-core"
    workspace_id: int = Field(gt=0)
    multimodal: bool = False
    total_runs: int = Field(ge=0)
    passed_runs: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    projected_product_count: int = Field(ge=0)
    projected_offer_count: int = Field(ge=0)
    projected_media_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    resolved_conflict_count: int = Field(ge=0, default=0)
    missing_field_count: int = Field(ge=0)
    media_search_hit_count: int = Field(ge=0)
    indexed_source_unit_count: int = Field(ge=0)
    stale_index_record_count: int = Field(ge=0, default=0)
    duration_ms: int = Field(ge=0)
    p95_case_duration_ms: int = Field(ge=0)
    results: list[CatalogCoreEvalResult] = Field(default_factory=list)


async def run_catalog_core_eval_suite(
    *,
    session: AsyncSession,
    workspace_id: int,
    include_multimodal: bool = False,
) -> CatalogCoreEvalSuiteReport:
    started = time.monotonic()
    service = CommerceCatalogCoreService(session)
    seed = f"catalog-core-eval:{workspace_id}:{'multi' if include_multimodal else 'base'}"
    await _seed_catalog_facts(
        session=session,
        workspace_id=workspace_id,
        seed=seed,
        include_multimodal=include_multimodal,
    )
    await session.flush()
    projection = await service.project_from_business_brain(
        workspace_id=workspace_id,
        commit=False,
        rebuild_retrieval_index=include_multimodal,
    )
    results = [
        await _typed_authority_projection_case(
            service=service,
            workspace_id=workspace_id,
            seed=seed,
            indexed_source_units=projection.indexed_source_units,
        ),
        await _conflict_and_missing_field_case(
            service=service,
            workspace_id=workspace_id,
            seed=seed,
        ),
        await _conflict_resolution_lifecycle_case(
            service=service,
            workspace_id=workspace_id,
            seed=seed,
        ),
    ]
    if include_multimodal:
        results.append(
            await _multimodal_media_authority_case(
                service=service,
                workspace_id=workspace_id,
                seed=seed,
                indexed_source_units=projection.indexed_source_units,
            )
        )
        results.append(
            await _retrieval_index_stale_lifecycle_case(
                session=session,
                service=service,
                workspace_id=workspace_id,
                seed=seed,
            )
        )

    passed = sum(1 for result in results if result.passed)
    durations = [result.search_latency_ms for result in results]
    media_search_hit_count = sum(
        result.media_count
        for result in results
        if result.case_id == "multimodal_media_authority" and result.passed
    )
    return CatalogCoreEvalSuiteReport(
        workspace_id=workspace_id,
        multimodal=include_multimodal,
        total_runs=len(results),
        passed_runs=passed,
        pass_rate=(passed / len(results)) if results else 0.0,
        projected_product_count=projection.products,
        projected_offer_count=projection.offers,
        projected_media_count=projection.media,
        conflict_count=projection.conflicts,
        resolved_conflict_count=sum(result.resolved_conflict_count for result in results),
        missing_field_count=projection.missing_fields,
        media_search_hit_count=media_search_hit_count,
        indexed_source_unit_count=projection.indexed_source_units,
        stale_index_record_count=sum(result.stale_index_record_count for result in results),
        duration_ms=int((time.monotonic() - started) * 1000),
        p95_case_duration_ms=_percentile_ms(durations, 0.95),
        results=results,
    )


async def _typed_authority_projection_case(
    *,
    service: CommerceCatalogCoreService,
    workspace_id: int,
    seed: str,
    indexed_source_units: int,
) -> CatalogCoreEvalResult:
    case = CatalogCoreEvalCase(
        case_id="typed_authority_projection",
        description="Approved catalog facts project to typed product/offer/media authority.",
    )
    result = await service.search_authority(
        workspace_id=workspace_id,
        query=f"{seed} atlas backpack price hero image",
        include_media=True,
    )
    product_refs = [product.product_ref for product in result.products]
    offer_prices = [offer.price for offer in result.offers]
    media_refs = [media.media_ref for media in result.media]
    checks = [
        CatalogCoreEvalCheck(
            name="product_authority_projected",
            passed=f"product:{seed}:atlas-backpack" in product_refs,
            detail=f"products={product_refs}",
        ),
        CatalogCoreEvalCheck(
            name="offer_authority_projected",
            passed="189000" in offer_prices,
            detail=f"offers={offer_prices}",
        ),
        CatalogCoreEvalCheck(
            name="media_authority_projected",
            passed=f"media:{seed}:atlas-backpack:hero" in media_refs,
            detail=f"media={media_refs}",
        ),
        CatalogCoreEvalCheck(
            name="source_refs_preserved",
            passed=f"telegram_channel:@catalog:{seed}:post:1" in result.source_refs,
            detail=f"source_refs={result.source_refs}",
        ),
    ]
    return _case_result(
        case=case,
        result=result,
        indexed_source_units=indexed_source_units,
        checks=checks,
    )


async def _conflict_and_missing_field_case(
    *,
    service: CommerceCatalogCoreService,
    workspace_id: int,
    seed: str,
) -> CatalogCoreEvalResult:
    case = CatalogCoreEvalCase(
        case_id="conflict_and_missing_field_visibility",
        description="Typed catalog search exposes open price conflicts and missing price fields.",
    )
    result = await service.search_authority(
        workspace_id=workspace_id,
        query=f"{seed} conflict sandal mystery cable price",
        include_media=True,
    )
    conflict_refs = [conflict.product_ref for conflict in result.conflicts]
    missing_refs = [field.product_ref for field in result.missing_fields]
    checks = [
        CatalogCoreEvalCheck(
            name="price_conflict_visible",
            passed=f"product:{seed}:conflict-sandal" in conflict_refs,
            detail=f"conflicts={conflict_refs}",
        ),
        CatalogCoreEvalCheck(
            name="missing_price_visible",
            passed=f"product:{seed}:mystery-cable" in missing_refs,
            detail=f"missing={missing_refs}",
        ),
        CatalogCoreEvalCheck(
            name="conflict_sources_preserved",
            passed=any(f"telegram_channel:@catalog:{seed}:price-a" in ref for ref in result.source_refs)
            and any(f"telegram_channel:@catalog:{seed}:price-b" in ref for ref in result.source_refs),
            detail=f"source_refs={result.source_refs}",
        ),
    ]
    return _case_result(case=case, result=result, indexed_source_units=0, checks=checks)


async def _conflict_resolution_lifecycle_case(
    *,
    service: CommerceCatalogCoreService,
    workspace_id: int,
    seed: str,
) -> CatalogCoreEvalResult:
    case = CatalogCoreEvalCase(
        case_id="conflict_resolution_lifecycle",
        description="Owner-resolved price conflicts demote losing offers and stop surfacing warnings.",
    )
    resolved = await service.resolve_price_conflict(
        workspace_id=workspace_id,
        conflict_ref=f"catalog_conflict:product:{seed}:conflict-sandal:default:price",
        winning_source_fact_id=f"catalog_offer:{seed}:conflict-sandal:a",
        actor_ref="catalog-core-eval",
        commit=False,
    )
    projection = await service.project_from_business_brain(
        workspace_id=workspace_id,
        commit=False,
    )
    result = await service.search_authority(
        workspace_id=workspace_id,
        query=f"{seed} conflict sandal price",
        include_media=True,
    )
    offer_refs = [offer.offer_ref for offer in result.offers]
    offer_prices = [offer.price for offer in result.offers]
    checks = [
        CatalogCoreEvalCheck(
            name="conflict_marked_resolved",
            passed=resolved.status == "resolved"
            and resolved.resolution.get("winning_source_fact_id")
            == f"catalog_offer:{seed}:conflict-sandal:a",
            detail=f"status={resolved.status} resolution={resolved.resolution}",
        ),
        CatalogCoreEvalCheck(
            name="projection_no_longer_has_open_conflict",
            passed=projection.conflicts == 0 and result.telemetry.conflict_count == 0,
            detail=(
                f"projection_conflicts={projection.conflicts} "
                f"search_conflicts={result.telemetry.conflict_count}"
            ),
        ),
        CatalogCoreEvalCheck(
            name="losing_offer_removed_from_authority",
            passed=f"offer:{seed}:conflict-sandal:a" in offer_refs
            and f"offer:{seed}:conflict-sandal:b" not in offer_refs
            and "90000" in offer_prices
            and "99000" not in offer_prices,
            detail=f"offers={offer_refs} prices={offer_prices}",
        ),
    ]
    case_result = _case_result(case=case, result=result, indexed_source_units=0, checks=checks)
    return case_result.model_copy(update={"resolved_conflict_count": 1 if resolved.status == "resolved" else 0})


async def _multimodal_media_authority_case(
    *,
    service: CommerceCatalogCoreService,
    workspace_id: int,
    seed: str,
    indexed_source_units: int,
) -> CatalogCoreEvalResult:
    case = CatalogCoreEvalCase(
        case_id="multimodal_media_authority",
        description="Visual/OCR media authority can retrieve product, offer, media, and index source units.",
    )
    result = await service.search_authority(
        workspace_id=workspace_id,
        query=f"{seed} emerald compact wallet brass key image",
        include_media=True,
    )
    product_refs = [product.product_ref for product in result.products]
    media_refs = [media.media_ref for media in result.media]
    checks = [
        CatalogCoreEvalCheck(
            name="visual_media_matches_product",
            passed=f"product:{seed}:visual-wallet" in product_refs,
            detail=f"products={product_refs}",
        ),
        CatalogCoreEvalCheck(
            name="media_hit_returned",
            passed=f"media:{seed}:visual-wallet:hero" in media_refs,
            detail=f"media={media_refs}",
        ),
        CatalogCoreEvalCheck(
            name="source_unit_indexed",
            passed=indexed_source_units >= 1,
            detail=f"indexed_source_units={indexed_source_units}",
        ),
    ]
    return _case_result(
        case=case,
        result=result,
        indexed_source_units=indexed_source_units,
        checks=checks,
    )


async def _retrieval_index_stale_lifecycle_case(
    *,
    session: AsyncSession,
    service: CommerceCatalogCoreService,
    workspace_id: int,
    seed: str,
) -> CatalogCoreEvalResult:
    case = CatalogCoreEvalCase(
        case_id="retrieval_index_stale_lifecycle",
        description="Stale catalog media retires its Retrieval Core source-unit index row.",
    )
    fact_id = f"catalog_media:{seed}:visual-wallet:hero"
    fact = await session.scalar(
        select(BusinessBrainFactRecord).where(
            BusinessBrainFactRecord.workspace_id == workspace_id,
            BusinessBrainFactRecord.fact_id == fact_id,
        )
    )
    if fact is not None:
        fact.status = "stale"
        fact.raw_fact = {**dict(fact.raw_fact or {}), "approval_state": "stale"}
        session.add(fact)
        await session.flush()
    await service.project_from_business_brain(
        workspace_id=workspace_id,
        commit=False,
        rebuild_retrieval_index=True,
    )
    result = await service.search_authority(
        workspace_id=workspace_id,
        query=f"{seed} emerald compact wallet brass key image",
        include_media=True,
    )
    index_row = await session.scalar(
        select(BusinessBrainIndexRecord).where(
            BusinessBrainIndexRecord.workspace_id == workspace_id,
            BusinessBrainIndexRecord.fact_id == fact_id,
        )
    )
    media_refs = [media.media_ref for media in result.media]
    checks = [
        CatalogCoreEvalCheck(
            name="stale_media_fact_found",
            passed=fact is not None,
            detail=f"fact_id={fact_id} present={fact is not None}",
        ),
        CatalogCoreEvalCheck(
            name="index_row_marked_stale",
            passed=index_row is not None
            and index_row.state == "stale"
            and index_row.embedding_state == "degraded"
            and index_row.embedding is None
            and index_row.degraded_reason == "catalog_authority_stale",
            detail=(
                f"state={(index_row.state if index_row else None)} "
                f"embedding_state={(index_row.embedding_state if index_row else None)} "
                f"reason={(index_row.degraded_reason if index_row else None)}"
            ),
        ),
        CatalogCoreEvalCheck(
            name="stale_media_not_returned",
            passed=f"media:{seed}:visual-wallet:hero" not in media_refs,
            detail=f"media_refs={media_refs}",
        ),
    ]
    case_result = _case_result(case=case, result=result, indexed_source_units=0, checks=checks)
    return case_result.model_copy(
        update={
            "stale_index_record_count": (
                1 if index_row is not None and index_row.state == "stale" else 0
            )
        }
    )


async def _seed_catalog_facts(
    *,
    session: AsyncSession,
    workspace_id: int,
    seed: str,
    include_multimodal: bool,
) -> None:
    facts = [
        _fact(
            workspace_id=workspace_id,
            fact_id=f"catalog_product:{seed}:atlas-backpack",
            fact_type="catalog_product",
            entity_ref=f"product:{seed}:atlas-backpack",
            value={
                "product_ref": f"product:{seed}:atlas-backpack",
                "name": f"{seed} Atlas Backpack",
                "aliases": ["atlas backpack", "travel backpack"],
                "description": "Durable daily backpack with laptop pocket.",
            },
            source_refs=[f"telegram_channel:@catalog:{seed}:post:1"],
        ),
        _fact(
            workspace_id=workspace_id,
            fact_id=f"catalog_offer:{seed}:atlas-backpack",
            fact_type="catalog_offer",
            entity_ref=f"product:{seed}:atlas-backpack",
            value={
                "offer_ref": f"offer:{seed}:atlas-backpack",
                "product_ref": f"product:{seed}:atlas-backpack",
                "price": "189000",
                "currency": "UZS",
                "stock": "available",
            },
            source_refs=[f"telegram_channel:@catalog:{seed}:post:1"],
        ),
        _fact(
            workspace_id=workspace_id,
            fact_id=f"catalog_media:{seed}:atlas-backpack:hero",
            fact_type="catalog_media",
            entity_ref=f"product:{seed}:atlas-backpack",
            value={
                "product_ref": f"product:{seed}:atlas-backpack",
                "media_ref": f"media:{seed}:atlas-backpack:hero",
                "url": "https://example.test/catalog-eval/atlas.webp",
                "caption": "Atlas backpack hero image",
                "ocr_text": "Atlas backpack 189000 UZS",
                "visual_summary": "black travel backpack on white studio background",
            },
            source_refs=[f"telegram_channel:@catalog:{seed}:post:1:photo"],
        ),
        _fact(
            workspace_id=workspace_id,
            fact_id=f"catalog_product:{seed}:conflict-sandal",
            fact_type="catalog_product",
            entity_ref=f"product:{seed}:conflict-sandal",
            value={
                "product_ref": f"product:{seed}:conflict-sandal",
                "name": f"{seed} Conflict Sandal",
            },
            source_refs=[f"telegram_channel:@catalog:{seed}:price-a"],
        ),
        _fact(
            workspace_id=workspace_id,
            fact_id=f"catalog_offer:{seed}:conflict-sandal:a",
            fact_type="catalog_offer",
            entity_ref=f"product:{seed}:conflict-sandal",
            value={
                "offer_ref": f"offer:{seed}:conflict-sandal:a",
                "product_ref": f"product:{seed}:conflict-sandal",
                "price": "90000",
                "currency": "UZS",
            },
            source_refs=[f"telegram_channel:@catalog:{seed}:price-a"],
        ),
        _fact(
            workspace_id=workspace_id,
            fact_id=f"catalog_offer:{seed}:conflict-sandal:b",
            fact_type="catalog_offer",
            entity_ref=f"product:{seed}:conflict-sandal",
            value={
                "offer_ref": f"offer:{seed}:conflict-sandal:b",
                "product_ref": f"product:{seed}:conflict-sandal",
                "price": "99000",
                "currency": "UZS",
            },
            source_refs=[f"telegram_channel:@catalog:{seed}:price-b"],
        ),
        _fact(
            workspace_id=workspace_id,
            fact_id=f"catalog_product:{seed}:mystery-cable",
            fact_type="catalog_product",
            entity_ref=f"product:{seed}:mystery-cable",
            value={
                "product_ref": f"product:{seed}:mystery-cable",
                "name": f"{seed} Mystery Cable",
            },
            source_refs=[f"telegram_channel:@catalog:{seed}:post:missing-price"],
        ),
    ]
    if include_multimodal:
        facts.extend(
            [
                _fact(
                    workspace_id=workspace_id,
                    fact_id=f"catalog_product:{seed}:visual-wallet",
                    fact_type="catalog_product",
                    entity_ref=f"product:{seed}:visual-wallet",
                    value={
                        "product_ref": f"product:{seed}:visual-wallet",
                        "name": f"{seed} SKU 42",
                    },
                    source_refs=[f"telegram_channel:@catalog:{seed}:wallet:product"],
                ),
                _fact(
                    workspace_id=workspace_id,
                    fact_id=f"catalog_offer:{seed}:visual-wallet",
                    fact_type="catalog_offer",
                    entity_ref=f"product:{seed}:visual-wallet",
                    value={
                        "offer_ref": f"offer:{seed}:visual-wallet",
                        "product_ref": f"product:{seed}:visual-wallet",
                        "price": "144000",
                        "currency": "UZS",
                        "stock": "available",
                    },
                    source_refs=[f"telegram_channel:@catalog:{seed}:wallet:offer"],
                ),
                _fact(
                    workspace_id=workspace_id,
                    fact_id=f"catalog_media:{seed}:visual-wallet:hero",
                    fact_type="catalog_media",
                    entity_ref=f"product:{seed}:visual-wallet",
                    value={
                        "product_ref": f"product:{seed}:visual-wallet",
                        "media_ref": f"media:{seed}:visual-wallet:hero",
                        "url": "https://example.test/catalog-eval/wallet.webp",
                        "caption": "",
                        "ocr_text": "emerald compact wallet",
                        "visual_summary": "green wallet photographed next to a brass key",
                    },
                    source_refs=[f"telegram_channel:@catalog:{seed}:wallet:photo"],
                ),
            ]
        )
    session.add_all(facts)


def _fact(
    *,
    workspace_id: int,
    fact_id: str,
    fact_type: str,
    entity_ref: str,
    value: dict,
    source_refs: list[str],
) -> BusinessBrainFactRecord:
    return BusinessBrainFactRecord(
        workspace_id=workspace_id,
        fact_id=fact_id,
        fact_type=fact_type,
        entity_ref=entity_ref,
        value=value,
        confidence=0.93,
        status="active",
        risk_tier="low",
        valid_from=datetime.now(UTC),
        source_refs=source_refs,
        idempotency_key=f"catalog-core-eval:{fact_id}",
        raw_fact={
            "approval_state": "approved",
            "source_refs": source_refs,
            "value": value,
        },
    )


def _case_result(
    *,
    case: CatalogCoreEvalCase,
    result,
    indexed_source_units: int,
    checks: list[CatalogCoreEvalCheck],
) -> CatalogCoreEvalResult:
    return CatalogCoreEvalResult(
        case_id=case.case_id,
        description=case.description,
        passed=all(check.passed for check in checks),
        product_count=result.telemetry.product_count,
        offer_count=result.telemetry.offer_count,
        media_count=result.telemetry.media_count,
        conflict_count=result.telemetry.conflict_count,
        resolved_conflict_count=0,
        missing_field_count=result.telemetry.missing_field_count,
        indexed_source_unit_count=indexed_source_units,
        stale_index_record_count=0,
        search_latency_ms=result.telemetry.search_latency_ms,
        checks=checks,
    )


def _percentile_ms(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * percentile)),
    )
    return ordered[index]
