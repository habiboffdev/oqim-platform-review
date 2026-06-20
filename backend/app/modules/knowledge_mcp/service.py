from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hermes_run import HermesRunEvent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.commercial_spine import BusinessBrainFactRecord
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.knowledge_mcp import (
    KnowledgeCandidateRecord,
    KnowledgeChunkRecord,
    KnowledgeCollectionRecord,
    KnowledgeItemRecord,
    KnowledgeSourceRecord,
)
from app.models.message import Message
from app.modules.agent_control.contracts import AgentControlActionInput
from app.modules.agent_control.service import AgentControlService
from app.modules.business_brain.contracts import BusinessBrainFactUpdateInput
from app.modules.business_brain.write_service import BusinessBrainWriteService
from app.modules.commerce_catalog.contracts import CommerceCatalogSearchResult
from app.modules.commerce_catalog.service import CommerceCatalogCoreService
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.knowledge_mcp.contracts import (
    KnowledgeAttachToCollectionInput,
    KnowledgeCandidate,
    KnowledgeCandidateInput,
    KnowledgeCandidateProposal,
    KnowledgeCatalogSearchRequest,
    KnowledgeChatMemorySearchRequest,
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeExplainSourcesRequest,
    KnowledgeGetItemRequest,
    KnowledgeItem,
    KnowledgeItemDetail,
    KnowledgeMediaSearchRequest,
    KnowledgeSaveInput,
    KnowledgeScope,
    KnowledgeSearchHit,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeSource,
    KnowledgeSourceExplanation,
    KnowledgeTagItemInput,
)
from app.modules.retrieval_core.contracts import RetrievalContextRequest
from app.modules.retrieval_core.indexing import (
    RetrievalIndexEmbeddingResult,
    RetrievalIndexEmbeddingService,
)
from app.modules.retrieval_core.service import RetrievalCoreService

_PROMOTABLE_ITEM_KINDS = {
    "note",
    "script",
    "doc",
    "chat",
    "media",
    "catalog",
    "faq",
    "policy",
    "source",
}
_KNOWLEDGE_RETRIEVAL_TOOLS = (
    "knowledge_search",
    "knowledge_search_chat_memory",
    "knowledge_search_catalog",
    "knowledge_search_media",
)


