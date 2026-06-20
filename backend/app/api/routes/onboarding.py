"""Onboarding routes — durable onboarding progress and learned review.

Covers:
- GET /onboarding/progress    — read ingestion progress from Redis (polling fallback for WS)
- GET /onboarding/runtime     — read durable runtime, source learning, and review state
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.async_tasks import spawn_guarded_task
from app.core.deps import get_current_workspace, get_db_session
from app.core.logging import get_logger
from app.db.session import async_session
from app.models.onboarding_runtime import OnboardingRuntime
from app.models.workspace import Workspace
from app.modules.brain.onboarding_documents import OnboardingDocumentsService
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.learned_review import (
    build_onboarding_learned_review_projection,
)
from app.modules.onboarding_learning.review_actions import (
    OnboardingLearnedReviewActionRequest,
    OnboardingLearnedReviewActionService,
    ReviewAction,
    ReviewTargetType,
)
from app.modules.onboarding_learning.source_progress import (
    build_onboarding_source_learning_projection,
)
from app.modules.workspace_os.provisioner import WorkspaceOSProvisioner
from app.services.onboarding_runtime import (
    _bg_tasks,
    build_onboarding_runtime_projection,
    default_ingestion_progress,
    get_progress_response,
    load_progress,
    reconcile_progress_with_db,
    store_progress,
)

logger = get_logger("api.onboarding")

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class OnboardingRuntimeProjectionResponse(BaseModel):
    schema_version: str = "onboarding_runtime.v1"
    workspace_id: int
    state: str
    phase: str
    percent: int = Field(ge=0, le=100)
    current_stage_id: str
    stages: list[dict[str, Any]]
    is_running: bool
    is_terminal: bool
    is_retryable: bool
    is_dlq: bool
    can_requeue: bool
    lease_expired: bool
    attempt_count: int
    max_attempts: int
    lease_owner: str | None = None
    leased_until: datetime | None = None
    next_attempt_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    last_error: str | None = None
    progress: dict[str, Any]
    source_learning: dict[str, Any]
    learned_review: dict[str, Any]


class LearnedReviewActionAPIRequest(BaseModel):
    action: ReviewAction
    target_type: ReviewTargetType
    target_ref: str = Field(min_length=1)
    value_patch: dict[str, Any] = Field(default_factory=dict)
    merge_into_ref: str | None = Field(default=None, min_length=1)
    correlation_id: str | None = Field(default=None, min_length=1)


@router.get("/progress")
async def get_onboarding_progress(
    workspace: WorkspaceDep,
):
    """Return canonical onboarding progress for lightweight polling."""
    return await get_progress_response(
        workspace,
        minimal_missing=True,
        include_is_running=True,
    )


@router.get("/runtime", response_model=OnboardingRuntimeProjectionResponse)
async def get_onboarding_runtime(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Return durable onboarding runtime projection for frontend/admin UX."""
    return await _load_onboarding_runtime_projection(workspace=workspace, session=session)


