from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.db.session import async_session
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import AgentDocumentSectionInput
from app.modules.agent_documents.renderer import render_business_md
from app.modules.agent_documents.service import (
    AgentDocumentService,
)
from app.modules.business_brain.contracts import BrainObjectDomain
from app.modules.business_brain.contracts import BusinessBrainFactUpdateInput
from app.modules.business_brain.memory import BusinessBrainMemoryService
from app.modules.business_brain.memory_contracts import MemoryFactWriteInput
from app.modules.business_brain.object_projection import BrainObjectProjectionService
from app.modules.business_brain.read_model import BusinessBrainReadService
from app.modules.business_brain.source_control import (
    SourceControlAction,
    SourceControlRequest,
    SourceControlService,
)
from app.modules.business_brain.source_projection import SourceIntakeProjectionService
from app.modules.business_brain.write_service import BusinessBrainWriteService
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.commercial_spine.contracts import LLMGatewayRequest
from app.modules.commercial_spine.llm_gateway import LLMGateway
from app.modules.onboarding_learning.source_runtime import (
    OnboardingSourceLearningRuntimeService,
)
from app.modules.onboarding_learning.source_progress import (
    build_onboarding_source_learning_projection,
)
from app.modules.onboarding_learning.review_actions import (
    OnboardingLearnedReviewActionRequest,
    OnboardingLearnedReviewActionService,
    ReviewAction,
)
from app.modules.retrieval_core.contracts import (
    RetrievalAgentGroundingRequest,
    RetrievalContextRequest,
)
from app.modules.retrieval_core.service import RetrievalCoreService

router = APIRouter(prefix="/business-brain", tags=["business-brain"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class ManualFactUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)
    update_id: str = Field(min_length=1)
    fact_type: str = Field(min_length=1)
    entity_ref: str = Field(min_length=1)
    value: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    risk_tier: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    correlation_id: str | None = Field(default=None, min_length=1)
    supersedes_fact_id: str | None = Field(default=None, min_length=1)


class MemoryWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)
    fact_type: str = Field(min_length=1)
    entity_ref: str = Field(min_length=1)
    value: dict[str, Any]
    source_refs: list[str] = Field(min_length=1)
    source: str = "manual"
    status: str = "active"
    approval_state: str = "confirmed"
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    risk_tier: str = "low"
    correlation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    supersedes_fact_id: str | None = Field(default=None, min_length=1)


class BusinessBrainFactReviewActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    target_ref: str = Field(min_length=1)
    value_patch: dict[str, Any] = Field(default_factory=dict)
    merge_into_ref: str | None = Field(default=None, min_length=1)


class BusinessBrainSourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    purpose: Literal["brain_data", "agent_data"] = "brain_data"
    label: str | None = Field(default=None, min_length=1)
    text: str | None = Field(default=None, min_length=1)
    url: str | None = Field(default=None, min_length=1)
    handle: str | None = Field(default=None, min_length=1)
    file_name: str | None = Field(default=None, min_length=1)
    content_type: str | None = Field(default=None, min_length=1)
    content_base64: str | None = Field(default=None, min_length=1)
    byte_size: int | None = Field(default=None, ge=1)
    transcript: str | None = Field(default=None, min_length=1)
    date_from: str | None = Field(default=None, min_length=1)
    date_to: str | None = Field(default=None, min_length=1)


class BusinessBrainAudioTranscriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_base64: str = Field(min_length=1)
    content_type: str | None = Field(default=None, min_length=1)
    file_name: str | None = Field(default=None, min_length=1)


class VoiceTranscriptPreviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript: str = ""


class BusinessBrainAudioTranscriptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["business_brain_audio_transcript.v1"] = (
        "business_brain_audio_transcript.v1"
    )
    status: Literal["ready", "degraded"]
    transcript: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model_used: str | None = None
    trace_id: str | None = None
    error_label: str | None = None


class BusinessBrainSourceLearnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=10, ge=1, le=25)
    max_attempts: int = Field(default=3, ge=1, le=5)
    embed_source_units: bool | None = None
    contextualize_source_units: bool | None = None
    background: bool = False


class BusinessBrainSourceRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str | None = Field(default=None, min_length=1)
    limit: int = Field(default=10, ge=1, le=25)
    max_attempts: int = Field(default=3, ge=1, le=5)
    embed_source_units: bool | None = None
    contextualize_source_units: bool | None = None
    background: bool = False


class BusinessBrainSourceControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str = Field(min_length=1)
    action: SourceControlAction
    idempotency_key: str | None = Field(default=None, min_length=1)


class MemoryRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_fact_types: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    candidate_fact_ids: list[str] = Field(default_factory=list)
    requested_slots: list[str] = Field(default_factory=list)
    query_text: str | None = Field(default=None, min_length=1)
    query_modalities: list[str] = Field(default_factory=list)
    minimum_lexical_score: float = Field(default=0.0, ge=0.0, le=1.0)
    enable_semantic: bool = False
    enable_query_rewrite: bool = False
    enable_agentic_search: bool = False
    enable_rerank: bool = False
    include_proposed: bool = False
    include_source_units: bool = False
    limit: int = Field(default=50, ge=1, le=250)


class AgentGroundingAPIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_kind: str = Field(min_length=1)
    requested_fact_types: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    requested_slots: list[str] = Field(default_factory=list)
    query_text: str | None = Field(default=None, min_length=1)
    query_modalities: list[str] = Field(default_factory=list)
    minimum_lexical_score: float = Field(default=0.0, ge=0.0, le=1.0)
    enable_semantic: bool = False
    enable_contextual_rank: bool = True
    enable_query_rewrite: bool = False
    enable_agentic_search: bool = False
    enable_rerank: bool = False


@router.get("/objects")
async def get_business_brain_objects(
    workspace: WorkspaceDep,
    session: SessionDep,
    domain: Annotated[BrainObjectDomain | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> dict:
    projection = await BrainObjectProjectionService(
        repository=CommercialSpineRepository(session),
    ).projection(workspace_id=workspace.id, domain=domain, limit=limit)
    return projection.model_dump(mode="json")


@router.get("/source-intake")
async def get_business_brain_source_intake(
    workspace: WorkspaceDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=250)] = 250,
) -> dict:
    projection = await SourceIntakeProjectionService(
        repository=CommercialSpineRepository(session),
    ).projection(workspace_id=workspace.id, limit=limit)
    return projection.model_dump(mode="json")