class KnowledgeMCPService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_service: Any | None = None,
        embed_on_write: bool = False,
        enable_semantic: bool = False,
    ) -> None:
        self._session = session
        self._agent_control = AgentControlService(CommercialSpineRepository(session))
        self._embed_on_write = embed_on_write
        self._enable_semantic = enable_semantic
        self._embedding_indexer = (
            RetrievalIndexEmbeddingService(embedding_service=embedding_service)
            if embedding_service is not None or embed_on_write or enable_semantic
            else None
        )

    async def stats(
        self,
        *,
        workspace_id: int,
        personal_owner_id: str | None = None,
        recent_limit: int = 10,
    ) -> dict[str, Any]:
        recent_limit = max(1, min(int(recent_limit), 50))
        scopes = [
            {
                "label": "business",
                "owner_type": "workspace",
                "owner_id": f"workspace:{workspace_id}",
                "workspace_id": workspace_id,
            }
        ]
        if personal_owner_id:
            scopes.append(
                {
                    "label": "personal",
                    "owner_type": "user",
                    "owner_id": personal_owner_id,
                    "workspace_id": None,
                }
            )

        item_filters = _scope_filters(KnowledgeItemRecord, scopes)
        source_filters = _scope_filters(KnowledgeSourceRecord, scopes)
        collection_filters = _scope_filters(KnowledgeCollectionRecord, scopes)
        chunk_filters = _scope_filters(KnowledgeChunkRecord, scopes)
        candidate_filters = _scope_filters(KnowledgeCandidateRecord, scopes)
        catalog_filters = _catalog_fact_filters(workspace_id)
        active_catalog_filters = _catalog_fact_filters(workspace_id, active_only=True)
        catalog_product_filters = _catalog_fact_filters(workspace_id, fact_type="catalog_product")
        active_catalog_product_filters = _catalog_fact_filters(
            workspace_id,
            fact_type="catalog_product",
            active_only=True,
        )

        latest_items = list(
            (
                await self._session.scalars(
                    select(KnowledgeItemRecord)
                    .where(or_(*item_filters))
                    .order_by(KnowledgeItemRecord.updated_at.desc(), KnowledgeItemRecord.id.desc())
                    .limit(recent_limit)
                )
            ).all()
        )
        latest_candidates = list(
            (
                await self._session.scalars(
                    select(KnowledgeCandidateRecord)
                    .where(or_(*candidate_filters))
                    .order_by(
                        KnowledgeCandidateRecord.updated_at.desc(),
                        KnowledgeCandidateRecord.id.desc(),
                    )
                    .limit(recent_limit)
                )
            ).all()
        )
        latest_tool_events = list(
            (
                await self._session.scalars(
                    select(HermesRunEvent)
                    .where(
                        HermesRunEvent.workspace_id == workspace_id,
                        HermesRunEvent.tool_name.like("knowledge%"),
                    )
                    .order_by(HermesRunEvent.created_at.desc(), HermesRunEvent.id.desc())
                    .limit(recent_limit)
                )
            ).all()
        )
        latest_catalog_facts = list(
            (
                await self._session.scalars(
                    select(BusinessBrainFactRecord)
                    .where(or_(*catalog_filters))
                    .order_by(
                        BusinessBrainFactRecord.updated_at.desc(),
                        BusinessBrainFactRecord.id.desc(),
                    )
                    .limit(recent_limit)
                )
            ).all()
        )

        totals = {
            "collections": await self._count(KnowledgeCollectionRecord, collection_filters),
            "sources": await self._count(KnowledgeSourceRecord, source_filters),
            "items": await self._count(KnowledgeItemRecord, item_filters),
            "chunks": await self._count(KnowledgeChunkRecord, chunk_filters),
            "candidates": await self._count(KnowledgeCandidateRecord, candidate_filters),
            "catalog_facts": await self._count(BusinessBrainFactRecord, catalog_filters),
            "catalog_active_facts": await self._count(
                BusinessBrainFactRecord,
                active_catalog_filters,
            ),
            "catalog_products": await self._count(
                BusinessBrainFactRecord,
                catalog_product_filters,
            ),
            "catalog_active_products": await self._count(
                BusinessBrainFactRecord,
                active_catalog_product_filters,
            ),
            "knowledge_tool_calls": await self._count_knowledge_tool_calls(workspace_id),
            "knowledge_actions": await self._count_knowledge_actions(workspace_id),
        }

        return {
            "schema_version": "knowledge_mcp_stats.v1",
            "workspace_id": workspace_id,
            "scopes": scopes,
            "empty": not any(
                totals[key]
                for key in (
                    "collections",
                    "sources",
                    "items",
                    "chunks",
                    "candidates",
                    "catalog_active_facts",
                )
            ),
            "totals": totals,
            "items_by_kind": await self._count_by(
                KnowledgeItemRecord,
                KnowledgeItemRecord.kind,
                item_filters,
            ),
            "items_by_authority_state": await self._count_by(
                KnowledgeItemRecord,
                KnowledgeItemRecord.authority_state,
                item_filters,
            ),
            "items_by_visibility": await self._count_by(
                KnowledgeItemRecord,
                KnowledgeItemRecord.visibility,
                item_filters,
            ),
            "sources_by_kind": await self._count_by(
                KnowledgeSourceRecord,
                KnowledgeSourceRecord.source_kind,
                source_filters,
            ),
            "chunks_by_embedding_state": await self._count_by(
                KnowledgeChunkRecord,
                KnowledgeChunkRecord.embedding_state,
                chunk_filters,
            ),
            "candidates_by_status": await self._count_by(
                KnowledgeCandidateRecord,
                KnowledgeCandidateRecord.status,
                candidate_filters,
            ),
            "catalog_adapter": {
                "collection_id": "business/catalog",
                "facts_by_type": await self._count_by(
                    BusinessBrainFactRecord,
                    BusinessBrainFactRecord.fact_type,
                    catalog_filters,
                ),
                "facts_by_status": await self._count_by(
                    BusinessBrainFactRecord,
                    BusinessBrainFactRecord.status,
                    catalog_filters,
                ),
                "recent_facts": [_stats_catalog_fact(row) for row in latest_catalog_facts],
            },
            "retrieval": await self._knowledge_retrieval_stats(workspace_id),
            "actions_by_lifecycle": await self._knowledge_actions_by_lifecycle(workspace_id),
            "recent_items": [_stats_item(row) for row in latest_items],
            "recent_candidates": [_stats_candidate(row) for row in latest_candidates],
            "recent_tool_calls": [_stats_tool_event(row) for row in latest_tool_events],
        }

    async def _count(self, model: Any, filters: list[Any]) -> int:
        return int(
            await self._session.scalar(
                select(func.count()).select_from(model).where(or_(*filters))
            )
            or 0
        )

    async def _count_by(self, model: Any, column: Any, filters: list[Any]) -> dict[str, int]:
        rows = (
            await self._session.execute(
                select(column, func.count())
                .select_from(model)
                .where(or_(*filters))
                .group_by(column)
                .order_by(column.asc())
            )
        ).all()
        return {str(key or "unknown"): int(count or 0) for key, count in rows}

    async def _knowledge_retrieval_stats(
        self,
        workspace_id: int,
        *,
        sample_limit: int = 500,
    ) -> dict[str, Any]:
        total_search_calls = int(
            await self._session.scalar(
                select(func.count())
                .select_from(HermesRunEvent)
                .where(
                    HermesRunEvent.workspace_id == workspace_id,
                    HermesRunEvent.tool_name.in_(_KNOWLEDGE_RETRIEVAL_TOOLS),
                )
            )
            or 0
        )
        rows = list(
            (
                await self._session.scalars(
                    select(HermesRunEvent)
                    .where(
                        HermesRunEvent.workspace_id == workspace_id,
                        HermesRunEvent.tool_name.in_(_KNOWLEDGE_RETRIEVAL_TOOLS),
                    )
                    .order_by(HermesRunEvent.created_at.desc(), HermesRunEvent.id.desc())
                    .limit(sample_limit)
                )
            ).all()
        )

        latencies: list[float] = []
        hit_counts: list[int] = []
        top_scores: list[float] = []
        retrieval_channels: dict[str, int] = {}
        by_tool: dict[str, dict[str, Any]] = {}
        zero_hit_count = 0
        with_hits_count = 0
        evidence_backed_count = 0
        degraded_count = 0
        semantic_enabled_count = 0
        rerank_enabled_count = 0
        citation_count = 0
        source_ref_count = 0

        for row in rows:
            payload = dict(row.payload or {})
            tool_name = str(row.tool_name or "unknown")
            bucket = by_tool.setdefault(
                tool_name,
                {
                    "calls": 0,
                    "degraded": 0,
                    "zero_hit": 0,
                    "with_hits": 0,
                    "evidence_backed": 0,
                    "_latencies": [],
                    "_hit_counts": [],
                },
            )
            bucket["calls"] += 1
            if row.tool_state != "ok":
                bucket["degraded"] += 1
                degraded_count += 1

            latency_ms = _payload_float(payload.get("latency_ms"))
            if latency_ms is not None:
                latencies.append(latency_ms)
                bucket["_latencies"].append(latency_ms)

            hit_count = _payload_int(payload.get("hit_count"), default=0)
            hit_counts.append(hit_count)
            bucket["_hit_counts"].append(hit_count)
            if hit_count == 0:
                zero_hit_count += 1
                bucket["zero_hit"] += 1
            else:
                with_hits_count += 1
                bucket["with_hits"] += 1

            if bool(payload.get("evidence_backed")):
                evidence_backed_count += 1
                bucket["evidence_backed"] += 1

            citation_count += _payload_int(
                payload.get("citation_count"),
                default=len(list(payload.get("citations") or [])),
            )
            source_ref_count += _payload_int(payload.get("source_ref_count"), default=0)

            top_score = _payload_float(payload.get("top_score"))
            if top_score is not None:
                top_scores.append(top_score)

            if payload.get("enable_semantic") is True:
                semantic_enabled_count += 1
            if payload.get("enable_rerank") is True:
                rerank_enabled_count += 1

            channels = list(payload.get("retrieval_channels") or [])
            for citation in list(payload.get("citations") or []):
                if isinstance(citation, dict):
                    channels.extend(list(citation.get("retrieval_channels") or []))
            for channel in dict.fromkeys(str(channel) for channel in channels if channel):
                retrieval_channels[channel] = retrieval_channels.get(channel, 0) + 1

        summarized_by_tool: dict[str, dict[str, Any]] = {}
        for tool_name, bucket in by_tool.items():
            calls = int(bucket["calls"])
            with_hits = int(bucket["with_hits"])
            summarized_by_tool[tool_name] = {
                "calls": calls,
                "degraded": int(bucket["degraded"]),
                "zero_hit": int(bucket["zero_hit"]),
                "avg_latency_ms": _avg(bucket["_latencies"]),
                "p95_latency_ms": _percentile(bucket["_latencies"], 0.95),
                "avg_hit_count": _avg(bucket["_hit_counts"]),
                "zero_hit_rate": _rate(bucket["zero_hit"], calls),
                "evidence_backed_rate": _rate(bucket["evidence_backed"], with_hits),
            }

        eager_context = await self._runtime_context_retrieval_stats(workspace_id)
        return {
            "schema_version": "knowledge_retrieval_stats.v1",
            "total_search_calls": total_search_calls,
            "sample_size": len(rows),
            "sample_limit": sample_limit,
            "latency": {
                "known_samples": len(latencies),
                "avg_ms": _avg(latencies),
                "p50_ms": _percentile(latencies, 0.50),
                "p95_ms": _percentile(latencies, 0.95),
                "max_ms": max(latencies) if latencies else None,
            },
            "quality_proxy": {
                "avg_hit_count": _avg(hit_counts),
                "zero_hit_rate": _rate(zero_hit_count, len(rows)),
                "evidence_backed_rate": _rate(evidence_backed_count, with_hits_count),
                "degraded_rate": _rate(degraded_count, len(rows)),
                "citation_count": citation_count,
                "source_ref_count": source_ref_count,
                "avg_top_score": _avg(top_scores),
            },
            "features": {
                "semantic_enabled_rate": _rate(semantic_enabled_count, len(rows)),
                "rerank_enabled_rate": _rate(rerank_enabled_count, len(rows)),
                "retrieval_channels": retrieval_channels,
            },
            "by_tool": summarized_by_tool,
            "eager_context": eager_context,
        }

    async def _runtime_context_retrieval_stats(
        self,
        workspace_id: int,
        *,
        sample_limit: int = 500,
    ) -> dict[str, Any]:
        total_context_gathers = int(
            await self._session.scalar(
                select(func.count())
                .select_from(HermesRunEvent)
                .where(
                    HermesRunEvent.workspace_id == workspace_id,
                    HermesRunEvent.kind == "context_gathered",
                )
            )
            or 0
        )
        rows = list(
            (
                await self._session.scalars(
                    select(HermesRunEvent)
                    .where(
                        HermesRunEvent.workspace_id == workspace_id,
                        HermesRunEvent.kind == "context_gathered",
                    )
                    .order_by(HermesRunEvent.created_at.desc(), HermesRunEvent.id.desc())
                    .limit(sample_limit)
                )
            ).all()
        )
        total_latencies: list[float] = []
        grounding_latencies: list[float] = []
        candidate_counts: list[int] = []
        truth_counts: list[int] = []
        source_ref_counts: list[int] = []
        retrieval_channels: dict[str, int] = {}
        degraded_count = 0
        evidence_backed_count = 0
        with_candidates_count = 0

        for row in rows:
            payload = dict(row.payload or {})
            metrics = dict(payload.get("context_metrics") or {})
            latency = dict(metrics.get("latency") or {})
            grounding = dict(metrics.get("grounding") or {})

            total_latency = _payload_float(
                latency.get("total_ms", payload.get("latency_ms"))
            )
            if total_latency is not None:
                total_latencies.append(total_latency)
            grounding_latency = _payload_float(
                latency.get("grounding_ms", payload.get("grounding_ms"))
            )
            if grounding_latency is not None:
                grounding_latencies.append(grounding_latency)

            candidate_count = _payload_int(
                grounding.get("candidate_count", payload.get("candidate_count")),
                default=0,
            )
            candidate_counts.append(candidate_count)
            if candidate_count:
                with_candidates_count += 1

            truth_counts.append(
                _payload_int(
                    grounding.get("truth_evidence_count", payload.get("grounding_lines")),
                    default=0,
                )
            )
            source_ref_counts.append(
                _payload_int(
                    grounding.get("source_ref_count", payload.get("source_ref_count")),
                    default=0,
                )
            )
            if bool(grounding.get("evidence_backed", payload.get("evidence_backed"))):
                evidence_backed_count += 1
            if _payload_int(grounding.get("degraded_count"), default=0) > 0:
                degraded_count += 1
            for channel in dict.fromkeys(
                str(channel)
                for channel in list(
                    grounding.get(
                        "retrieval_channels",
                        payload.get("retrieval_channels") or [],
                    )
                    or []
                )
                if channel
            ):
                retrieval_channels[channel] = retrieval_channels.get(channel, 0) + 1

        return {
            "schema_version": "eager_context_retrieval_stats.v1",
            "total_context_gathers": total_context_gathers,
            "sample_size": len(rows),
            "sample_limit": sample_limit,
            "latency": {
                "known_samples": len(total_latencies),
                "avg_ms": _avg(total_latencies),
                "p50_ms": _percentile(total_latencies, 0.50),
                "p95_ms": _percentile(total_latencies, 0.95),
                "max_ms": max(total_latencies) if total_latencies else None,
                "grounding_avg_ms": _avg(grounding_latencies),
                "grounding_p95_ms": _percentile(grounding_latencies, 0.95),
            },
            "quality_proxy": {
                "avg_candidate_count": _avg(candidate_counts),
                "avg_truth_evidence_count": _avg(truth_counts),
                "avg_source_ref_count": _avg(source_ref_counts),
                "evidence_backed_rate": _rate(evidence_backed_count, with_candidates_count),
                "degraded_rate": _rate(degraded_count, len(rows)),
            },
            "retrieval_channels": retrieval_channels,
        }

    async def _count_knowledge_tool_calls(self, workspace_id: int) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(HermesRunEvent)
                .where(
                    HermesRunEvent.workspace_id == workspace_id,
                    HermesRunEvent.tool_name.like("knowledge%"),
                )
            )
            or 0
        )

    async def _count_knowledge_actions(self, workspace_id: int) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(CommercialActionProposalRecord)
                .where(
                    CommercialActionProposalRecord.workspace_id == workspace_id,
                    CommercialActionProposalRecord.action_type.like("knowledge.%"),
                )
            )
            or 0
        )

    async def _knowledge_actions_by_lifecycle(self, workspace_id: int) -> dict[str, int]:
        rows = (
            await self._session.execute(
                select(CommercialActionProposalRecord.lifecycle_state, func.count())
                .where(
                    CommercialActionProposalRecord.workspace_id == workspace_id,
                    CommercialActionProposalRecord.action_type.like("knowledge.%"),
                )
                .group_by(CommercialActionProposalRecord.lifecycle_state)
                .order_by(CommercialActionProposalRecord.lifecycle_state.asc())
            )
        ).all()
        return {str(key or "unknown"): int(count or 0) for key, count in rows}

    async def save_item(self, payload: KnowledgeSaveInput) -> KnowledgeItem:
        item_id = f"knowledge:{uuid.uuid4().hex}"
        source_id = f"knowledge_source:{uuid.uuid4().hex}"
        chunk_id = f"knowledge_chunk:{uuid.uuid4().hex}"
        checksum = hashlib.sha256(payload.body_text.encode("utf-8")).hexdigest()
        source = KnowledgeSource(
            source_id=source_id,
            scope=payload.scope,
            source_kind=payload.source_kind,
            external_ref=payload.external_ref,
            checksum=checksum,
            raw_content=payload.body_text,
            metadata={"correlation_id": payload.correlation_id},
        )
        item = KnowledgeItem(
            item_id=item_id,
            scope=payload.scope,
            kind=payload.kind,
            title=payload.title.strip(),
            body_text=payload.body_text,
            source_refs=[source_id],
            collection_ids=_unique(payload.collection_ids),
            tags=_unique(payload.tags),
            authority_state=payload.authority_state,
            visibility=payload.visibility,
            created_by=payload.created_by,
            created_by_ref=payload.created_by_ref,
            metadata=dict(payload.metadata),
        )
        chunk = KnowledgeChunk(
            chunk_id=chunk_id,
            item_id=item_id,
            source_id=source_id,
            scope=payload.scope,
            text=payload.body_text,
            contextual_prefix=f"{item.kind}: {item.title}",
            citation={
                "source_id": source_id,
                "item_id": item_id,
                "title": item.title,
            },
        )
        for collection_id in item.collection_ids:
            await self.ensure_collection(
                scope=payload.scope,
                collection_id=collection_id,
                title=collection_id,
            )
        self._session.add(_source_record(source))
        self._session.add(_item_record(item))
        self._session.add(await self._chunk_record_for(chunk))
        await self._session.flush()
        return item

    async def ensure_collection(
        self,
        *,
        scope: KnowledgeScope,
        collection_id: str,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeCollection:
        existing = await self._session.scalar(
            select(KnowledgeCollectionRecord).where(
                KnowledgeCollectionRecord.owner_type == scope.owner_type,
                KnowledgeCollectionRecord.owner_id == scope.owner_id,
                KnowledgeCollectionRecord.collection_id == collection_id,
            )
        )
        if existing is not None:
            return _collection_from_record(existing)
        collection = KnowledgeCollection(
            collection_id=collection_id,
            scope=scope,
            title=title.strip() or collection_id,
            description=description,
            tags=_unique(tags or []),
            metadata=dict(metadata or {}),
        )
        self._session.add(_collection_record(collection))
        await self._session.flush()
        return collection

    async def get_item(self, request: KnowledgeGetItemRequest) -> KnowledgeItemDetail | None:
        row = await self._item_row(
            scope=request.scope,
            item_id=request.item_id,
        )
        if row is None:
            return None
        item = _item_from_record(row)
        sources = await self._source_records(scope=request.scope, source_refs=item.source_refs)
        chunks = await self._chunk_records(scope=request.scope, item_id=item.item_id)
        return KnowledgeItemDetail(
            item=item,
            sources=[_source_from_record(source) for source in sources],
            chunks=[_chunk_from_record(chunk) for chunk in chunks],
        )

    async def explain_sources(
        self,
        request: KnowledgeExplainSourcesRequest,
    ) -> KnowledgeSourceExplanation | None:
        detail = await self.get_item(
            KnowledgeGetItemRequest(
                scope=request.scope,
                item_id=request.item_id,
            )
        )
        if detail is None:
            return None
        return KnowledgeSourceExplanation(
            item_id=detail.item.item_id,
            source_refs=list(detail.item.source_refs),
            sources=list(detail.sources),
            citations=[dict(chunk.citation) for chunk in detail.chunks if chunk.citation],
            chunks=list(detail.chunks),
        )

    async def attach_to_collection(
        self,
        payload: KnowledgeAttachToCollectionInput,
    ) -> KnowledgeItem:
        row = await self._require_item_row(
            scope=payload.scope,
            item_id=payload.item_id,
        )
        additions = _unique(payload.collection_ids)
        for collection_id in additions:
            await self.ensure_collection(
                scope=payload.scope,
                collection_id=collection_id,
                title=collection_id,
            )
        row.collection_ids = _unique([*list(row.collection_ids or []), *additions])
        await self._session.flush()
        return _item_from_record(row)

    async def tag_item(self, payload: KnowledgeTagItemInput) -> KnowledgeItem:
        row = await self._require_item_row(
            scope=payload.scope,
            item_id=payload.item_id,
        )
        row.tags = _unique([*list(row.tags or []), *_unique(payload.tags)])
        await self._session.flush()
        return _item_from_record(row)

    async def search(self, request: KnowledgeSearchRequest) -> KnowledgeSearchResult:
        rows = await self._session.scalars(
            select(KnowledgeItemRecord)
            .where(
                KnowledgeItemRecord.owner_type == request.scope.owner_type,
                KnowledgeItemRecord.owner_id == request.scope.owner_id,
            )
            .order_by(KnowledgeItemRecord.updated_at.desc(), KnowledgeItemRecord.id.desc())
            .limit(250)
        )
        query_terms = _terms(request.query)
        hits_by_item_id: dict[str, KnowledgeSearchHit] = {}
        for row in rows:
            if not _item_row_matches_request(row, request):
                continue
            score = _score(query_terms, row.title, row.body_text, row.tags)
            if score <= 0:
                continue
            item = _item_from_record(row)
            _merge_search_hit(
                hits_by_item_id,
                item=item,
                score=score,
                citation={
                    "type": "knowledge_item",
                    "item_id": item.item_id,
                    "source_refs": item.source_refs,
                    "title": item.title,
                    "retrieval_scores": {"lexical": score},
                },
            )

        query_embedding = await self._query_embedding(request)
        if query_embedding is not None:
            for row, chunk, semantic_score, distance in await self._semantic_chunk_rows(
                request=request,
                query_embedding=query_embedding,
            ):
                if not _item_row_matches_request(row, request):
                    continue
                item = _item_from_record(row)
                _merge_search_hit(
                    hits_by_item_id,
                    item=item,
                    score=semantic_score,
                    citation={
                        "type": "knowledge_chunk",
                        "item_id": item.item_id,
                        "chunk_id": chunk.chunk_id,
                        "source_id": chunk.source_id,
                        "source_refs": item.source_refs,
                        "title": item.title,
                        "retrieval_scores": {"semantic": semantic_score},
                        "distance": distance,
                    },
                )

        hits = list(hits_by_item_id.values())
        hits.sort(key=lambda hit: (-hit.score, hit.item.title))
        return KnowledgeSearchResult(hits=hits[: request.limit])

    async def search_chat_memory(
        self,
        request: KnowledgeChatMemorySearchRequest,
    ) -> KnowledgeSearchResult:
        filters = [
            Conversation.workspace_id == request.workspace_id,
            Message.is_deleted.is_(False),
        ]
        if request.conversation_id is not None:
            filters.append(Conversation.id == request.conversation_id)
        if request.sender_types:
            filters.append(Message.sender_type.in_(request.sender_types))

        rows = await self._session.execute(
            select(Message, Conversation, Customer)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .join(Customer, Conversation.customer_id == Customer.id)
            .where(*filters)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(500)
        )
        scope = KnowledgeScope(
            owner_type="workspace",
            owner_id=f"workspace:{request.workspace_id}",
            workspace_id=request.workspace_id,
        )
        query_terms = _terms(request.query)
        hits: list[KnowledgeSearchHit] = []
        for message, conversation, customer in rows:
            tags = _chat_tags(message)
            score = _chat_score(
                query_terms,
                message.content,
                message.transcription,
                message.media_description,
                conversation.summary,
                customer.display_name,
                *tags,
            )
            if score <= 0:
                continue
            source_refs = _chat_source_refs(message, conversation)
            citation = _chat_citation(
                message=message,
                conversation=conversation,
                customer=customer,
                source_refs=source_refs,
            )
            item = KnowledgeItem(
                item_id=f"chat_message:{message.id}",
                scope=scope,
                kind="chat",
                title=_chat_title(message=message, conversation=conversation, customer=customer),
                body_text=_message_search_text(message),
                source_refs=source_refs,
                collection_ids=["chat_memory"],
                tags=tags,
                authority_state="source",
                visibility="workspace",
                created_by="system",
                created_by_ref="chat_memory",
                metadata={
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "sender_type": message.sender_type,
                    "telegram_chat_id": conversation.telegram_chat_id,
                    "telegram_message_id": message.telegram_message_id,
                    "created_at": _iso(message.created_at),
                },
            )
            hits.append(
                KnowledgeSearchHit(
                    item=item,
                    score=score,
                    citations=[citation],
                )
            )
        hits.sort(
            key=lambda hit: (
                -hit.score,
                str(hit.item.metadata.get("created_at") or ""),
                hit.item.item_id,
            ),
            reverse=False,
        )
        return KnowledgeSearchResult(hits=hits[: request.limit])

    async def search_catalog(
        self,
        request: KnowledgeCatalogSearchRequest,
    ) -> KnowledgeSearchResult:
        typed = await CommerceCatalogCoreService(self._session).search_authority(
            workspace_id=request.workspace_id,
            query=request.query,
            include_media=request.include_media,
            limit=request.limit,
        )
        if typed.products or typed.offers or typed.media:
            return _typed_catalog_result_to_knowledge_search(
                workspace_id=request.workspace_id,
                collection_id="business/catalog",
                result=typed,
                limit=request.limit,
            )
        fact_types = ["catalog_product", "catalog_variant", "catalog_offer"]
        if request.include_media:
            fact_types.append("catalog_media")
        result = await RetrievalCoreService(
            repository=CommercialSpineRepository(self._session)
        ).retrieve_contextual(
            RetrievalContextRequest(
                workspace_id=request.workspace_id,
                requested_fact_types=fact_types,
                query_text=request.query,
                query_modalities=list(request.query_modalities),
                enable_semantic=request.enable_semantic,
                enable_query_rewrite=False,
                enable_agentic_search=False,
                enable_rerank=request.enable_rerank,
                include_proposed=False,
                include_source_units=True,
                limit=request.limit,
            )
        )
        return _retrieval_result_to_knowledge_search(
            workspace_id=request.workspace_id,
            collection_id="business/catalog",
            candidates=result.candidates,
            trace=result.trace.model_dump(mode="json"),
            limit=request.limit,
        )

    async def search_media(
        self,
        request: KnowledgeMediaSearchRequest,
    ) -> KnowledgeSearchResult:
        typed = await CommerceCatalogCoreService(self._session).search_authority(
            workspace_id=request.workspace_id,
            query=request.query,
            include_media=True,
            limit=request.limit,
        )
        if typed.media:
            return _typed_catalog_result_to_knowledge_search(
                workspace_id=request.workspace_id,
                collection_id="business/media",
                result=typed.model_copy(update={"products": [], "offers": []}),
                limit=request.limit,
            )
        result = await RetrievalCoreService(
            repository=CommercialSpineRepository(self._session)
        ).retrieve_contextual(
            RetrievalContextRequest(
                workspace_id=request.workspace_id,
                requested_fact_types=["catalog_product"],
                query_text=request.query,
                query_modalities=list(request.query_modalities),
                enable_semantic=request.enable_semantic,
                enable_query_rewrite=False,
                enable_agentic_search=False,
                enable_rerank=request.enable_rerank,
                include_proposed=False,
                include_source_units=True,
                limit=request.limit,
            )
        )
        return _retrieval_result_to_knowledge_search(
            workspace_id=request.workspace_id,
            collection_id="business/media",
            candidates=[
                candidate
                for candidate in result.candidates
                if _knowledge_kind_for_fact_type(str(candidate.fact_type)) == "media"
            ],
            trace=result.trace.model_dump(mode="json"),
            limit=request.limit,
        )

    async def propose_candidate(
        self,
        payload: KnowledgeCandidateInput,
    ) -> KnowledgeCandidateProposal:
        if payload.scope.owner_type != "workspace" or payload.scope.workspace_id is None:
            raise ValueError("authority candidates require workspace knowledge scope")
        candidate_id = f"knowledge_candidate:{uuid.uuid4().hex}"
        action = await self._agent_control.create_action(
            AgentControlActionInput(
                workspace_id=payload.scope.workspace_id,
                user_id=payload.scope.owner_id,
                action_kind="knowledge.promote",
                target_ref=candidate_id,
                hermes_run_id=payload.hermes_run_id,
                proposed_payload={
                    "source_id": payload.source_id,
                    "proposed_kind": payload.proposed_kind,
                    "proposed_payload": payload.proposed_payload,
                },
                risk_level="medium",
                evidence_refs=payload.evidence_refs,
                approval_required=True,
                correlation_id=payload.correlation_id,
                idempotency_key=payload.idempotency_key,
            )
        )
        candidate = KnowledgeCandidate(
            candidate_id=candidate_id,
            scope=payload.scope,
            source_id=payload.source_id,
            proposed_kind=payload.proposed_kind,
            proposed_payload=dict(payload.proposed_payload),
            evidence_refs=list(payload.evidence_refs),
            confidence=payload.confidence,
            status="pending",
            agent_control_action_id=action.action_id,
            metadata={
                "created_by_ref": payload.created_by_ref,
                "hermes_run_id": payload.hermes_run_id,
            },
        )
        self._session.add(_candidate_record(candidate))
        await self._session.flush()
        return KnowledgeCandidateProposal(candidate=candidate, action=action)

    async def approve_candidate_action(
        self,
        *,
        workspace_id: int,
        action_id: str,
        actor_ref: str,
        correlation_id: str,
    ) -> KnowledgeCandidateProposal:
        row = await self._candidate_by_action(
            workspace_id=workspace_id,
            action_id=action_id,
        )
        if row is None:
            raise ValueError("knowledge_candidate_not_found")
        await self._agent_control.approve(
            workspace_id=workspace_id,
            action_id=action_id,
            actor_ref=actor_ref,
            correlation_id=correlation_id,
        )
        await self._promote_candidate(row, actor_ref=actor_ref, correlation_id=correlation_id)
        action = await self._agent_control.get_action(workspace_id=workspace_id, action_id=action_id)
        if action is None:
            raise ValueError("agent_control_action_not_found")
        return KnowledgeCandidateProposal(candidate=_candidate_from_record(row), action=action)

    async def reject_candidate_action(
        self,
        *,
        workspace_id: int,
        action_id: str,
        actor_ref: str,
        correlation_id: str,
    ) -> KnowledgeCandidateProposal:
        row = await self._candidate_by_action(
            workspace_id=workspace_id,
            action_id=action_id,
        )
        if row is None:
            raise ValueError("knowledge_candidate_not_found")
        await self._agent_control.reject(
            workspace_id=workspace_id,
            action_id=action_id,
            actor_ref=actor_ref,
            correlation_id=correlation_id,
        )
        row.status = "rejected"
        row.metadata_json = {
            **dict(row.metadata_json or {}),
            "rejected_by": actor_ref,
            "rejection_correlation_id": correlation_id,
            "rejected_at": datetime.now(UTC).isoformat(),
        }
        await self._session.flush()
        action = await self._agent_control.get_action(workspace_id=workspace_id, action_id=action_id)
        if action is None:
            raise ValueError("agent_control_action_not_found")
        return KnowledgeCandidateProposal(candidate=_candidate_from_record(row), action=action)

    async def _candidate_by_action(
        self,
        *,
        workspace_id: int,
        action_id: str,
    ) -> KnowledgeCandidateRecord | None:
        return await self._session.scalar(
            select(KnowledgeCandidateRecord).where(
                KnowledgeCandidateRecord.workspace_id == workspace_id,
                KnowledgeCandidateRecord.agent_control_action_id == action_id,
            )
        )

    async def _item_row(
        self,
        *,
        scope: KnowledgeScope,
        item_id: str,
    ) -> KnowledgeItemRecord | None:
        return await self._session.scalar(
            select(KnowledgeItemRecord).where(
                KnowledgeItemRecord.owner_type == scope.owner_type,
                KnowledgeItemRecord.owner_id == scope.owner_id,
                KnowledgeItemRecord.item_id == item_id,
            )
        )

    async def _require_item_row(
        self,
        *,
        scope: KnowledgeScope,
        item_id: str,
    ) -> KnowledgeItemRecord:
        row = await self._item_row(scope=scope, item_id=item_id)
        if row is None:
            raise ValueError("knowledge_item_not_found")
        return row

    async def _source_records(
        self,
        *,
        scope: KnowledgeScope,
        source_refs: list[str],
    ) -> list[KnowledgeSourceRecord]:
        refs = _unique(source_refs)
        if not refs:
            return []
        rows = await self._session.scalars(
            select(KnowledgeSourceRecord)
            .where(
                KnowledgeSourceRecord.owner_type == scope.owner_type,
                KnowledgeSourceRecord.owner_id == scope.owner_id,
                KnowledgeSourceRecord.source_id.in_(refs),
            )
            .order_by(KnowledgeSourceRecord.id.asc())
        )
        return list(rows)

    async def _chunk_records(
        self,
        *,
        scope: KnowledgeScope,
        item_id: str,
    ) -> list[KnowledgeChunkRecord]:
        rows = await self._session.scalars(
            select(KnowledgeChunkRecord)
            .where(
                KnowledgeChunkRecord.owner_type == scope.owner_type,
                KnowledgeChunkRecord.owner_id == scope.owner_id,
                KnowledgeChunkRecord.item_id == item_id,
            )
            .order_by(KnowledgeChunkRecord.id.asc())
        )
        return list(rows)

    async def _promote_candidate(
        self,
        row: KnowledgeCandidateRecord,
        *,
        actor_ref: str,
        correlation_id: str,
    ) -> None:
        metadata = dict(row.metadata_json or {})
        if metadata.get("promoted_item_id") and metadata.get("brain_fact_id"):
            row.status = "approved"
            await self._session.flush()
            return

        scope = _scope(row)
        item_kind = _knowledge_item_kind(row.proposed_kind)
        item = KnowledgeItem(
            item_id=f"knowledge:{uuid.uuid4().hex}",
            scope=scope,
            kind=item_kind,  # type: ignore[arg-type]
            title=_candidate_title(row),
            body_text=_candidate_body(row),
            source_refs=_unique([row.source_id, *list(row.evidence_refs or [])]),
            collection_ids=[f"business/{row.proposed_kind}"],
            tags=_unique([row.proposed_kind, "approved"]),
            authority_state="approved",
            visibility="workspace",
            created_by="agent",
            created_by_ref=str(metadata.get("created_by_ref") or "agent"),
            metadata={
                "candidate_id": row.candidate_id,
                "approved_by": actor_ref,
                "approval_correlation_id": correlation_id,
            },
        )
        chunk = KnowledgeChunk(
            chunk_id=f"knowledge_chunk:{uuid.uuid4().hex}",
            item_id=item.item_id,
            source_id=row.source_id,
            scope=scope,
            text=item.body_text,
            contextual_prefix=f"{item.kind}: {item.title}",
            citation={
                "source_id": row.source_id,
                "item_id": item.item_id,
                "candidate_id": row.candidate_id,
                "title": item.title,
            },
        )
        await self.ensure_collection(
            scope=scope,
            collection_id=item.collection_ids[0],
            title=item.collection_ids[0],
            tags=[row.proposed_kind, "approved"],
        )
        self._session.add(_item_record(item))
        self._session.add(await self._chunk_record_for(chunk))

        brain_fact_type = _brain_fact_type(row.proposed_kind)
        brain_value = _brain_fact_value(row, knowledge_item_id=item.item_id)
        fact_id = _brain_fact_id(
            row,
            fact_type=brain_fact_type,
            value=brain_value,
        )
        await BusinessBrainWriteService(
            repository=CommercialSpineRepository(self._session)
        ).apply(
            BusinessBrainFactUpdateInput(
                update_id=f"knowledge_candidate:{row.candidate_id}:approved",
                fact_id=fact_id,
                workspace_id=int(row.workspace_id or 0),
                fact_type=brain_fact_type,
                entity_ref=_brain_entity_ref(fact_id=fact_id, value=brain_value),
                value=brain_value,
                confidence=float(row.confidence or 0.0),
                status="active",
                risk_tier="medium",
                source="ai_proposal",
                approval_state="confirmed",
                source_refs=item.source_refs,
                idempotency_key=f"knowledge-candidate:{row.candidate_id}:approved",
                applied_at=datetime.now(UTC),
                actor_type="owner",
                actor_ref=actor_ref,
                correlation_id=correlation_id,
            )
        )
        if _is_catalog_fact_type(brain_fact_type):
            await CommerceCatalogCoreService(self._session).project_from_business_brain(
                workspace_id=int(row.workspace_id or 0),
                commit=False,
                rebuild_retrieval_index=True,
            )
        row.status = "approved"
        row.metadata_json = {
            **metadata,
            "promoted_item_id": item.item_id,
            "brain_fact_id": fact_id,
            "approved_by": actor_ref,
            "approval_correlation_id": correlation_id,
            "approved_at": datetime.now(UTC).isoformat(),
        }
        await self._session.flush()

    async def _chunk_record_for(self, chunk: KnowledgeChunk) -> KnowledgeChunkRecord:
        record = _chunk_record(chunk)
        embedding_result = await self._embed_chunk(chunk)
        if embedding_result is not None:
            _apply_embedding(record, embedding_result)
        return record

    async def _embed_chunk(
        self,
        chunk: KnowledgeChunk,
    ) -> RetrievalIndexEmbeddingResult | None:
        if self._embedding_indexer is None:
            return None
        return await self._embedding_indexer.embed_text(
            _chunk_embedding_text(chunk),
            enabled=self._embed_on_write,
            context=f"knowledge_chunk:{chunk.chunk_id}",
        )

    async def _query_embedding(self, request: KnowledgeSearchRequest) -> list[float] | None:
        if request.query_embedding:
            return list(request.query_embedding)
        if not request.enable_semantic or not self._enable_semantic or self._embedding_indexer is None:
            return None
        result = await self._embedding_indexer.embed_text(
            request.query,
            enabled=True,
            context="knowledge_search_query",
        )
        if result.embedding_state != "ready" or result.embedding is None:
            return None
        return list(result.embedding)

    async def _semantic_chunk_rows(
        self,
        *,
        request: KnowledgeSearchRequest,
        query_embedding: list[float],
    ) -> list[tuple[KnowledgeItemRecord, KnowledgeChunkRecord, float, float]]:
        from app.brain.embedding_service import halfvec_cosine

        distance = halfvec_cosine(KnowledgeChunkRecord.embedding, query_embedding)
        statement = (
            select(KnowledgeItemRecord, KnowledgeChunkRecord, distance.label("distance"))
            .join(
                KnowledgeItemRecord,
                and_(
                    KnowledgeItemRecord.owner_type == KnowledgeChunkRecord.owner_type,
                    KnowledgeItemRecord.owner_id == KnowledgeChunkRecord.owner_id,
                    KnowledgeItemRecord.item_id == KnowledgeChunkRecord.item_id,
                ),
            )
            .where(
                KnowledgeChunkRecord.owner_type == request.scope.owner_type,
                KnowledgeChunkRecord.owner_id == request.scope.owner_id,
                KnowledgeChunkRecord.embedding.is_not(None),
                KnowledgeChunkRecord.embedding_state == "ready",
            )
            .order_by(distance.asc(), KnowledgeChunkRecord.id.asc())
            .limit(max(request.limit * 8, 50))
        )
        rows = (await self._session.execute(statement)).all()
        results: list[tuple[KnowledgeItemRecord, KnowledgeChunkRecord, float, float]] = []
        for item_row, chunk_row, raw_distance in rows:
            distance_value = float(raw_distance or 0.0)
            score = max(0.0, min(1.0, 1.0 - distance_value))
            if score <= 0:
                continue
            results.append((item_row, chunk_row, score, distance_value))
        return results


def _scope_filters(model: Any, scopes: list[dict[str, Any]]) -> list[Any]:
    return [
        and_(
            model.owner_type == scope["owner_type"],
            model.owner_id == scope["owner_id"],
        )
        for scope in scopes
    ] or [model.id < 0]


def _payload_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _payload_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return default
    return default


def _avg(values: list[int] | list[float]) -> float | None:
    if not values:
        return None
    return round(float(sum(values) / len(values)), 2)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(float(ordered[index]), 2)


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator / denominator), 4)