@router.get("/runtime/stream")
async def stream_onboarding_runtime(
    request: Request,
    workspace: WorkspaceDep,
) -> StreamingResponse:
    """Stream durable onboarding runtime snapshots for owner-facing progress UI.

    This is intentionally projection-backed and retry-safe: reconnecting clients
    receive the current runtime state again instead of relying on ephemeral
    browser state.
    """

    async def event_stream():
        previous_fingerprint = ""
        idle_heartbeats = 0
        while not await request.is_disconnected():
            payload: dict[str, Any] | None = None
            try:
                async with async_session() as session:
                    current_workspace = await session.get(Workspace, workspace.id)
                    if current_workspace is None:
                        break
                    payload = await _load_onboarding_runtime_projection(
                        workspace=current_workspace,
                        session=session,
                    )
            except Exception:
                logger.exception("onboarding.runtime_stream.load_failed")
                yield _sse_event(
                    "runtime.error",
                    {
                        "title_uz": "Jarayon holati olinmadi",
                        "detail_uz": "OQIM holatni qayta so‘raydi. Saqlangan ish yo‘qolmaydi.",
                    },
                )
                await asyncio.sleep(2)
                continue

            fingerprint = _runtime_stream_fingerprint(payload)
            if fingerprint != previous_fingerprint:
                previous_fingerprint = fingerprint
                idle_heartbeats = 0
                yield _sse_event("runtime", payload)
            else:
                idle_heartbeats += 1
                if idle_heartbeats >= 10:
                    idle_heartbeats = 0
                    yield _sse_event(
                        "runtime.heartbeat",
                        {
                            "workspace_id": workspace.id,
                            "percent": payload.get("percent"),
                            "source_percent": (
                                payload.get("source_learning", {}).get("percent")
                                if isinstance(payload.get("source_learning"), dict)
                                else None
                            ),
                        },
                    )

            summary = dict(payload.get("source_learning", {}).get("summary") or {})
            working = (
                bool(payload.get("is_running"))
                or int(summary.get("learning") or 0) > 0
                or int(summary.get("retrying") or 0) > 0
            )
            await asyncio.sleep(1 if working else 4)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _load_onboarding_runtime_projection(
    *,
    workspace: Workspace,
    session: AsyncSession,
) -> dict[str, Any]:
    runtime = await session.scalar(
        select(OnboardingRuntime).where(OnboardingRuntime.workspace_id == workspace.id)
    )
    progress = await load_progress(workspace.id)
    if progress is None:
        progress = default_ingestion_progress(workspace.id)
    reconciled_progress = await reconcile_progress_with_db(workspace.id, progress, session=session)
    if reconciled_progress != progress:
        await store_progress(workspace.id, reconciled_progress)
        progress = reconciled_progress
    repository = CommercialSpineRepository(session)
    business_facts = await repository.list_facts(
        workspace_id=workspace.id,
        limit=250,
    )
    source_learning = build_onboarding_source_learning_projection(
        source_facts=[
            fact for fact in business_facts
            if fact.fact_type == "business_source_fact"
        ],
        source_learning_projections=await repository.list_projections(
            workspace_id=workspace.id,
            projection_type="business_source_learning",
            limit=250,
        ),
    )
    return build_onboarding_runtime_projection(
        workspace=workspace,
        runtime=runtime,
        progress=progress,
        source_learning=source_learning,
        learned_review=build_onboarding_learned_review_projection(facts=business_facts),
    )


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def _runtime_stream_fingerprint(payload: dict[str, Any]) -> str:
    source_learning = dict(payload.get("source_learning") or {})
    learned_review = dict(payload.get("learned_review") or {})
    compact = {
        "state": payload.get("state"),
        "phase": payload.get("phase"),
        "percent": payload.get("percent"),
        "current_stage_id": payload.get("current_stage_id"),
        "is_running": payload.get("is_running"),
        "is_retryable": payload.get("is_retryable"),
        "is_dlq": payload.get("is_dlq"),
        "attempt_count": payload.get("attempt_count"),
        "source_status": source_learning.get("status"),
        "source_percent": source_learning.get("percent"),
        "source_summary": source_learning.get("summary"),
        "source_events": source_learning.get("events"),
        "source_rows": [
            {
                "source_ref": source.get("source_ref"),
                "status": source.get("status"),
                "stage": source.get("stage"),
                "attempt_count": source.get("attempt_count"),
                "updated_at": source.get("updated_at"),
                "catalog_candidate_count": source.get("catalog_candidate_count"),
                "memory_candidate_count": source.get("memory_candidate_count"),
                "source_unit_count": source.get("source_unit_count"),
                "source_media_count": source.get("source_media_count"),
            }
            for source in list(source_learning.get("sources") or [])
            if isinstance(source, dict)
        ],
        "review_summary": learned_review.get("summary"),
    }
    return json.dumps(compact, sort_keys=True, default=str, ensure_ascii=False)


def _documents_stream_fingerprint(payload: dict[str, Any]) -> str:
    docs = payload.get("documents", {})
    parts: list[Any] = [payload.get("running"), payload.get("current_doc"), payload.get("error")]
    for name in ("business", "agent"):
        block = docs.get(name, {})
        parts.append([(s.get("key"), s.get("status")) for s in block.get("sections", [])])
    parts.append(docs.get("skill", {}))
    return json.dumps(parts, sort_keys=True, ensure_ascii=False)