@router.post("/facts/manual")
async def apply_manual_fact_update(
    payload: ManualFactUpdateRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    repository = CommercialSpineRepository(session)
    service = BusinessBrainWriteService(repository=repository)
    result = await service.apply(
        BusinessBrainFactUpdateInput(
            update_id=payload.update_id,
            fact_id=payload.fact_id,
            workspace_id=workspace.id,
            fact_type=payload.fact_type,
            entity_ref=payload.entity_ref,
            value=dict(payload.value),
            confidence=payload.confidence,
            status="active",
            risk_tier=payload.risk_tier,  # type: ignore[arg-type]
            source="manual",
            approval_state="confirmed",
            source_refs=list(payload.source_refs),
            idempotency_key=payload.idempotency_key,
            supersedes_fact_id=payload.supersedes_fact_id,
            actor_type="owner",
            actor_ref=f"workspace:{workspace.id}",
            correlation_id=payload.correlation_id,
        )
    )
    projection = await service.rebuild_projection(
        workspace_id=workspace.id,
        projection_ref=f"business_brain:{payload.entity_ref}",
        projection_type="business_brain",
        entity_ref=payload.entity_ref,
    )
    await session.commit()
    return {
        **result.model_dump(mode="json"),
        "projection": projection.model_dump(mode="json"),
    }


@router.get("/facts")
async def list_business_brain_facts(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    service = BusinessBrainReadService(
        repository=CommercialSpineRepository(session),
    )
    facts = await service.list_facts(workspace_id=workspace.id)
    return {"items": [fact.model_dump(mode="json") for fact in facts]}


@router.post("/facts/review-actions")
async def apply_business_brain_fact_review_action(
    payload: BusinessBrainFactReviewActionRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    if payload.action == "merge":
        repository = CommercialSpineRepository(session)
        target = await repository.get_fact(
            workspace_id=workspace.id,
            fact_id=payload.target_ref,
        )
        merge_target = (
            await repository.get_fact(
                workspace_id=workspace.id,
                fact_id=payload.merge_into_ref,
            )
            if payload.merge_into_ref
            else None
        )
        if target is None or merge_target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="merge target not found",
            )
        if target.fact_type.startswith("catalog_") or merge_target.fact_type.startswith(
            "catalog_"
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Use catalog merge actions for product merges",
            )
        if target.fact_type != merge_target.fact_type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Brain fact merge requires the same fact type",
            )
    try:
        target_digest = hashlib.sha256(payload.target_ref.encode("utf-8")).hexdigest()[:16]
        result = await OnboardingLearnedReviewActionService(
            repository=CommercialSpineRepository(session),
        ).apply(
            OnboardingLearnedReviewActionRequest(
                workspace_id=workspace.id,
                action=payload.action,
                target_type="fact",
                target_ref=payload.target_ref,
                value_patch=dict(payload.value_patch),
                merge_into_ref=payload.merge_into_ref,
                correlation_id=f"bb-review:{workspace.id}:{payload.action}:{target_digest}",
                actor_ref=f"workspace:{workspace.id}",
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return result.model_dump(mode="json")


@router.get("/sources")
async def list_business_brain_sources(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    repository = CommercialSpineRepository(session)
    source_facts = await repository.list_facts(
        workspace_id=workspace.id,
        fact_type="business_source_fact",
        limit=250,
    )
    projections = await repository.list_projections(
        workspace_id=workspace.id,
        projection_type="business_source_learning",
        limit=250,
    )
    return build_onboarding_source_learning_projection(
        source_facts=source_facts,
        source_learning_projections=projections,
    )


@router.post("/sources/audio-transcript")
async def transcribe_business_brain_audio_source(
    payload: BusinessBrainAudioTranscriptRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    content_type = (payload.content_type or "audio/webm").strip() or "audio/webm"
    if not content_type.startswith("audio/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio content type is required",
        )
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio file is not valid base64",
        ) from None
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio file is empty",
        )
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio file is too large",
        )

    source_ref = f"audio-transcript:{workspace.id}:{hashlib.sha256(raw).hexdigest()[:16]}"
    gateway = LLMGateway(repository=CommercialSpineRepository(session))
    result = await gateway.generate(
        LLMGatewayRequest(
            route_key="structured_fast",
            workflow_name="onboarding_audio_transcription",
            prompt_id="media.voice_transcription",
            prompt_version="1.0.0",
            input_payload={
                "content_type": content_type,
                "file_name": payload.file_name,
                "ui_goal": "Transcribe audio into owner-editable source text before processing.",
            },
            content_parts=[
                {
                    "kind": "inline_data",
                    "mime_type": content_type,
                    "data_base64": payload.content_base64,
                    "file_name": payload.file_name,
                }
            ],
            output_schema_name="VoiceTranscriptOutput",
            workspace_id=workspace.id,
            correlation_id=source_ref,
            source_refs=[source_ref],
            timeout_ms=45_000,
        ),
        output_model=VoiceTranscriptPreviewOutput,
    )
    await session.commit()

    if result.status != "ok" or not result.parsed_output:
        return BusinessBrainAudioTranscriptResponse(
            status="degraded",
            error_label="Audio o‘qishda muammo bo‘ldi. Qayta urinib ko‘ring yoki qisqa izoh yozing.",
            model_used=result.model_used,
            trace_id=result.trace_id,
        ).model_dump(mode="json")

    parsed = VoiceTranscriptPreviewOutput.model_validate(result.parsed_output)
    transcript = parsed.transcript.strip()
    return BusinessBrainAudioTranscriptResponse(
        status="ready" if transcript else "degraded",
        transcript=transcript,
        confidence=_transcript_confidence(transcript),
        model_used=result.model_used,
        trace_id=result.trace_id,
        error_label=None if transcript else "Audio tushunarli emas. Qo‘lda qisqa izoh qo‘shing.",
    ).model_dump(mode="json")


@router.post("/sources")
async def create_business_brain_source(
    payload: BusinessBrainSourceCreateRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    source_kind = payload.kind.strip()
    source_input = _source_input(payload)
    if not source_input:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Source content is required",
        )
    label = payload.label or _source_label(
        source_kind=source_kind,
        source_input=source_input,
    )
    source_ref = _source_ref(
        workspace_id=workspace.id,
        source_kind=source_kind,
        label=label,
        source_input=source_input,
    )
    repository = CommercialSpineRepository(session)
    result = await BusinessBrainMemoryService(
        repository=repository,
    ).write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id=f"brain:{workspace.id}:source:{source_ref.split(':')[-1]}",
            fact_type="business_source_fact",
            entity_ref=f"workspace:source:{source_ref}",
            value={
                "kind": source_kind,
                "label": label,
                "input": source_input,
                "processing": {
                    "state": "queued",
                    "reason": "business_brain_source_waiting_for_learning",
                },
            },
            source_refs=[source_ref],
            source="import",
            status="active",
            approval_state="confirmed",
            confidence=1.0,
            risk_tier="low",
            correlation_id=f"business-brain-source:{workspace.id}",
            idempotency_key=f"business-brain-source:{workspace.id}:{source_ref}",
            actor_ref=f"workspace:{workspace.id}",
        )
    )
    await OnboardingSourceLearningRuntimeService(
        repository=repository,
    ).queue_workspace_sources(
        workspace_id=workspace.id,
        limit=1,
        max_attempts=3,
        source_refs={source_ref},
    )
    await session.commit()
    return {
        "source_ref": source_ref,
        "fact": result.fact.model_dump(mode="json"),
    }