def _catalog_fact_filters(
    workspace_id: int,
    *,
    fact_type: str | None = None,
    active_only: bool = False,
) -> list[Any]:
    conditions = [
        BusinessBrainFactRecord.workspace_id == workspace_id,
        BusinessBrainFactRecord.fact_type.like("catalog%"),
    ]
    if fact_type is not None:
        conditions.append(BusinessBrainFactRecord.fact_type == fact_type)
    if active_only:
        conditions.append(BusinessBrainFactRecord.status.in_(("active", "confirmed", "approved")))
    return [and_(*conditions)]


def _stats_item(row: KnowledgeItemRecord) -> dict[str, Any]:
    return {
        "item_id": row.item_id,
        "owner_type": row.owner_type,
        "owner_id": row.owner_id,
        "workspace_id": row.workspace_id,
        "kind": row.kind,
        "title": row.title,
        "authority_state": row.authority_state,
        "visibility": row.visibility,
        "collection_ids": list(row.collection_ids or []),
        "tags": list(row.tags or []),
        "source_refs": list(row.source_refs or []),
        "updated_at": _iso(row.updated_at),
    }


def _stats_candidate(row: KnowledgeCandidateRecord) -> dict[str, Any]:
    return {
        "candidate_id": row.candidate_id,
        "owner_type": row.owner_type,
        "owner_id": row.owner_id,
        "workspace_id": row.workspace_id,
        "source_id": row.source_id,
        "proposed_kind": row.proposed_kind,
        "status": row.status,
        "confidence": float(row.confidence or 0.0),
        "evidence_refs": list(row.evidence_refs or []),
        "agent_control_action_id": row.agent_control_action_id,
        "updated_at": _iso(row.updated_at),
    }