async def _run_document_generation(workspace_id: int) -> None:
    try:
        async with async_session() as session:
            await OnboardingDocumentsService(session).generate_all(
                workspace_id=workspace_id,
            )
            await session.commit()
    except Exception:
        # The orchestrator already recorded the error to Redis for the projection.
        logger.exception("onboarding.documents.generation_failed")


@router.get("/documents")
async def get_onboarding_documents(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Return the onboarding three-document projection (BUSINESS/AGENT/SKILL)."""
    return await OnboardingDocumentsService(session).build_documents_projection(
        workspace_id=workspace.id
    )


@router.post("/documents/generate", status_code=202)
async def generate_onboarding_documents(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, str]:
    """Kick off background three-document generation for this workspace.

    Before scheduling generation, idempotently ensure the default agents
    exist so AGENT.md has subjects to render. Document provisioning is
    skipped here (documents=False) — the doc-gen orchestrator owns content.
    """
    await WorkspaceOSProvisioner(session).provision(
        workspace=workspace,
        profile=workspace.onboarding_profile or {},
        preferences={},
        documents=False,
    )
    await session.commit()

    spawn_guarded_task(
        _run_document_generation(workspace.id),
        logger=logger,
        name=f"onboarding-docgen:{workspace.id}",
        registry=_bg_tasks,
    )
    return {"status": "started"}


@router.get("/documents/stream")
async def stream_onboarding_documents(
    request: Request,
    workspace: WorkspaceDep,
) -> StreamingResponse:
    """Stream onboarding document-generation snapshots for the workbench UI.

    Projection-backed and retry-safe: reconnecting clients get the current
    document state again instead of relying on ephemeral browser state.
    """

    async def event_stream():
        previous_fingerprint = ""
        idle_heartbeats = 0
        while not await request.is_disconnected():
            payload: dict[str, Any] | None = None
            try:
                async with async_session() as session:
                    current_workspace = await session.get(Workspace, workspace.id)
                    if current_workspace is None:
                        break
                    payload = await OnboardingDocumentsService(
                        session
                    ).build_documents_projection(workspace_id=workspace.id)
            except Exception:
                logger.exception("onboarding.documents_stream.load_failed")
                yield _sse_event(
                    "documents.error",
                    {
                        "title_uz": "Hujjat holati olinmadi",
                        "detail_uz": "OQIM holatni qayta so‘raydi. Saqlangan ish yo‘qolmaydi.",
                    },
                )
                await asyncio.sleep(2)
                continue

            fingerprint = _documents_stream_fingerprint(payload)
            if fingerprint != previous_fingerprint:
                previous_fingerprint = fingerprint
                idle_heartbeats = 0
                yield _sse_event("documents", payload)
            else:
                idle_heartbeats += 1
                if idle_heartbeats >= 10:
                    idle_heartbeats = 0
                    yield _sse_event(
                        "documents.heartbeat",
                        {"workspace_id": workspace.id, "percent": payload.get("percent")},
                    )
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/learned-review/actions")
async def apply_learned_review_action(
    payload: LearnedReviewActionAPIRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Apply an owner decision to learned Business Brain proposals."""
    repository = CommercialSpineRepository(session)
    correlation_id = (
        payload.correlation_id
        or f"onboarding:{workspace.id}:learned_review:{payload.action}:{payload.target_ref}"
    )
    result = await OnboardingLearnedReviewActionService(
        repository=repository,
    ).apply(
        OnboardingLearnedReviewActionRequest(
            workspace_id=workspace.id,
            action=payload.action,
            target_type=payload.target_type,
            target_ref=payload.target_ref,
            value_patch=dict(payload.value_patch),
            merge_into_ref=payload.merge_into_ref,
            correlation_id=correlation_id,
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    business_facts = await repository.list_facts(
        workspace_id=workspace.id,
        limit=250,
    )
    await session.commit()
    return {
        "action": result.model_dump(mode="json"),
        "learned_review": build_onboarding_learned_review_projection(
            facts=business_facts,
        ),
    }
