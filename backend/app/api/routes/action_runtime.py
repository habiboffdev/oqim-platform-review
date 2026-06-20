from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_current_workspace, get_db_session
from app.models.workspace import Workspace
from app.modules.action_runtime.contracts import (
    ActionRuntimeDraftEditInput,
    ActionRuntimePolicyInput,
    ActionRuntimeRejectInput,
    ActionRuntimeRequeueInput,
    IntegrationCapabilityInput,
)
from app.modules.action_runtime.service import ActionRuntimeService
from app.modules.agent_runtime_events.contracts import AgentRunTimeline
from app.modules.agent_runtime_events.service import AgentRuntimeEventService
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.services.delivery import DeliveryService

router = APIRouter(prefix="/action-runtime", tags=["action-runtime"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    confidence_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    low_risk_allowlist: list[str] = Field(default_factory=list)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    escalation_destination: str = "in_app"
    source_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(default="api:action_runtime_policy", min_length=1)


class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_ref: str = Field(min_length=1)
    integration_kind: str = Field(min_length=1)
    enabled: bool = True
    allowed_action_types: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(default="api:integration_capability", min_length=1)


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    correlation_id: str | None = Field(default=None, min_length=1)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_ref: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class RejectRequest(ApprovalRequest):
    reason_code: str = Field(min_length=1)


class DraftEditRequest(ApprovalRequest):
    draft_text: str = Field(min_length=1, max_length=4000)


class RequeueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_payload: dict[str, Any] = Field(default_factory=dict)
    actor_ref: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class TaskSnoozeRequest(ApprovalRequest):
    due_at: str | None = Field(default=None, min_length=1)


def _service(session: AsyncSession) -> ActionRuntimeService:
    settings = get_settings()
    return ActionRuntimeService(
        repository=CommercialSpineRepository(session),
        delivery=DeliveryService(
            sidecar_url=settings.sidecar_url,
            sidecar_api_key=settings.sidecar_api_key,
        ),
    )


def _agent_run_id_from_refs(source_refs: list[str]) -> str | None:
    for source_ref in source_refs:
        if source_ref.startswith("agent_run:"):
            return source_ref.removeprefix("agent_run:").strip() or None
    return None


@router.get("/inbox")
async def get_action_runtime_inbox(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    inbox = await _service(session).inbox(workspace_id=workspace.id)
    return inbox.model_dump(mode="json")


@router.get("/tasks")
async def get_owner_task_projection(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    tasks = await _service(session).owner_tasks(workspace_id=workspace.id)
    return tasks.model_dump(mode="json")


@router.get("/proposals/{proposal_id}/timeline")
async def get_action_proposal_timeline(
    proposal_id: str,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    repository = CommercialSpineRepository(session)
    proposal = await repository.get_action_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal_id,
    )
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="action_proposal_not_found",
        )

    service = AgentRuntimeEventService(repository)
    run_id = _agent_run_id_from_refs(proposal.source_refs)
    timeline = (
        await service.timeline(workspace_id=workspace.id, run_id=run_id)
        if run_id is not None
        else await service.timeline_for_proposal(
            workspace_id=workspace.id,
            proposal_id=proposal.proposal_id,
        )
    )
    if timeline is None:
        timeline = AgentRunTimeline(
            workspace_id=workspace.id,
            run_id=run_id or "",
            run=None,
            events=[],
        )
    return timeline.model_dump(mode="json")


@router.get("/agent-runs/recent")
async def get_recent_agent_runs(
    workspace: WorkspaceDep,
    session: SessionDep,
    limit: int = 5,
) -> dict:
    feed = await AgentRuntimeEventService(
        CommercialSpineRepository(session)
    ).recent_timelines(workspace_id=workspace.id, limit=limit)
    return feed.model_dump(mode="json")


@router.post("/tasks/{proposal_id}/accept")
async def accept_owner_task(
    proposal_id: str,
    payload: ApprovalRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        proposal = await _service(session).approve(
            workspace_id=workspace.id,
            proposal_id=proposal_id,
            actor_ref=payload.actor_ref,
            correlation_id=payload.correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return proposal.model_dump(mode="json")


@router.post("/tasks/{proposal_id}/complete")
async def complete_owner_task(
    proposal_id: str,
    payload: ApprovalRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        execution = await _service(session).execute(
            workspace_id=workspace.id,
            proposal_id=proposal_id,
            correlation_id=payload.correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return execution.model_dump(mode="json")


@router.post("/tasks/{proposal_id}/dismiss")
async def dismiss_owner_task(
    proposal_id: str,
    payload: RejectRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        proposal = await _service(session).reject_input(
            ActionRuntimeRejectInput(
                workspace_id=workspace.id,
                proposal_id=proposal_id,
                actor_ref=payload.actor_ref,
                reason_code=payload.reason_code,
                correlation_id=payload.correlation_id,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return proposal.model_dump(mode="json")


@router.post("/tasks/{proposal_id}/snooze")
async def snooze_owner_task(
    proposal_id: str,
    payload: TaskSnoozeRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        task = await _service(session).snooze_task(
            workspace_id=workspace.id,
            proposal_id=proposal_id,
            actor_ref=payload.actor_ref,
            correlation_id=payload.correlation_id,
            due_at=payload.due_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return task.model_dump(mode="json")


@router.get("/policy")
async def get_action_runtime_policy(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    policy = await _service(session).get_policy(workspace_id=workspace.id)
    return policy.model_dump(mode="json")


@router.put("/policy")
async def set_action_runtime_policy(
    payload: PolicyRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    policy = await _service(session).set_policy(
        ActionRuntimePolicyInput(
            workspace_id=workspace.id,
            **payload.model_dump(mode="python"),
        )
    )
    await session.commit()
    return policy.model_dump(mode="json")


@router.post("/capabilities")
async def register_action_runtime_capability(
    payload: CapabilityRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    capability = await _service(session).register_capability(
        IntegrationCapabilityInput(
            workspace_id=workspace.id,
            **payload.model_dump(mode="python"),
        )
    )
    await session.commit()
    return capability.model_dump(mode="json")


@router.post("/process")
async def process_action_runtime_proposal(
    payload: ProcessRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        decision = await _service(session).process_proposal(
            workspace_id=workspace.id,
            proposal_id=payload.proposal_id,
            correlation_id=payload.correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return decision.model_dump(mode="json")


@router.post("/proposals/{proposal_id}/approve")
async def approve_action_runtime_proposal(
    proposal_id: str,
    payload: ApprovalRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        proposal = await _service(session).approve(
            workspace_id=workspace.id,
            proposal_id=proposal_id,
            actor_ref=payload.actor_ref,
            correlation_id=payload.correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return proposal.model_dump(mode="json")


@router.post("/proposals/{proposal_id}/reject")
async def reject_action_runtime_proposal(
    proposal_id: str,
    payload: RejectRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        proposal = await _service(session).reject_input(
            ActionRuntimeRejectInput(
                workspace_id=workspace.id,
                proposal_id=proposal_id,
                actor_ref=payload.actor_ref,
                reason_code=payload.reason_code,
                correlation_id=payload.correlation_id,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return proposal.model_dump(mode="json")


@router.post("/proposals/{proposal_id}/draft")
async def edit_action_runtime_proposal_draft(
    proposal_id: str,
    payload: DraftEditRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        proposal = await _service(session).edit_draft_input(
            ActionRuntimeDraftEditInput(
                workspace_id=workspace.id,
                proposal_id=proposal_id,
                actor_ref=payload.actor_ref,
                draft_text=payload.draft_text,
                correlation_id=payload.correlation_id,
            )
        )
    except ValueError as exc:
        reason = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if reason == "action_proposal_not_found"
                else status.HTTP_409_CONFLICT
            ),
            detail=reason,
        ) from exc
    await session.commit()
    return proposal.model_dump(mode="json")


@router.post("/proposals/{proposal_id}/execute")
async def execute_action_runtime_proposal(
    proposal_id: str,
    payload: ApprovalRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        execution = await _service(session).execute(
            workspace_id=workspace.id,
            proposal_id=proposal_id,
            correlation_id=payload.correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return execution.model_dump(mode="json")


@router.post("/proposals/{proposal_id}/requeue")
async def requeue_action_runtime_proposal(
    proposal_id: str,
    payload: RequeueRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    try:
        proposal = await _service(session).requeue_failed(
            ActionRuntimeRequeueInput(
                workspace_id=workspace.id,
                proposal_id=proposal_id,
                **payload.model_dump(mode="python"),
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await session.commit()
    return proposal.model_dump(mode="json")