def _stats_catalog_fact(row: BusinessBrainFactRecord) -> dict[str, Any]:
    value = dict(row.value or {})
    title = _title_for_fact(
        fact_id=row.fact_id,
        fact_type=row.fact_type,
        value=value,
    )
    return {
        "fact_id": row.fact_id,
        "item_id": f"business_fact:{row.fact_id}",
        "kind": _knowledge_kind_for_fact_type(row.fact_type),
        "title": title,
        "fact_type": row.fact_type,
        "entity_ref": row.entity_ref,
        "status": row.status,
        "authority_state": _authority_state_for_fact(row),
        "source_refs": list(row.source_refs or []),
        "updated_at": _iso(row.updated_at),
    }


def _stats_tool_event(row: HermesRunEvent) -> dict[str, Any]:
    payload = dict(row.payload or {})
    return {
        "run_id": row.run_id,
        "event_id": row.event_id,
        "tool_name": row.tool_name,
        "tool_state": row.tool_state,
        "query": payload.get("query"),
        "scope": payload.get("scope"),
        "latency_ms": payload.get("latency_ms"),
        "hit_count": payload.get("hit_count"),
        "citation_count": payload.get("citation_count"),
        "source_ref_count": payload.get("source_ref_count"),
        "evidence_backed": payload.get("evidence_backed"),
        "retrieval_channels": list(payload.get("retrieval_channels") or []),
        "action_proposal_id": row.action_proposal_id,
        "created_at": _iso(row.created_at),
    }