@router.post("/sources/learn")
async def learn_business_brain_sources(
    payload: BusinessBrainSourceLearnRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    if payload.background:
        result = await OnboardingSourceLearningRuntimeService(
            repository=CommercialSpineRepository(session),
            embed_source_units=payload.embed_source_units,
            contextualize_source_units=payload.contextualize_source_units,
        ).queue_workspace_sources(
            workspace_id=workspace.id,
            limit=payload.limit,
            max_attempts=payload.max_attempts,
        )
        await session.commit()
        return {
            **result.model_dump(mode="json"),
            "background": True,
            "queued_count": sum(1 for item in result.items if item.status == "queued"),
            "worker": "source_learning",
        }

    result = await OnboardingSourceLearningRuntimeService(
        repository=CommercialSpineRepository(session),
        embed_source_units=payload.embed_source_units,
        contextualize_source_units=payload.contextualize_source_units,
        session_factory=async_session,
        max_parallelism=4,
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id=f"business-brain-source-learn:{workspace.id}",
        limit=payload.limit,
        max_attempts=payload.max_attempts,
    )
    await session.commit()
    return result.model_dump(mode="json")


@router.post("/sources/retry")
async def retry_business_brain_sources(
    payload: BusinessBrainSourceRetryRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    source_refs = {payload.source_ref} if payload.source_ref else None
    if payload.background:
        result = await OnboardingSourceLearningRuntimeService(
            repository=CommercialSpineRepository(session),
            embed_source_units=payload.embed_source_units,
            contextualize_source_units=payload.contextualize_source_units,
        ).queue_workspace_sources(
            workspace_id=workspace.id,
            limit=payload.limit,
            max_attempts=payload.max_attempts,
            source_refs=source_refs,
            force=True,
        )
        await session.commit()
        return {
            **result.model_dump(mode="json"),
            "background": True,
            "queued_count": sum(1 for item in result.items if item.status == "queued"),
            "worker": "source_learning",
        }

    result = await OnboardingSourceLearningRuntimeService(
        repository=CommercialSpineRepository(session),
        embed_source_units=payload.embed_source_units,
        contextualize_source_units=payload.contextualize_source_units,
        session_factory=async_session,
        max_parallelism=4,
    ).process_workspace_sources(
        workspace_id=workspace.id,
        correlation_id=f"business-brain-source-retry:{workspace.id}",
        limit=payload.limit,
        max_attempts=payload.max_attempts,
        source_refs=source_refs,
        force=True,
    )
    await session.commit()
    return result.model_dump(mode="json")


@router.post("/source-intake/actions")
async def apply_business_brain_source_intake_action(
    payload: BusinessBrainSourceControlRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    idempotency_key = (
        payload.idempotency_key
        or f"business-brain-source-control:{workspace.id}:{payload.action}:{payload.source_ref}"
    )
    try:
        result = await SourceControlService(
            repository=CommercialSpineRepository(session),
        ).apply(
            SourceControlRequest(
                workspace_id=workspace.id,
                source_ref=payload.source_ref,
                action=payload.action,
                actor_ref=f"workspace:{workspace.id}",
                correlation_id=f"business-brain-source-control:{workspace.id}",
                idempotency_key=idempotency_key,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        ) from exc
    await session.commit()
    return result.model_dump(mode="json")


@router.get("/facts/{fact_id}")
async def get_business_brain_fact(
    fact_id: str,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    service = BusinessBrainReadService(
        repository=CommercialSpineRepository(session),
    )
    detail = await service.detail(workspace_id=workspace.id, fact_id=fact_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business Brain fact not found",
        )
    return detail.model_dump(mode="json")


@router.post("/memory/facts")
async def write_business_brain_memory_fact(
    payload: MemoryWriteRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    result = await BusinessBrainMemoryService(
        repository=CommercialSpineRepository(session),
    ).write_memory_fact(
        MemoryFactWriteInput(
            workspace_id=workspace.id,
            fact_id=payload.fact_id,
            fact_type=payload.fact_type,
            entity_ref=payload.entity_ref,
            value=dict(payload.value),
            source_refs=list(payload.source_refs),
            source=payload.source,
            status=payload.status,
            approval_state=payload.approval_state,
            confidence=payload.confidence,
            risk_tier=payload.risk_tier,  # type: ignore[arg-type]
            correlation_id=payload.correlation_id,
            idempotency_key=payload.idempotency_key,
            supersedes_fact_id=payload.supersedes_fact_id,
        )
    )
    await session.commit()
    return result.model_dump(mode="json")


@router.post("/memory/retrieve")
async def retrieve_business_brain_memory(
    payload: MemoryRetrievalRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    result = await RetrievalCoreService(
        repository=CommercialSpineRepository(session),
    ).retrieve_contextual(
        RetrievalContextRequest(
            workspace_id=workspace.id,
            requested_fact_types=list(payload.requested_fact_types),
            entity_refs=list(payload.entity_refs),
            candidate_fact_ids=list(payload.candidate_fact_ids),
            requested_slots=list(payload.requested_slots),
            query_text=payload.query_text,
            query_modalities=list(payload.query_modalities),  # type: ignore[arg-type]
            minimum_lexical_score=payload.minimum_lexical_score,
            enable_semantic=payload.enable_semantic,
            enable_query_rewrite=payload.enable_query_rewrite,
            enable_agentic_search=payload.enable_agentic_search,
            enable_rerank=payload.enable_rerank,
            include_proposed=payload.include_proposed,
            include_source_units=payload.include_source_units,
            limit=payload.limit,
        )
    )
    return result.model_dump(mode="json")


@router.post("/memory/agent-grounding")
async def build_business_brain_agent_grounding(
    payload: AgentGroundingAPIRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    result = await RetrievalCoreService(
        repository=CommercialSpineRepository(session),
    ).build_agent_grounding(
        RetrievalAgentGroundingRequest(
            workspace_id=workspace.id,
            agent_kind=payload.agent_kind,  # type: ignore[arg-type]
            requested_fact_types=list(payload.requested_fact_types),
            entity_refs=list(payload.entity_refs),
            requested_slots=list(payload.requested_slots),
            query_text=payload.query_text,
            query_modalities=list(payload.query_modalities),  # type: ignore[arg-type]
            minimum_lexical_score=payload.minimum_lexical_score,
            enable_semantic=payload.enable_semantic,
            enable_contextual_rank=payload.enable_contextual_rank,
            enable_query_rewrite=payload.enable_query_rewrite,
            enable_agentic_search=payload.enable_agentic_search,
            enable_rerank=payload.enable_rerank,
        )
    )
    return result.model_dump(mode="json")


def _source_input(payload: BusinessBrainSourceCreateRequest) -> dict[str, Any]:
    if payload.kind == "website":
        return _with_source_purpose({"url": payload.url}, payload.purpose) if payload.url else {}
    if payload.kind == "telegram_channel":
        if not payload.handle:
            return {}
        source: dict[str, Any] = {"handle": payload.handle}
        if payload.date_from:
            source["date_from"] = payload.date_from
        if payload.date_to:
            source["date_to"] = payload.date_to
        return _with_source_purpose(source, payload.purpose)
    if payload.kind == "file":
        if not payload.content_base64:
            return {}
        source = {
            "file_name": payload.file_name or payload.label or "source-file",
            "content_type": payload.content_type or "application/octet-stream",
            "content_base64": payload.content_base64,
        }
        if payload.byte_size:
            source["byte_size"] = payload.byte_size
        return _with_source_purpose(source, payload.purpose)
    if payload.kind == "voice_note":
        source: dict[str, Any] = {}
        if payload.transcript:
            source["transcript"] = payload.transcript
        if payload.content_base64:
            source["file_name"] = payload.file_name or payload.label or "voice-note"
            source["content_type"] = payload.content_type or "audio/mpeg"
            source["content_base64"] = payload.content_base64
            if payload.byte_size:
                source["byte_size"] = payload.byte_size
        return _with_source_purpose(source, payload.purpose) if source else {}
    if payload.kind == "text":
        return _with_source_purpose({"text": payload.text}, payload.purpose) if payload.text else {}
    source: dict[str, Any] = {}
    for key in (
        "text",
        "url",
        "handle",
        "file_name",
        "content_type",
        "content_base64",
        "byte_size",
        "transcript",
        "date_from",
        "date_to",
    ):
        value = getattr(payload, key)
        if value is not None:
            source[key] = value
    return _with_source_purpose(source, payload.purpose) if source else {}


def _source_label(*, source_kind: str, source_input: dict[str, Any]) -> str:
    for key in ("file_name", "url", "handle", "text", "transcript"):
        value = str(source_input.get(key) or "").strip()
        if value:
            return value[:80]
    return source_kind


def _with_source_purpose(source_input: dict[str, Any], purpose: str) -> dict[str, Any]:
    return {**source_input, "purpose": purpose}


def _transcript_confidence(transcript: str) -> float:
    length = len(transcript.strip())
    if length <= 0:
        return 0.0
    if length < 20:
        return 0.55
    if length < 80:
        return 0.78
    return 0.86


# ---------------------------------------------------------------------------
# BUSINESS.md document view + section editor
# Phase 3 task #20. Frontend renders these into the `/brain/business-md` tab.
# ---------------------------------------------------------------------------


class _BusinessMdSectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=255)
    body: str = ""
    order_index: int = 0
    generated_by: str = "owner"


@router.get("/business-md")
async def get_business_md(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Return the rendered BUSINESS.md plus its underlying section rows.

    The Markdown is derived (read-only); the sections are the editable surface.
    Owners edit sections via POST /business-brain/business-md/sections; the
    next GET re-renders. Never store the rendered text as truth.
    """

    service = AgentDocumentService(session)
    sections = await service.list_sections(
        workspace_id=workspace.id,
        document_kind="business",
        subject_type="workspace",
        subject_id=None,
    )
    rendered = render_business_md(workspace.name, sections)
    return {
        "schema_version": "business_md_document.v1",
        "workspace_id": workspace.id,
        "rendered": rendered.model_dump(mode="json"),
        "sections": [section.model_dump(mode="json") for section in sections],
    }


@router.post("/business-md/sections")
async def upsert_business_md_section(
    payload: _BusinessMdSectionRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Round-trip a section edit through AgentDocumentService.

    `generated_by` is owner by default so the audit trail distinguishes
    owner-authored edits from system extractions.
    """

    service = AgentDocumentService(session)
    section = await service.upsert_section(
        workspace_id=workspace.id,
        payload=AgentDocumentSectionInput(
            document_kind="business",
            subject_type="workspace",
            subject_id=None,
            section_key=payload.section_key,
            title=payload.title,
            body=payload.body,
            order_index=payload.order_index,
            source_evidence=[],
            generated_by=payload.generated_by,
        ),
    )
    await session.commit()
    return {
        "schema_version": "business_md_section.v1",
        "section": section.model_dump(mode="json"),
    }


def _source_ref(
    *,
    workspace_id: int,
    source_kind: str,
    label: str,
    source_input: dict[str, Any],
) -> str:
    digest_input = json.dumps(
        {
            "workspace_id": workspace_id,
            "kind": source_kind,
            "label": label,
            "input": source_input,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:20]
    return f"brain:source:{digest}"
