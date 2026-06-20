from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import (
    ContextualRetrievalRequest,
    MemoryFactWriteInput,
    SourceUnitRebuildRequest,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository


def _memory_service(db_session: AsyncSession) -> BusinessBrainMemoryService:
    return BusinessBrainMemoryService(
        repository=CommercialSpineRepository(db_session),
    )


def _fact_input(
    *,
    workspace: Workspace,
    fact_id: str,
    value: dict[str, Any],
    source_refs: list[str],
) -> MemoryFactWriteInput:
    return MemoryFactWriteInput(
        workspace_id=workspace.id,
        fact_id=fact_id,
        fact_type="knowledge_fact",
        entity_ref=fact_id,
        value=value,
        source_refs=source_refs,
        source="manual",
        status="active",
        approval_state="confirmed",
        confidence=0.92,
        risk_tier="low",
        correlation_id=f"corr:{fact_id}",
        idempotency_key=f"idem:{fact_id}",
    )


@pytest.mark.asyncio
async def test_embedding_provider_failure_does_not_block_business_brain_visibility(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    class FailingEmbeddingService:
        async def embed_texts_batch(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("quota exhausted")

    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService",
        FailingEmbeddingService,
    )

    service = _memory_service(db_session)
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="knowledge:delivery",
            value={
                "topic": "delivery",
                "answer": "Toshkent bo'ylab bugun yetkazamiz",
            },
            source_refs=["source:delivery"],
        )
    )

    units = await service.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["knowledge_fact"],
            embed_source_units=True,
        )
    )
    bundle = await service.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact"],
            query_text="Toshkent",
            include_source_units=True,
        )
    )

    assert units.source_units[0].embedding_state == "degraded"
    assert units.source_units[0].degraded_reason == "embedding_unavailable"
    assert bundle.candidates[0].fact_id == "knowledge:delivery"
    assert bundle.degraded_reasons == ["embedding_unavailable"]


@pytest.mark.asyncio
async def test_wrong_dimension_embeddings_are_discarded_without_losing_fact(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    class WrongDimensionEmbeddingService:
        async def embed_texts_batch(self, texts: list[str]) -> list[list[float]]:
            return [[0.25] * 16]

    monkeypatch.setattr(
        "app.brain.embedding_service.EmbeddingService",
        WrongDimensionEmbeddingService,
    )

    service = _memory_service(db_session)
    await service.write_memory_fact(
        _fact_input(
            workspace=workspace,
            fact_id="knowledge:warranty",
            value={
                "topic": "warranty",
                "answer": "Telefonlarga 12 oy kafolat bor",
            },
            source_refs=["source:warranty"],
        )
    )

    units = await service.rebuild_contextual_source_units(
        SourceUnitRebuildRequest(
            workspace_id=workspace.id,
            fact_types=["knowledge_fact"],
            embed_source_units=True,
        )
    )
    bundle = await service.retrieve_contextual(
        ContextualRetrievalRequest(
            workspace_id=workspace.id,
            requested_fact_types=["knowledge_fact"],
            query_text="kafolat",
            include_source_units=True,
        )
    )

    assert units.source_units[0].embedding is None
    assert units.source_units[0].embedding_state == "degraded"
    assert units.source_units[0].degraded_reason == "embedding_dimension_mismatch"
    assert bundle.candidates[0].fact_id == "knowledge:warranty"