def _collection_record(collection: KnowledgeCollection) -> KnowledgeCollectionRecord:
    return KnowledgeCollectionRecord(
        collection_id=collection.collection_id,
        owner_type=collection.scope.owner_type,
        owner_id=collection.scope.owner_id,
        workspace_id=collection.scope.workspace_id,
        title=collection.title,
        description=collection.description,
        tags=list(collection.tags),
        metadata_json=dict(collection.metadata),
    )


def _source_record(source: KnowledgeSource) -> KnowledgeSourceRecord:
    return KnowledgeSourceRecord(
        source_id=source.source_id,
        owner_type=source.scope.owner_type,
        owner_id=source.scope.owner_id,
        workspace_id=source.scope.workspace_id,
        source_kind=source.source_kind,
        external_ref=source.external_ref,
        checksum=source.checksum,
        acl_snapshot=dict(source.acl_snapshot),
        freshness=dict(source.freshness),
        ingestion_status=source.ingestion_status,
        raw_content=source.raw_content,
        metadata_json=dict(source.metadata),
    )


def _item_record(item: KnowledgeItem) -> KnowledgeItemRecord:
    return KnowledgeItemRecord(
        item_id=item.item_id,
        owner_type=item.scope.owner_type,
        owner_id=item.scope.owner_id,
        workspace_id=item.scope.workspace_id,
        kind=item.kind,
        title=item.title,
        body_text=item.body_text,
        source_refs=list(item.source_refs),
        collection_ids=list(item.collection_ids),
        tags=list(item.tags),
        authority_state=item.authority_state,
        visibility=item.visibility,
        created_by=item.created_by,
        created_by_ref=item.created_by_ref,
        metadata_json=dict(item.metadata),
    )


