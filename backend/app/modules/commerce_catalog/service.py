from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce_catalog import (
    CatalogConflictRecord,
    CatalogMediaRecord,
    CatalogMissingFieldRecord,
    CatalogOfferRecord,
    CatalogProductRecord,
    CatalogSourceFactRecord,
    CatalogVariantRecord,
)
from app.models.commercial_spine import BusinessBrainFactRecord, BusinessBrainIndexRecord
from app.modules.commerce_catalog.contracts import (
    CommerceCatalogConflict,
    CommerceCatalogMedia,
    CommerceCatalogMissingField,
    CommerceCatalogOffer,
    CommerceCatalogProduct,
    CommerceCatalogProjectionReport,
    CommerceCatalogSearchResult,
    CommerceCatalogTelemetry,
)

_CATALOG_FACT_TYPES = {
    "catalog_product",
    "catalog_variant",
    "catalog_offer",
    "catalog_media",
    "catalog_source",
}


class CommerceCatalogCoreService:
    """Typed catalog authority core.

    Source learning still writes evidence/candidates into Business Brain facts.
    This service projects approved catalog facts into durable typed catalog rows
    so runtime authority does not depend on a generic fact-search facade.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def project_from_business_brain(
        self,
        *,
        workspace_id: int,
        commit: bool = True,
        rebuild_retrieval_index: bool = False,
    ) -> CommerceCatalogProjectionReport:
        rows = list(
            (
                await self._session.execute(
                    select(BusinessBrainFactRecord)
                    .where(
                        BusinessBrainFactRecord.workspace_id == workspace_id,
                        BusinessBrainFactRecord.fact_type.in_(_CATALOG_FACT_TYPES),
                    )
                    .order_by(BusinessBrainFactRecord.valid_from.asc(), BusinessBrainFactRecord.id.asc())
                )
            ).scalars()
        )
        report = CommerceCatalogProjectionReport(workspace_id=workspace_id, source_facts=len(rows))
        projected_fact_ids: list[str] = []
        for row in rows:
            authority_state = _authority_state(row)
            if authority_state != "approved":
                if authority_state == "stale":
                    await self._upsert_source_fact(row, authority_state=authority_state)
                    await self._mark_projected_stale(row)
                    if rebuild_retrieval_index:
                        await self._mark_retrieval_index_stale(row)
                continue
            await self._upsert_source_fact(row, authority_state=authority_state)
            if row.fact_type == "catalog_product":
                if await self._upsert_product(row, authority_state=authority_state):
                    report.products += 1
                    projected_fact_ids.append(row.fact_id)
            elif row.fact_type == "catalog_variant":
                if await self._upsert_variant(row, authority_state=authority_state):
                    report.variants += 1
                    projected_fact_ids.append(row.fact_id)
            elif row.fact_type == "catalog_offer":
                if await self._upsert_offer(row, authority_state=authority_state):
                    report.offers += 1
                    projected_fact_ids.append(row.fact_id)
            elif row.fact_type == "catalog_media" and await self._upsert_media(
                row, authority_state=authority_state
            ):
                report.media += 1
                projected_fact_ids.append(row.fact_id)
        report = report.model_copy(
            update={
                "missing_fields": await self._refresh_missing_fields(workspace_id),
                "conflicts": await self._refresh_conflicts(workspace_id),
            }
        )
        if rebuild_retrieval_index and projected_fact_ids:
            indexed = await self._rebuild_retrieval_index(
                workspace_id=workspace_id,
                fact_ids=_unique_refs(projected_fact_ids),
            )
            report = report.model_copy(update={"indexed_source_units": indexed})
        if commit:
            await self._session.commit()
        else:
            await self._session.flush()
        return report

    async def search_authority(
        self,
        *,
        workspace_id: int,
        query: str,
        include_media: bool = True,
        limit: int = 8,
    ) -> CommerceCatalogSearchResult:
        started = time.perf_counter()
        products = list(
            (
                await self._session.execute(
                    select(CatalogProductRecord)
                    .where(
                        CatalogProductRecord.workspace_id == workspace_id,
                        CatalogProductRecord.authority_state == "approved",
                    )
                    .order_by(CatalogProductRecord.updated_at.desc(), CatalogProductRecord.id.desc())
                )
            ).scalars()
        )
        approved_media = await self._approved_media_for_workspace(workspace_id)
        media_scores: dict[str, float] = {}
        for media in approved_media:
            score = _text_score(query, _media_search_text(media))
            if score > 0:
                media_scores[media.product_ref] = max(media_scores.get(media.product_ref, 0.0), score)
        ranked = [
            (score, product)
            for product in products
            if (score := max(_text_score(query, _product_search_text(product)), media_scores.get(product.product_ref, 0.0))) > 0
        ]
        ranked.sort(key=lambda item: item[0], reverse=True)
        matched_products = [
            product for _, product in _scope_ranked_products(ranked)[:limit]
        ]
        if not matched_products and len(products) == 1:
            matched_products = products
        product_refs = [product.product_ref for product in matched_products]
        offers = await self._approved_offers(workspace_id, product_refs)
        media = await self._approved_media(workspace_id, product_refs) if include_media else []
        missing = await self._missing_fields(workspace_id, product_refs)
        conflicts = await self._open_conflicts(workspace_id, product_refs)
        source_refs = _unique_refs(
            ref
            for rows in (matched_products, offers, media, missing, conflicts)
            for row in rows
            for ref in list(getattr(row, "source_refs", []) or [])
        )
        source_fact_count = int(
            await self._session.scalar(
                select(func.count())
                .select_from(CatalogSourceFactRecord)
                .where(CatalogSourceFactRecord.workspace_id == workspace_id)
            )
            or 0
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return CommerceCatalogSearchResult(
            query=query,
            products=[_product_contract(row) for row in matched_products],
            offers=[_offer_contract(row) for row in offers],
            media=[_media_contract(row) for row in media],
            missing_fields=[_missing_contract(row) for row in missing],
            conflicts=[_conflict_contract(row) for row in conflicts],
            source_refs=source_refs,
            telemetry=CommerceCatalogTelemetry(
                source_fact_count=source_fact_count,
                product_count=len(matched_products),
                offer_count=len(offers),
                media_count=len(media),
                missing_field_count=len(missing),
                conflict_count=len(conflicts),
                search_latency_ms=elapsed_ms,
            ),
        )

    async def resolve_price_conflict(
        self,
        *,
        workspace_id: int,
        conflict_ref: str,
        winning_source_fact_id: str,
        actor_ref: str,
        commit: bool = True,
    ) -> CommerceCatalogConflict:
        return await self.resolve_catalog_conflict(
            workspace_id=workspace_id,
            conflict_ref=conflict_ref,
            winning_source_fact_id=winning_source_fact_id,
            actor_ref=actor_ref,
            allowed_fields={"price"},
            commit=commit,
        )

    async def resolve_catalog_conflict(
        self,
        *,
        workspace_id: int,
        conflict_ref: str,
        winning_source_fact_id: str,
        actor_ref: str,
        allowed_fields: set[str] | None = None,
        commit: bool = True,
    ) -> CommerceCatalogConflict:
        conflict = await self._get_one(
            CatalogConflictRecord,
            workspace_id=workspace_id,
            conflict_ref=conflict_ref,
        )
        if conflict is None or conflict.status != "open":
            raise ValueError(f"Open catalog conflict not found: {conflict_ref}")
        supported_fields = allowed_fields or {"price", "stock_state", "availability"}
        if conflict.field not in supported_fields:
            raise ValueError(f"Unsupported catalog conflict field: {conflict.field}")
        candidates = [dict(item) for item in list(conflict.candidate_values or [])]
        winner = next(
            (
                candidate
                for candidate in candidates
                if str(candidate.get("source_fact_id") or "") == winning_source_fact_id
            ),
            None,
        )
        if winner is None:
            raise ValueError(f"Winning source fact is not a conflict candidate: {winning_source_fact_id}")
        losing_source_fact_ids = [
            str(candidate.get("source_fact_id") or "")
            for candidate in candidates
            if str(candidate.get("source_fact_id") or "") and str(candidate.get("source_fact_id") or "") != winning_source_fact_id
        ]
        resolution = {
            "winning_source_fact_id": winning_source_fact_id,
            "losing_source_fact_ids": losing_source_fact_ids,
            "actor_ref": actor_ref,
            "resolved_at": datetime.now(UTC).isoformat(),
        }
        for source_fact_id in losing_source_fact_ids:
            await self._demote_offer_source_fact(
                workspace_id=workspace_id,
                source_fact_id=source_fact_id,
                resolution=resolution,
            )
        conflict.status = "resolved"
        conflict.candidate_values = [
            {
                **candidate,
                "selected": str(candidate.get("source_fact_id") or "") == winning_source_fact_id,
            }
            for candidate in candidates
        ]
        self._session.add(conflict)
        if commit:
            await self._session.commit()
        else:
            await self._session.flush()
        return _conflict_contract(conflict).model_copy(update={"resolution": resolution})

    async def _upsert_product(self, row: BusinessBrainFactRecord, *, authority_state: str) -> bool:
        value = _fact_value(row)
        product_ref = _product_ref(row, value)
        if not product_ref:
            return False
        record = await self._get_one(
            CatalogProductRecord,
            workspace_id=row.workspace_id,
            product_ref=product_ref,
        )
        if record is None:
            record = CatalogProductRecord(workspace_id=row.workspace_id, product_ref=product_ref, name="")
            self._session.add(record)
        record.name = _first_text(value, "name", "title", "product_name") or product_ref
        record.aliases = _string_list(value.get("aliases"))
        record.description = _first_text(value, "description", "summary", "details") or ""
        record.attributes = _dict_value(value.get("attributes"))
        record.authority_state = authority_state
        record.source_refs = _source_refs(row)
        record.source_fact_ids = _unique_refs([*list(record.source_fact_ids or []), row.fact_id])
        record.freshness = _dict_value(value.get("freshness"))
        return True

    async def _upsert_variant(self, row: BusinessBrainFactRecord, *, authority_state: str) -> bool:
        value = _fact_value(row)
        product_ref = _product_ref(row, value)
        variant_ref = _first_text(value, "variant_ref", "variant_id") or row.fact_id
        if not product_ref:
            return False
        record = await self._get_one(
            CatalogVariantRecord,
            workspace_id=row.workspace_id,
            variant_ref=variant_ref,
        )
        if record is None:
            record = CatalogVariantRecord(
                workspace_id=row.workspace_id,
                variant_ref=variant_ref,
                product_ref=product_ref,
                label="",
            )
            self._session.add(record)
        record.product_ref = product_ref
        record.label = _first_text(value, "label", "name", "title") or variant_ref
        record.attributes = _dict_value(value.get("attributes"))
        record.authority_state = authority_state
        record.source_refs = _source_refs(row)
        record.source_fact_ids = _unique_refs([*list(record.source_fact_ids or []), row.fact_id])
        return True

    async def _upsert_offer(self, row: BusinessBrainFactRecord, *, authority_state: str) -> bool:
        value = _fact_value(row)
        product_ref = _product_ref(row, value)
        if not product_ref:
            return False
        offer_ref = _first_text(value, "offer_ref", "offer_id") or row.fact_id
        record = await self._get_one(CatalogOfferRecord, workspace_id=row.workspace_id, offer_ref=offer_ref)
        if record is None:
            record = CatalogOfferRecord(
                workspace_id=row.workspace_id,
                offer_ref=offer_ref,
                product_ref=product_ref,
                authority_state=authority_state,
            )
            self._session.add(record)
        record.product_ref = product_ref
        record.variant_ref = _first_text(value, "variant_ref", "variant_id")
        record.price = _price_text(value)
        record.currency = _currency_text(value)
        record.stock_state = _stock_state_text(value)
        record.availability = _first_text(value, "availability")
        record.authority_state = authority_state
        record.source_refs = _source_refs(row)
        record.source_fact_ids = _unique_refs([*list(record.source_fact_ids or []), row.fact_id])
        return True

    async def _upsert_media(self, row: BusinessBrainFactRecord, *, authority_state: str) -> bool:
        value = _fact_value(row)
        product_ref = _product_ref(row, value)
        media_ref = _first_text(value, "media_ref", "asset_ref", "media_id") or row.fact_id
        if not product_ref:
            return False
        record = await self._get_one(CatalogMediaRecord, workspace_id=row.workspace_id, media_ref=media_ref)
        if record is None:
            record = CatalogMediaRecord(
                workspace_id=row.workspace_id,
                media_ref=media_ref,
                product_ref=product_ref,
                authority_state=authority_state,
            )
            self._session.add(record)
        record.product_ref = product_ref
        record.media_kind = _first_text(value, "media_kind", "kind") or "image"
        record.url = _first_text(value, "url", "public_url", "file_url")
        record.caption = _first_text(value, "caption", "title") or ""
        record.ocr_text = _first_text(value, "ocr_text", "text") or ""
        record.visual_summary = _first_text(value, "visual_summary", "description", "summary") or ""
        record.quality_state = _first_text(value, "quality_state")
        record.crop_state = _first_text(value, "crop_state")
        record.authority_state = authority_state if value.get("approved", True) is not False else "candidate"
        record.source_refs = _source_refs(row)
        record.source_fact_ids = _unique_refs([*list(record.source_fact_ids or []), row.fact_id])
        record.metadata_ = _dict_value(value.get("metadata"))
        return True

    async def _upsert_source_fact(self, row: BusinessBrainFactRecord, *, authority_state: str) -> None:
        value = _fact_value(row)
        record = await self._get_one(
            CatalogSourceFactRecord,
            workspace_id=row.workspace_id,
            source_fact_id=row.fact_id,
        )
        if record is None:
            record = CatalogSourceFactRecord(
                workspace_id=row.workspace_id,
                source_fact_id=row.fact_id,
                fact_type=row.fact_type,
                authority_state=authority_state,
            )
            self._session.add(record)
        record.product_ref = _product_ref(row, value)
        record.fact_type = row.fact_type
        record.authority_state = authority_state
        record.value = value
        record.source_refs = _source_refs(row)

    async def _demote_offer_source_fact(
        self,
        *,
        workspace_id: int,
        source_fact_id: str,
        resolution: dict[str, Any],
    ) -> None:
        source_fact = await self._get_one(
            CatalogSourceFactRecord,
            workspace_id=workspace_id,
            source_fact_id=source_fact_id,
        )
        value = dict(source_fact.value or {}) if source_fact is not None else {}
        source_refs = list(source_fact.source_refs or []) if source_fact is not None else []
        if source_fact is not None:
            source_fact.authority_state = "stale"
            source_fact.value = {
                **value,
                "resolution": resolution,
            }
            self._session.add(source_fact)

        brain_fact = await self._get_one(
            BusinessBrainFactRecord,
            workspace_id=workspace_id,
            fact_id=source_fact_id,
        )
        if brain_fact is not None:
            raw_fact = dict(brain_fact.raw_fact or {})
            brain_value = dict(brain_fact.value or {})
            brain_fact.status = "stale"
            brain_fact.raw_fact = {
                **raw_fact,
                "approval_state": "stale",
                "resolution": resolution,
            }
            brain_fact.value = {
                **brain_value,
                "resolution": resolution,
            }
            self._session.add(brain_fact)
            value = brain_value or value
            source_refs = list(brain_fact.source_refs or []) or source_refs

        offer_ref = _first_text(value, "offer_ref", "offer_id") or source_fact_id
        offer = await self._get_one(
            CatalogOfferRecord,
            workspace_id=workspace_id,
            offer_ref=offer_ref,
        )
        if offer is not None:
            offer.authority_state = "stale"
            offer.source_refs = source_refs or list(offer.source_refs or [])
            offer.source_fact_ids = _unique_refs([*list(offer.source_fact_ids or []), source_fact_id])
            self._session.add(offer)

    async def _mark_projected_stale(self, row: BusinessBrainFactRecord) -> None:
        value = _fact_value(row)
        if row.fact_type == "catalog_product":
            await self._mark_product_stale(row, value)
        elif row.fact_type == "catalog_variant":
            await self._mark_variant_stale(row, value)
        elif row.fact_type == "catalog_offer":
            await self._mark_offer_stale(row, value)
        elif row.fact_type == "catalog_media":
            await self._mark_media_stale(row, value)

    async def _mark_product_stale(self, row: BusinessBrainFactRecord, value: dict[str, Any]) -> None:
        product_ref = _product_ref(row, value)
        if not product_ref:
            return
        record = await self._get_one(
            CatalogProductRecord,
            workspace_id=row.workspace_id,
            product_ref=product_ref,
        )
        if record is None:
            return
        record.authority_state = "stale"
        record.source_refs = _source_refs(row) or list(record.source_refs or [])
        record.source_fact_ids = _unique_refs([*list(record.source_fact_ids or []), row.fact_id])

    async def _mark_variant_stale(self, row: BusinessBrainFactRecord, value: dict[str, Any]) -> None:
        variant_ref = _first_text(value, "variant_ref", "variant_id") or row.fact_id
        record = await self._get_one(
            CatalogVariantRecord,
            workspace_id=row.workspace_id,
            variant_ref=variant_ref,
        )
        if record is None:
            return
        record.authority_state = "stale"
        record.source_refs = _source_refs(row) or list(record.source_refs or [])
        record.source_fact_ids = _unique_refs([*list(record.source_fact_ids or []), row.fact_id])

    async def _mark_offer_stale(self, row: BusinessBrainFactRecord, value: dict[str, Any]) -> None:
        offer_ref = _first_text(value, "offer_ref", "offer_id") or row.fact_id
        record = await self._get_one(
            CatalogOfferRecord,
            workspace_id=row.workspace_id,
            offer_ref=offer_ref,
        )
        if record is None:
            return
        record.authority_state = "stale"
        record.source_refs = _source_refs(row) or list(record.source_refs or [])
        record.source_fact_ids = _unique_refs([*list(record.source_fact_ids or []), row.fact_id])

    async def _mark_media_stale(self, row: BusinessBrainFactRecord, value: dict[str, Any]) -> None:
        media_ref = _first_text(value, "media_ref", "asset_ref", "media_id") or row.fact_id
        record = await self._get_one(
            CatalogMediaRecord,
            workspace_id=row.workspace_id,
            media_ref=media_ref,
        )
        if record is None:
            return
        record.authority_state = "stale"
        record.source_refs = _source_refs(row) or list(record.source_refs or [])
        record.source_fact_ids = _unique_refs([*list(record.source_fact_ids or []), row.fact_id])

    async def _mark_retrieval_index_stale(self, row: BusinessBrainFactRecord) -> int:
        records = list(
            (
                await self._session.execute(
                    select(BusinessBrainIndexRecord).where(
                        BusinessBrainIndexRecord.workspace_id == row.workspace_id,
                        BusinessBrainIndexRecord.fact_id == row.fact_id,
                    )
                )
            ).scalars()
        )
        for record in records:
            record.state = "stale"
            record.embedding_state = "degraded"
            record.embedding = None
            record.degraded_reason = "catalog_authority_stale"
            raw_index = dict(record.raw_index or {})
            raw_index.update(
                {
                    "state": "stale",
                    "embedding_state": "degraded",
                    "embedding": None,
                    "degraded_reason": "catalog_authority_stale",
                }
            )
            record.raw_index = raw_index
            self._session.add(record)
        return len(records)

    async def _refresh_missing_fields(self, workspace_id: int) -> int:
        products = list(
            (
                await self._session.execute(
                    select(CatalogProductRecord).where(
                        CatalogProductRecord.workspace_id == workspace_id,
                        CatalogProductRecord.authority_state == "approved",
                    )
                )
            ).scalars()
        )
        count = 0
        for product in products:
            offer_count = int(
                await self._session.scalar(
                    select(func.count())
                    .select_from(CatalogOfferRecord)
                    .where(
                        CatalogOfferRecord.workspace_id == workspace_id,
                        CatalogOfferRecord.product_ref == product.product_ref,
                        CatalogOfferRecord.authority_state == "approved",
                        CatalogOfferRecord.price.is_not(None),
                    )
                )
                or 0
            )
            if offer_count:
                await self._session.execute(
                    delete(CatalogMissingFieldRecord).where(
                        CatalogMissingFieldRecord.workspace_id == workspace_id,
                        CatalogMissingFieldRecord.product_ref == product.product_ref,
                        CatalogMissingFieldRecord.field == "price",
                    )
                )
                continue
            record = await self._get_one(
                CatalogMissingFieldRecord,
                workspace_id=workspace_id,
                product_ref=product.product_ref,
                field="price",
            )
            if record is None:
                record = CatalogMissingFieldRecord(
                    workspace_id=workspace_id,
                    product_ref=product.product_ref,
                    field="price",
                )
                self._session.add(record)
            record.authority_state = "candidate"
            record.source_refs = list(product.source_refs or [])
            count += 1
        return count

    async def _refresh_conflicts(self, workspace_id: int) -> int:
        await self._session.execute(
            delete(CatalogConflictRecord).where(
                CatalogConflictRecord.workspace_id == workspace_id,
                CatalogConflictRecord.status == "open",
            )
        )
        source_facts = list(
            (
                await self._session.execute(
                    select(CatalogSourceFactRecord)
                    .where(
                        CatalogSourceFactRecord.workspace_id == workspace_id,
                        CatalogSourceFactRecord.fact_type == "catalog_offer",
                        CatalogSourceFactRecord.authority_state == "approved",
                    )
                    .order_by(CatalogSourceFactRecord.id.asc())
                )
            ).scalars()
        )
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for fact in source_facts:
            value = dict(fact.value or {})
            product_ref = _product_ref_from_source_fact(fact, value)
            if not product_ref:
                continue
            variant_ref = _first_text(value, "variant_ref", "variant_id") or ""
            for candidate in _offer_conflict_candidates(fact, value):
                key = (product_ref, variant_ref, str(candidate["field"]))
                group = grouped.setdefault(key, {"values": {}, "source_refs": []})
                group["values"].setdefault(str(candidate["value_key"]), candidate)
                group["source_refs"].extend(list(fact.source_refs or []))
        count = 0
        for (product_ref, variant_ref, field), group in grouped.items():
            candidate_values = list(group["values"].values())
            if len(candidate_values) <= 1:
                continue
            conflict_ref = ":".join(
                [
                    "catalog_conflict",
                    product_ref,
                    variant_ref or "default",
                    field,
                ]
            )
            existing = await self._get_one(
                CatalogConflictRecord,
                workspace_id=workspace_id,
                conflict_ref=conflict_ref,
            )
            if existing is None:
                existing = CatalogConflictRecord(
                    workspace_id=workspace_id,
                    conflict_ref=conflict_ref,
                    product_ref=product_ref,
                    field=field,
                )
                self._session.add(existing)
            existing.product_ref = product_ref
            existing.field = field
            existing.candidate_values = candidate_values
            existing.source_refs = _unique_refs(group["source_refs"])
            existing.status = "open"
            count += 1
        return count

    async def _approved_offers(self, workspace_id: int, product_refs: list[str]) -> list[CatalogOfferRecord]:
        if not product_refs:
            return []
        return list(
            (
                await self._session.execute(
                    select(CatalogOfferRecord)
                    .where(
                        CatalogOfferRecord.workspace_id == workspace_id,
                        CatalogOfferRecord.product_ref.in_(product_refs),
                        CatalogOfferRecord.authority_state == "approved",
                    )
                    .order_by(CatalogOfferRecord.updated_at.desc(), CatalogOfferRecord.id.desc())
                )
            ).scalars()
        )

    async def _approved_media(self, workspace_id: int, product_refs: list[str]) -> list[CatalogMediaRecord]:
        if not product_refs:
            return []
        return list(
            (
                await self._session.execute(
                    select(CatalogMediaRecord)
                    .where(
                        CatalogMediaRecord.workspace_id == workspace_id,
                        CatalogMediaRecord.product_ref.in_(product_refs),
                        CatalogMediaRecord.authority_state == "approved",
                    )
                    .order_by(CatalogMediaRecord.updated_at.desc(), CatalogMediaRecord.id.desc())
                )
            ).scalars()
        )

    async def _approved_media_for_workspace(self, workspace_id: int) -> list[CatalogMediaRecord]:
        return list(
            (
                await self._session.execute(
                    select(CatalogMediaRecord)
                    .where(
                        CatalogMediaRecord.workspace_id == workspace_id,
                        CatalogMediaRecord.authority_state == "approved",
                    )
                    .order_by(CatalogMediaRecord.updated_at.desc(), CatalogMediaRecord.id.desc())
                )
            ).scalars()
        )

    async def _missing_fields(self, workspace_id: int, product_refs: list[str]) -> list[CatalogMissingFieldRecord]:
        if not product_refs:
            return []
        return list(
            (
                await self._session.execute(
                    select(CatalogMissingFieldRecord)
                    .where(
                        CatalogMissingFieldRecord.workspace_id == workspace_id,
                        CatalogMissingFieldRecord.product_ref.in_(product_refs),
                        CatalogMissingFieldRecord.authority_state == "candidate",
                    )
                    .order_by(CatalogMissingFieldRecord.updated_at.desc(), CatalogMissingFieldRecord.id.desc())
                )
            ).scalars()
        )

    async def _open_conflicts(self, workspace_id: int, product_refs: list[str]) -> list[CatalogConflictRecord]:
        if not product_refs:
            return []
        return list(
            (
                await self._session.execute(
                    select(CatalogConflictRecord)
                    .where(
                        CatalogConflictRecord.workspace_id == workspace_id,
                        CatalogConflictRecord.product_ref.in_(product_refs),
                        CatalogConflictRecord.status == "open",
                    )
                    .order_by(CatalogConflictRecord.updated_at.desc(), CatalogConflictRecord.id.desc())
                )
            ).scalars()
        )

    async def _get_one(self, model: type, **filters: Any) -> Any | None:
        conditions = [getattr(model, key) == value for key, value in filters.items()]
        return await self._session.scalar(select(model).where(and_(*conditions)).limit(1))

    async def _rebuild_retrieval_index(
        self,
        *,
        workspace_id: int,
        fact_ids: list[str],
    ) -> int:
        from app.modules.business_brain.memory import BusinessBrainMemoryService
        from app.modules.commercial_spine.repository import CommercialSpineRepository

        result = await BusinessBrainMemoryService(
            repository=CommercialSpineRepository(self._session)
        ).index_structured_facts_for_search(
            workspace_id=workspace_id,
            fact_ids=fact_ids,
        )
        return len(result.source_units)


def _authority_state(row: BusinessBrainFactRecord) -> str:
    row_status = str(row.status or "").lower()
    if row_status in {"approved", "active", "confirmed"}:
        return "approved"
    if row_status in {"stale", "superseded", "expired"}:
        return "stale"
    raw = row.raw_fact if isinstance(row.raw_fact, dict) else {}
    state = str(raw.get("approval_state") or raw.get("authority_state") or row_status).lower()
    if state in {"approved", "active", "confirmed"}:
        return "approved"
    if state in {"candidate", "proposed", "pending"}:
        return "candidate"
    if state == "stale":
        return "stale"
    return "source"


def _fact_value(row: BusinessBrainFactRecord) -> dict[str, Any]:
    raw = row.raw_fact if isinstance(row.raw_fact, dict) else {}
    if isinstance(raw.get("value"), dict):
        value = dict(raw["value"])
        value.update(row.value or {})
        return value
    return dict(row.value or {})


def _product_ref(row: BusinessBrainFactRecord, value: dict[str, Any]) -> str | None:
    candidate = _first_text(value, "product_ref", "product_id", "entity_ref", "identity_ref")
    if candidate:
        return candidate
    if str(row.entity_ref or "").startswith("product:"):
        return row.entity_ref
    return None


def _product_ref_from_source_fact(row: CatalogSourceFactRecord, value: dict[str, Any]) -> str | None:
    candidate = _first_text(value, "product_ref", "product_id", "entity_ref")
    if candidate:
        return candidate
    if row.product_ref:
        return row.product_ref
    return None


def _first_text(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        raw = value.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            return str(raw)
        text = str(raw).strip()
        if text:
            return text
    return None


def _price_text(value: dict[str, Any]) -> str | None:
    raw_price = value.get("price")
    if isinstance(raw_price, dict):
        return _first_text(raw_price, "amount", "value", "price")
    return _first_text(value, "price", "price_text", "amount")


def _currency_text(value: dict[str, Any]) -> str | None:
    raw_price = value.get("price")
    if isinstance(raw_price, dict):
        return _first_text(raw_price, "currency")
    return _first_text(value, "currency")


def _stock_state_text(value: dict[str, Any]) -> str | None:
    raw_stock = value.get("stock")
    if isinstance(raw_stock, dict):
        return _first_text(raw_stock, "state", "stock_state", "availability")
    return _first_text(value, "stock", "stock_state", "availability")


def _offer_conflict_candidates(
    fact: CatalogSourceFactRecord,
    value: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    price = _first_text(value, "price", "price_text", "amount")
    currency = _first_text(value, "currency")
    if price:
        price_key = f"{price} {currency or ''}".strip()
        candidates.append(
            {
                "field": "price",
                "value_key": price_key,
                "price": price,
                "currency": currency,
                "source_fact_id": fact.source_fact_id,
                "source_refs": list(fact.source_refs or []),
            }
        )
    stock_state = _stock_state_text(value)
    if stock_state:
        candidates.append(
            {
                "field": "stock_state",
                "value_key": stock_state,
                "stock_state": stock_state,
                "source_fact_id": fact.source_fact_id,
                "source_refs": list(fact.source_refs or []),
            }
        )
    availability = _first_text(value, "availability")
    if availability and availability != stock_state:
        candidates.append(
            {
                "field": "availability",
                "value_key": availability,
                "availability": availability,
                "source_fact_id": fact.source_fact_id,
                "source_refs": list(fact.source_refs or []),
            }
        )
    return candidates


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item).strip())]


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _source_refs(row: BusinessBrainFactRecord) -> list[str]:
    raw = row.raw_fact if isinstance(row.raw_fact, dict) else {}
    refs = []
    for collection in (raw.get("source_refs"), row.source_refs):
        if isinstance(collection, list):
            refs.extend(str(ref) for ref in collection if str(ref).strip())
    return _unique_refs(refs)


def _unique_refs(refs: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip()))


def _product_search_text(product: CatalogProductRecord) -> str:
    return " ".join(
        [
            product.product_ref,
            product.name,
            product.description,
            " ".join(str(alias) for alias in list(product.aliases or [])),
        ]
    )


def _media_search_text(media: CatalogMediaRecord) -> str:
    return " ".join(
        [
            media.media_ref,
            media.caption,
            media.ocr_text,
            media.visual_summary,
            media.url or "",
            media.media_kind,
        ]
    )


def _text_score(query: str, text: str) -> float:
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokens(text))
    if not text_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens)
    return overlap / max(len(query_tokens), 1)


def _scope_ranked_products(
    ranked: list[tuple[float, CatalogProductRecord]],
) -> list[tuple[float, CatalogProductRecord]]:
    """Keep weaker related products out of an exact product authority bundle.

    Broad queries such as "sat bormi" should still return multiple SAT products
    when they tie. More specific queries such as "satstation platform narxi"
    should not drag in a loosely related priced product just because it shares
    one brand token.
    """
    if not ranked:
        return []
    best_score = ranked[0][0]
    if best_score <= 0:
        return []
    min_score = best_score * 0.65
    return [item for item in ranked if item[0] >= min_score]


def _tokens(value: str) -> list[str]:
    return [token for token in "".join(ch.lower() if ch.isalnum() else " " for ch in value).split() if token]


def _product_contract(row: CatalogProductRecord) -> CommerceCatalogProduct:
    return CommerceCatalogProduct(
        product_ref=row.product_ref,
        name=row.name,
        aliases=[str(alias) for alias in list(row.aliases or [])],
        description=row.description,
        authority_state=row.authority_state,  # type: ignore[arg-type]
        source_refs=list(row.source_refs or []),
        source_fact_ids=list(row.source_fact_ids or []),
    )


def _offer_contract(row: CatalogOfferRecord) -> CommerceCatalogOffer:
    return CommerceCatalogOffer(
        offer_ref=row.offer_ref,
        product_ref=row.product_ref,
        variant_ref=row.variant_ref,
        price=row.price,
        currency=row.currency,
        stock_state=row.stock_state,
        availability=row.availability,
        authority_state=row.authority_state,  # type: ignore[arg-type]
        source_refs=list(row.source_refs or []),
        source_fact_ids=list(row.source_fact_ids or []),
    )


def _media_contract(row: CatalogMediaRecord) -> CommerceCatalogMedia:
    return CommerceCatalogMedia(
        media_ref=row.media_ref,
        product_ref=row.product_ref,
        media_kind=row.media_kind,
        url=row.url,
        caption=row.caption,
        ocr_text=row.ocr_text,
        visual_summary=row.visual_summary,
        authority_state=row.authority_state,  # type: ignore[arg-type]
        source_refs=list(row.source_refs or []),
        source_fact_ids=list(row.source_fact_ids or []),
    )


def _missing_contract(row: CatalogMissingFieldRecord) -> CommerceCatalogMissingField:
    return CommerceCatalogMissingField(
        product_ref=row.product_ref,
        field=row.field,
        authority_state=row.authority_state,  # type: ignore[arg-type]
        source_refs=list(row.source_refs or []),
    )


def _conflict_contract(row: CatalogConflictRecord) -> CommerceCatalogConflict:
    return CommerceCatalogConflict(
        conflict_ref=row.conflict_ref,
        product_ref=row.product_ref,
        field=row.field,
        candidate_values=[dict(item) for item in list(row.candidate_values or [])],
        source_refs=list(row.source_refs or []),
        status=row.status,
    )
