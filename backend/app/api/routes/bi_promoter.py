from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.workspace import Workspace
from app.modules.bi_promoter.contracts import (
    BICommandInput,
    BICommandResult,
    BIInsightRequest,
    BIInvestigationRequest,
    PromoterCampaignInput,
    PromoterCandidateInput,
    PromoterPolicyInput,
    PromoterProjectionCampaignInput,
)
from app.modules.bi_promoter.service import BIPromoterService
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.workspace_os.custom_agent import (
    CustomAgentPackageInput,
    CustomAgentPackageService,
)

router = APIRouter(prefix="/bi-promoter", tags=["bi-promoter"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class PromoterPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    approved: bool = False
    allowed_stages: list[str] = Field(default_factory=list)
    max_contacts_per_7d: int = Field(default=1, ge=0, le=20)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(default="api:promoter_policy", min_length=1)


class PromoterCampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_ref: str = Field(min_length=1)
    approval_state: Literal["proposed", "approved", "rejected"]
    message_goal: str = Field(min_length=1)
    offer_refs: list[str] = Field(default_factory=list)
    candidates: list[PromoterCandidateInput] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class PromoterProjectionCampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_ref: str = Field(min_length=1)
    approval_state: Literal["proposed", "approved", "rejected"]
    message_goal: str = Field(min_length=1)
    offer_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    max_candidates: int = Field(default=50, ge=1, le=100)


class BIInvestigationApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_ref: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    limit: int = Field(default=100, ge=1, le=250)


class BICommandApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_kind: Literal["create_agent", "create_owner_task", "create_reply_action"]
    command_text: str = Field(min_length=8, max_length=2000)
    agent_name: str | None = Field(default=None, min_length=2, max_length=120)
    permission_mode: Literal["ask_always", "auto_approve", "full_access"] = "ask_always"
    brain_scopes: list[str] = Field(
        default_factory=lambda: ["knowledge", "rules", "voice", "examples"]
    )
    tool_scopes: list[str] = Field(default_factory=lambda: ["telegram.read_messages"])
    trigger_sources: list[str] = Field(default_factory=list)
    task_title: str | None = Field(default=None, min_length=2, max_length=160)
    task_detail: str | None = Field(default=None, min_length=2, max_length=1000)
    task_kind: Literal[
        "business",
        "meeting",
        "delivery",
        "stock",
        "call",
        "payment",
        "follow_up",
    ] | None = None
    due_at: str | None = Field(default=None, min_length=1, max_length=80)
    customer_label: str | None = Field(default=None, min_length=1, max_length=160)
    conversation_id: int | None = Field(default=None, ge=0)
    customer_id: int | None = Field(default=None, ge=0)
    reply_text: str | None = Field(default=None, min_length=1, max_length=2000)
    source_proposal_id: str | None = Field(default=None, min_length=1, max_length=200)
    correlation_id: str = Field(default="ui:bi_command", min_length=1)


def _service(session: AsyncSession) -> BIPromoterService:
    return BIPromoterService(repository=CommercialSpineRepository(session))


def _bi_command_idempotency_key(command: BICommandInput) -> str:
    payload = command.model_dump(
        mode="json",
        exclude={"schema_version", "workspace_id", "correlation_id"},
    )
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"bi-command:{command.workspace_id}:{digest}"


def _bi_command_digest(command: BICommandInput) -> str:
    return _bi_command_idempotency_key(command).rsplit(":", 1)[-1]


async def _propose_owner_task(
    session: AsyncSession,
    command: BICommandInput,
) -> tuple[CommercialActionProposal, bool]:
    repository = CommercialSpineRepository(session)
    digest = _bi_command_digest(command)
    proposal = CommercialActionProposal(
        proposal_id=f"bi-owner-task:{digest}",
        workspace_id=command.workspace_id,
        conversation_id=command.conversation_id or 0,
        customer_id=command.customer_id or 0,
        action_type="create_business_task",
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level="low",
        requires_approval=False,
        executor_runtime="owner_task",
        priority="medium",
        confidence=0.9,
        reason_code="bi_owner_task_proposal",
        source_refs=[f"bi_command:{command.command_kind}"],
        payload={
            "actor_ref": "bi_agent",
            "command_text": command.command_text,
            "customer_name": command.customer_label or "Biznes",
            "owner_task": {
                "task_kind": command.task_kind,
                "title": command.task_title,
                "detail": command.task_detail,
                "due_at": command.due_at,
                "created_by": "bi_agent",
            },
        },
        idempotency_key=_bi_command_idempotency_key(command),
        correlation_id=command.correlation_id,
        trace_id=f"trace:bi-command:{digest}",
    )
    created = await repository.persist_action_proposal(proposal)
    if created:
        return proposal, True
    existing = await repository.get_action_proposal(
        workspace_id=command.workspace_id,
        proposal_id=proposal.proposal_id,
    )
    return existing or proposal, False


async def _propose_reply_action(
    session: AsyncSession,
    command: BICommandInput,
) -> tuple[CommercialActionProposal, bool]:
    repository = CommercialSpineRepository(session)
    digest = _bi_command_digest(command)
    assert command.conversation_id is not None and command.conversation_id > 0
    assert command.customer_id is not None and command.customer_id > 0
    assert command.reply_text is not None
    source_refs = [
        f"bi_command:{command.command_kind}",
        f"conversation:{command.conversation_id}",
    ]
    if command.source_proposal_id:
        source_refs.append(f"owner_task:{command.source_proposal_id}")
    proposal = CommercialActionProposal(
        proposal_id=f"bi-reply-action:{digest}",
        workspace_id=command.workspace_id,
        conversation_id=command.conversation_id,
        customer_id=command.customer_id,
        action_type="send_reply",
        lifecycle_state="waiting_approval",
        execution_mode="draft_for_review",
        risk_level="medium",
        requires_approval=True,
        executor_runtime="telegram_tool_runtime",
        priority="medium",
        confidence=0.9,
        reason_code="bi_reply_action_proposal",
        source_refs=source_refs,
        payload={
            "actor_ref": "bi_agent",
            "command_text": command.command_text,
            "customer_name": command.customer_label or "Mijoz",
            "draft_text": command.reply_text,
            "reply_text": command.reply_text,
            "source_task_proposal_id": command.source_proposal_id,
        },
        idempotency_key=_bi_command_idempotency_key(command),
        correlation_id=command.correlation_id,
        trace_id=f"trace:bi-command:{digest}",
    )
    created = await repository.persist_action_proposal(proposal)
    if created:
        return proposal, True
    existing = await repository.get_action_proposal(
        workspace_id=command.workspace_id,
        proposal_id=proposal.proposal_id,
    )
    return existing or proposal, False


@router.get("/insights/pipeline-summary")
async def get_pipeline_summary(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    insight = await _service(session).answer(
        BIInsightRequest(
            workspace_id=workspace.id,
            question_kind="pipeline_summary",
            source_refs=["api:bi:pipeline_summary"],
            correlation_id="api:bi:pipeline_summary",
        )
    )
    await session.commit()
    return insight.model_dump(mode="json")


@router.get("/analytics/dashboard")
async def get_analytics_dashboard(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    dashboard = await _service(session).dashboard(
        workspace_id=workspace.id,
        source_refs=["api:bi:analytics_dashboard"],
        correlation_id="api:bi:analytics_dashboard",
    )
    await session.commit()
    return dashboard.model_dump(mode="json")


@router.post("/investigations")
async def create_bi_investigation(
    payload: BIInvestigationApiRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    result = await _service(session).investigate(
        BIInvestigationRequest(
            workspace_id=workspace.id,
            **payload.model_dump(mode="python"),
        )
    )
    await session.commit()
    return result.model_dump(mode="json")


@router.post("/commands", status_code=201)
async def run_bi_command(
    payload: BICommandApiRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    command = BICommandInput(
        workspace_id=workspace.id,
        **payload.model_dump(mode="python"),
    )
    if command.command_kind == "create_agent":
        assert command.agent_name is not None
        package = CustomAgentPackageInput(
            name=command.agent_name,
            mission=command.command_text,
            permission_mode=command.permission_mode,
            brain_scopes=command.brain_scopes,
            tool_scopes=command.tool_scopes,
            trigger_sources=command.trigger_sources,
            idempotency_key=_bi_command_idempotency_key(command),
        )
        proposal_result = await CustomAgentPackageService(session).propose(
            workspace_id=workspace.id,
            payload=package,
            actor_ref="agent",
            correlation_id=command.correlation_id,
        )
        await session.commit()
        result = BICommandResult(
            workspace_id=workspace.id,
            command_kind=command.command_kind,
            status=(
                "proposal_created"
                if proposal_result.created
                else "proposal_reused"
            ),
            message_uz=(
                "Agent taklifi Amallar bo'limiga qo'shildi."
                if proposal_result.created
                else "Bu agent taklifi allaqachon Amallarda bor."
            ),
            proposal=proposal_result.proposal,
            source_refs=[
                f"bi_command:{command.command_kind}",
                *proposal_result.proposal.source_refs,
            ],
        )
        return result.model_dump(mode="json")

    if command.command_kind == "create_owner_task":
        proposal, created = await _propose_owner_task(session, command)
        await session.commit()
        result = BICommandResult(
            workspace_id=workspace.id,
            command_kind=command.command_kind,
            status="proposal_created" if created else "proposal_reused",
            message_uz=(
                "Vazifa taklifi Amallarga qo'shildi."
                if created
                else "Bu vazifa taklifi allaqachon Amallarda bor."
            ),
            proposal=proposal,
            source_refs=[f"bi_command:{command.command_kind}", *proposal.source_refs],
        )
        return result.model_dump(mode="json")

    if command.command_kind == "create_reply_action":
        proposal, created = await _propose_reply_action(session, command)
        await session.commit()
        result = BICommandResult(
            workspace_id=workspace.id,
            command_kind=command.command_kind,
            status="proposal_created" if created else "proposal_reused",
            message_uz=(
                "Javob taklifi Amallarga qo'shildi."
                if created
                else "Bu javob taklifi allaqachon Amallarda bor."
            ),
            proposal=proposal,
            source_refs=[f"bi_command:{command.command_kind}", *proposal.source_refs],
        )
        return result.model_dump(mode="json")

    raise AssertionError(f"unsupported BI command: {command.command_kind}")


@router.get("/promoter/policy")
async def get_promoter_policy(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    policy = await _service(session).get_promoter_policy(workspace_id=workspace.id)
    return policy.model_dump(mode="json")


@router.put("/promoter/policy")
async def set_promoter_policy(
    payload: PromoterPolicyRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    policy = await _service(session).set_promoter_policy(
        PromoterPolicyInput(
            workspace_id=workspace.id,
            **payload.model_dump(mode="python"),
        )
    )
    await session.commit()
    return policy.model_dump(mode="json")


@router.post("/promoter/campaigns/plan")
async def plan_promoter_campaign(
    payload: PromoterCampaignRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    plan = await _service(session).plan_campaign(
        PromoterCampaignInput(
            workspace_id=workspace.id,
            **payload.model_dump(mode="python"),
        )
    )
    await session.commit()
    return plan.model_dump(mode="json")


@router.post("/promoter/campaigns/plan-from-projections")
async def plan_promoter_campaign_from_projections(
    payload: PromoterProjectionCampaignRequest,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict:
    plan = await _service(session).plan_campaign_from_projections(
        PromoterProjectionCampaignInput(
            workspace_id=workspace.id,
            **payload.model_dump(mode="python"),
        )
    )
    await session.commit()
    return plan.model_dump(mode="json")