def _chunk_record(chunk: KnowledgeChunk) -> KnowledgeChunkRecord:
    return KnowledgeChunkRecord(
        chunk_id=chunk.chunk_id,
        item_id=chunk.item_id,
        source_id=chunk.source_id,
        owner_type=chunk.scope.owner_type,
        owner_id=chunk.scope.owner_id,
        workspace_id=chunk.scope.workspace_id,
        text=chunk.text,
        contextual_prefix=chunk.contextual_prefix,
        metadata_json=dict(chunk.metadata),
        citation=dict(chunk.citation),
        embedding_model=chunk.embedding_model,
        embedding_state=chunk.embedding_state,
        embedding_degraded_reason=chunk.embedding_degraded_reason,
    )


def _candidate_record(candidate: KnowledgeCandidate) -> KnowledgeCandidateRecord:
    return KnowledgeCandidateRecord(
        candidate_id=candidate.candidate_id,
        owner_type=candidate.scope.owner_type,
        owner_id=candidate.scope.owner_id,
        workspace_id=candidate.scope.workspace_id,
        source_id=candidate.source_id,
        proposed_kind=candidate.proposed_kind,
        proposed_payload=dict(candidate.proposed_payload),
        evidence_refs=list(candidate.evidence_refs),
        confidence=candidate.confidence,
        status=candidate.status,
        agent_control_action_id=candidate.agent_control_action_id,
        metadata_json=dict(candidate.metadata),
    )


def _collection_from_record(row: KnowledgeCollectionRecord) -> KnowledgeCollection:
    return KnowledgeCollection(
        collection_id=row.collection_id,
        scope=_scope(row),
        title=row.title,
        description=row.description,
        tags=list(row.tags or []),
        metadata=dict(row.metadata_json or {}),
    )


def _source_from_record(row: KnowledgeSourceRecord) -> KnowledgeSource:
    return KnowledgeSource(
        source_id=row.source_id,
        scope=_scope(row),
        source_kind=row.source_kind,  # type: ignore[arg-type]
        external_ref=row.external_ref,
        checksum=row.checksum,
        acl_snapshot=dict(row.acl_snapshot or {}),
        freshness=dict(row.freshness or {}),
        ingestion_status=row.ingestion_status,
        raw_content=row.raw_content,
        metadata=dict(row.metadata_json or {}),
    )


def _item_from_record(row: KnowledgeItemRecord) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=row.item_id,
        scope=_scope(row),
        kind=row.kind,  # type: ignore[arg-type]
        title=row.title,
        body_text=row.body_text,
        source_refs=list(row.source_refs or []),
        collection_ids=list(row.collection_ids or []),
        tags=list(row.tags or []),
        authority_state=row.authority_state,  # type: ignore[arg-type]
        visibility=row.visibility,  # type: ignore[arg-type]
        created_by=row.created_by,  # type: ignore[arg-type]
        created_by_ref=row.created_by_ref,
        metadata=dict(row.metadata_json or {}),
    )


def _chunk_from_record(row: KnowledgeChunkRecord) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=row.chunk_id,
        item_id=row.item_id,
        source_id=row.source_id,
        scope=_scope(row),
        text=row.text,
        contextual_prefix=row.contextual_prefix,
        metadata=dict(row.metadata_json or {}),
        citation=dict(row.citation or {}),
        embedding_model=row.embedding_model,
        embedding_state=row.embedding_state,
        embedding_degraded_reason=row.embedding_degraded_reason,
    )


def _candidate_from_record(row: KnowledgeCandidateRecord) -> KnowledgeCandidate:
    return KnowledgeCandidate(
        candidate_id=row.candidate_id,
        scope=_scope(row),
        source_id=row.source_id,
        proposed_kind=row.proposed_kind,
        proposed_payload=dict(row.proposed_payload or {}),
        evidence_refs=list(row.evidence_refs or []),
        confidence=row.confidence,
        status=row.status,  # type: ignore[arg-type]
        agent_control_action_id=row.agent_control_action_id,
        metadata=dict(row.metadata_json or {}),
    )


def _scope(row: Any) -> KnowledgeScope:
    return KnowledgeScope(
        owner_type=row.owner_type,
        owner_id=row.owner_id,
        workspace_id=row.workspace_id,
    )


def _message_search_text(message: Message) -> str:
    parts = [
        message.content,
        message.transcription,
        message.media_description,
    ]
    return "\n".join(str(part).strip() for part in parts if str(part or "").strip())


def _chat_title(
    *,
    message: Message,
    conversation: Conversation,
    customer: Customer,
) -> str:
    display_name = (customer.display_name or "").strip()
    if not display_name:
        display_name = f"Chat {conversation.telegram_chat_id or conversation.id}"
    sender = (message.sender_type or "message").replace("_", " ")
    return f"{display_name} / {sender}"


def _chat_tags(message: Message) -> list[str]:
    tags = ["chat", str(message.sender_type or "").strip()]
    if message.media_type:
        tags.append(f"media:{message.media_type}")
    if message.transcription:
        tags.append("transcription")
    if message.media_description:
        tags.append("media_description")
    return _unique(tags)


def _chat_source_refs(message: Message, conversation: Conversation) -> list[str]:
    refs = [f"conversation:{conversation.id}", f"message:{message.id}"]
    if conversation.telegram_chat_id is not None:
        refs.append(f"telegram_chat:{conversation.telegram_chat_id}")
    if message.telegram_message_id is not None:
        refs.append(f"telegram_message:{conversation.telegram_chat_id}:{message.telegram_message_id}")
    if message.external_message_id:
        refs.append(f"external_message:{message.external_message_id}")
    return _unique(refs)


def _chat_citation(
    *,
    message: Message,
    conversation: Conversation,
    customer: Customer,
    source_refs: list[str],
) -> dict[str, Any]:
    return {
        "type": "chat_message",
        "conversation_id": conversation.id,
        "message_id": message.id,
        "telegram_chat_id": conversation.telegram_chat_id,
        "telegram_message_id": message.telegram_message_id,
        "customer_id": customer.id,
        "display_name": customer.display_name,
        "sender_type": message.sender_type,
        "created_at": _iso(message.created_at),
        "source_refs": list(source_refs),
    }


def _chat_score(terms: list[str], *values: str | None) -> float:
    filtered_terms = [term for term in terms if len(term) > 1]
    haystack = " ".join(str(value or "") for value in values).lower()
    return float(sum(1 for term in filtered_terms if term in haystack))


def _retrieval_result_to_knowledge_search(
    *,
    workspace_id: int,
    collection_id: str,
    candidates: list[Any],
    trace: dict[str, Any],
    limit: int,
) -> KnowledgeSearchResult:
    scope = KnowledgeScope(
        owner_type="workspace",
        owner_id=f"workspace:{workspace_id}",
        workspace_id=workspace_id,
    )
    hits = [
        KnowledgeSearchHit(
            item=_retrieval_candidate_item(
                candidate=candidate,
                scope=scope,
                collection_id=collection_id,
            ),
            score=_retrieval_candidate_score(candidate),
            citations=[_retrieval_candidate_citation(candidate, trace=trace)],
        )
        for candidate in candidates[:limit]
    ]
    return KnowledgeSearchResult(hits=hits)


def _typed_catalog_result_to_knowledge_search(
    *,
    workspace_id: int,
    collection_id: str,
    result: CommerceCatalogSearchResult,
    limit: int,
) -> KnowledgeSearchResult:
    scope = KnowledgeScope(
        owner_type="workspace",
        owner_id=f"workspace:{workspace_id}",
        workspace_id=workspace_id,
    )
    hits: list[KnowledgeSearchHit] = []
    rank = 0
    for product in result.products:
        rank += 1
        fact_id = product.source_fact_ids[0] if product.source_fact_ids else product.product_ref
        hits.append(
            KnowledgeSearchHit(
                item=KnowledgeItem(
                    item_id=f"catalog_product:{product.product_ref}",
                    scope=scope,
                    kind="catalog",
                    title=product.name,
                    body_text=product.description,
                    source_refs=_unique([f"fact:{fact_id}", *product.source_refs]),
                    collection_ids=[collection_id],
                    tags=_unique(["catalog_product", *product.aliases]),
                    authority_state=product.authority_state,  # type: ignore[arg-type]
                    visibility="workspace",
                    created_by="system",
                    created_by_ref="commerce_catalog_core",
                    metadata={
                        "catalog_core": "typed",
                        "fact_id": fact_id,
                        "fact_type": "catalog_product",
                        "product_ref": product.product_ref,
                        "value": product.model_dump(mode="json"),
                    },
                ),
                score=max(1.0 - (rank * 0.01), 0.0),
                citations=[_typed_catalog_citation("catalog_product", fact_id, product.source_refs, result)],
            )
        )
    product_names = {product.product_ref: product.name for product in result.products}
    for offer in result.offers:
        rank += 1
        fact_id = offer.source_fact_ids[0] if offer.source_fact_ids else offer.offer_ref
        body = " ".join(part for part in [offer.price, offer.currency] if part)
        hits.append(
            KnowledgeSearchHit(
                item=KnowledgeItem(
                    item_id=f"catalog_offer:{offer.offer_ref}",
                    scope=scope,
                    kind="catalog",
                    title=product_names.get(offer.product_ref, offer.product_ref),
                    body_text=body or (offer.stock_state or offer.availability or ""),
                    source_refs=_unique([f"fact:{fact_id}", *offer.source_refs]),
                    collection_ids=[collection_id],
                    tags=["catalog_offer"],
                    authority_state=offer.authority_state,  # type: ignore[arg-type]
                    visibility="workspace",
                    created_by="system",
                    created_by_ref="commerce_catalog_core",
                    metadata={
                        "catalog_core": "typed",
                        "fact_id": fact_id,
                        "fact_type": "catalog_offer",
                        "product_ref": offer.product_ref,
                        "value": offer.model_dump(mode="json"),
                    },
                ),
                score=max(1.0 - (rank * 0.01), 0.0),
                citations=[_typed_catalog_citation("catalog_offer", fact_id, offer.source_refs, result)],
            )
        )
    for media in result.media:
        rank += 1
        fact_id = media.source_fact_ids[0] if media.source_fact_ids else media.media_ref
        body = media.caption or media.visual_summary or media.ocr_text or (media.url or "")
        hits.append(
            KnowledgeSearchHit(
                item=KnowledgeItem(
                    item_id=f"catalog_media:{media.media_ref}",
                    scope=scope,
                    kind="media",
                    title=product_names.get(media.product_ref, media.product_ref),
                    body_text=body,
                    source_refs=_unique([f"fact:{fact_id}", *media.source_refs]),
                    collection_ids=[collection_id],
                    tags=["catalog_media", media.media_kind],
                    authority_state=media.authority_state,  # type: ignore[arg-type]
                    visibility="workspace",
                    created_by="system",
                    created_by_ref="commerce_catalog_core",
                    metadata={
                        "catalog_core": "typed",
                        "fact_id": fact_id,
                        "fact_type": "catalog_media",
                        "product_ref": media.product_ref,
                        "media_ref": media.media_ref,
                        "value": media.model_dump(mode="json"),
                    },
                ),
                score=max(1.0 - (rank * 0.01), 0.0),
                citations=[_typed_catalog_citation("catalog_media", fact_id, media.source_refs, result)],
            )
        )
    return KnowledgeSearchResult(hits=hits[:limit])


def _typed_catalog_citation(
    fact_type: str,
    fact_id: str,
    source_refs: list[str],
    result: CommerceCatalogSearchResult,
) -> dict[str, Any]:
    return {
        "type": "commerce_catalog_core",
        "fact_id": fact_id,
        "fact_type": fact_type,
        "source_refs": list(source_refs),
        "retrieval_channels": ["typed_catalog"],
        "catalog_core": "typed",
        "telemetry": result.telemetry.model_dump(mode="json"),
    }


def _retrieval_candidate_item(
    *,
    candidate: Any,
    scope: KnowledgeScope,
    collection_id: str,
) -> KnowledgeItem:
    value = dict(getattr(candidate, "value", {}) or {})
    fact_id = str(candidate.fact_id)
    fact_type = str(candidate.fact_type)
    source_refs = _unique([f"fact:{fact_id}", *list(getattr(candidate, "source_refs", []) or [])])
    return KnowledgeItem(
        item_id=f"business_fact:{fact_id}",
        scope=scope,
        kind=_knowledge_kind_for_fact_type(fact_type),  # type: ignore[arg-type]
        title=_title_for_fact(fact_id=fact_id, fact_type=fact_type, value=value),
        body_text=str(getattr(candidate, "contextual_text", None) or _candidate_body_text(value)),
        source_refs=source_refs,
        collection_ids=[collection_id],
        tags=_unique([fact_type, *list(value.get("tags") or [])]),
        authority_state=_authority_state_for_fact(candidate),
        visibility="workspace",
        created_by="system",
        created_by_ref="business_brain",
        metadata={
            "fact_id": fact_id,
            "fact_type": fact_type,
            "entity_ref": str(getattr(candidate, "entity_ref", "")),
            "confidence": float(getattr(candidate, "confidence", 0.0) or 0.0),
            "risk_tier": str(getattr(candidate, "risk_tier", "")),
            "retrieval_scores": dict(getattr(candidate, "retrieval_scores", {}) or {}),
        },
    )


def _retrieval_candidate_citation(candidate: Any, *, trace: dict[str, Any]) -> dict[str, Any]:
    source_units = [
        unit.model_dump(mode="json") if hasattr(unit, "model_dump") else dict(unit)
        for unit in list(getattr(candidate, "source_units", []) or [])
    ]
    return {
        "type": "business_brain_fact",
        "fact_id": str(candidate.fact_id),
        "fact_type": str(candidate.fact_type),
        "entity_ref": str(getattr(candidate, "entity_ref", "")),
        "source_refs": list(getattr(candidate, "source_refs", []) or []),
        "source_units": source_units,
        "retrieval_scores": dict(getattr(candidate, "retrieval_scores", {}) or {}),
        "retrieval_channels": list(trace.get("retrieval_channels") or []),
        "expanded_fact_types": list(trace.get("expanded_fact_types") or []),
    }


def _retrieval_candidate_score(candidate: Any) -> float:
    scores = dict(getattr(candidate, "retrieval_scores", {}) or {})
    if scores:
        return float(max(scores.values()))
    return float(getattr(candidate, "confidence", 0.0) or 0.0)


def _knowledge_kind_for_fact_type(fact_type: str) -> str:
    if fact_type in {"catalog_media", "business_source_media_fact", "media_evidence"}:
        return "media"
    if fact_type.startswith("catalog_"):
        return "catalog"
    if "rule" in fact_type or "policy" in fact_type:
        return "policy"
    if fact_type == "knowledge_fact":
        return "faq"
    return "doc"


def _authority_state_for_fact(candidate: Any) -> str:
    fact_type = str(getattr(candidate, "fact_type", ""))
    status = str(getattr(candidate, "status", ""))
    if fact_type == "business_source_media_fact":
        return "source"
    if status in {"active", "confirmed"}:
        return "approved"
    if status == "proposed":
        return "candidate"
    return "source"


def _title_for_fact(*, fact_id: str, fact_type: str, value: dict[str, Any]) -> str:
    for key in ("title", "name", "question", "topic", "media_ref", "alt_text"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return fact_id.removeprefix(f"{fact_type}:").replace("_", " ").replace("-", " ")


def _candidate_body_text(value: dict[str, Any]) -> str:
    for key in ("description", "answer", "alt_text", "summary", "body_text", "text"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _unique(values: list[str]) -> list[str]:
    return [value for value in dict.fromkeys(str(item).strip() for item in values) if value]


def _item_row_matches_request(
    row: KnowledgeItemRecord,
    request: KnowledgeSearchRequest,
) -> bool:
    if request.authority_states and row.authority_state not in request.authority_states:
        return False
    if request.collection_ids and not set(request.collection_ids).intersection(
        list(row.collection_ids or [])
    ):
        return False
    return not (
        request.tags
        and not set(request.tags).intersection(list(row.tags or []))
    )


def _merge_search_hit(
    hits_by_item_id: dict[str, KnowledgeSearchHit],
    *,
    item: KnowledgeItem,
    score: float,
    citation: dict[str, Any],
) -> None:
    existing = hits_by_item_id.get(item.item_id)
    if existing is None:
        hits_by_item_id[item.item_id] = KnowledgeSearchHit(
            item=item,
            score=score,
            citations=[citation],
        )
        return
    existing.score = max(existing.score, score)
    existing.citations.append(citation)


def _apply_embedding(
    row: KnowledgeChunkRecord,
    result: RetrievalIndexEmbeddingResult,
) -> None:
    row.embedding = result.embedding
    row.embedding_model = result.embedding_model
    row.embedding_state = result.embedding_state
    row.embedding_degraded_reason = result.degraded_reason


def _chunk_embedding_text(chunk: KnowledgeChunk) -> str:
    return "\n".join(
        part
        for part in (chunk.contextual_prefix.strip(), chunk.text.strip())
        if part
    )


def _terms(value: str) -> list[str]:
    return [term for term in re.split(r"\W+", value.lower()) if term]


def _score(terms: list[str], title: str, body: str, tags: list[str]) -> float:
    haystack = f"{title} {body} {' '.join(tags)}".lower()
    return float(sum(1 for term in terms if term in haystack))


def _knowledge_item_kind(candidate_kind: str) -> str:
    normalized = candidate_kind.strip().lower().replace("_item", "")
    if normalized in _PROMOTABLE_ITEM_KINDS:
        return normalized
    if normalized in {"catalog_product", "catalog_family", "catalog_offer"}:
        return "catalog"
    return "doc"


def _candidate_title(row: KnowledgeCandidateRecord) -> str:
    payload = dict(row.proposed_payload or {})
    for key in ("title", "name", "label", "topic"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return row.proposed_kind.replace("_", " ").title()


def _candidate_body(row: KnowledgeCandidateRecord) -> str:
    payload = dict(row.proposed_payload or {})
    for key in ("body_text", "text", "summary", "description", "rule"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _brain_fact_type(candidate_kind: str) -> str:
    normalized = candidate_kind.strip().lower()
    if normalized in {
        "catalog_product",
        "catalog_variant",
        "catalog_offer",
        "catalog_media",
        "catalog_source",
    }:
        return normalized
    if normalized == "catalog_family":
        return "catalog_product"
    if normalized in {"policy", "rule", "seller_rule"}:
        return "seller_rule_fact"
    if normalized in {"voice", "style"}:
        return "voice_fact"
    return "knowledge_fact"


def _brain_fact_value(row: KnowledgeCandidateRecord, *, knowledge_item_id: str) -> dict[str, Any]:
    payload = dict(row.proposed_payload or {})
    if _is_catalog_fact_type(_brain_fact_type(row.proposed_kind)):
        return payload
    return {
        "kind": row.proposed_kind,
        "payload": payload,
        "knowledge_item_id": knowledge_item_id,
    }


def _brain_fact_id(
    row: KnowledgeCandidateRecord,
    *,
    fact_type: str,
    value: dict[str, Any],
) -> str:
    if fact_type == "catalog_product":
        return _first_payload_text(value, "product_ref", "product_id") or f"knowledge:{row.candidate_id}"
    if fact_type == "catalog_variant":
        return _first_payload_text(value, "variant_ref", "variant_id") or f"knowledge:{row.candidate_id}"
    if fact_type == "catalog_offer":
        return _first_payload_text(value, "offer_ref", "offer_id") or f"knowledge:{row.candidate_id}"
    if fact_type == "catalog_media":
        return _first_payload_text(value, "media_ref", "asset_ref", "media_id") or f"knowledge:{row.candidate_id}"
    if fact_type == "catalog_source":
        return _first_payload_text(value, "source_ref", "source_id") or f"knowledge:{row.candidate_id}"
    return f"knowledge:{row.candidate_id}"


def _brain_entity_ref(*, fact_id: str, value: dict[str, Any]) -> str:
    return _first_payload_text(value, "product_ref", "product_id", "entity_ref") or fact_id


def _is_catalog_fact_type(fact_type: str) -> bool:
    return fact_type in {
        "catalog_product",
        "catalog_variant",
        "catalog_offer",
        "catalog_media",
        "catalog_source",
    }


def _first_payload_text(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        raw = value.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return None
