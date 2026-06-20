"""Intelligence API — Skills catalog, Agents, and AGENT.md sections.

Phase 4 of the 2026-05-16 reset roadmap. Workspace-scoped CRUD that the
Intelligence frontend page consumes. The rendered AGENT.md is always derived
from `render_agent_md(agent, sections, skills)`; this router exposes the
underlying structured records and the rendered view side-by-side.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_workspace, get_db_session
from app.models.agent import Agent
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import (
    AgentDocumentSectionInput,
    AgentSkillInput,
)
from app.modules.agent_documents.renderer import render_agent_md
from app.modules.agent_documents.service import (
    AgentDocumentService,
)
from app.modules.agent_runtime_v2.budget import BudgetExceededError
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.telegram_tools.contracts import TELEGRAM_TOOL_DEFINITIONS, TELEGRAM_TOOL_SCOPES
from app.modules.tool_catalog import is_external_tool_scope
from app.modules.tool_grants.contracts import ToolGrantProposalInput
from app.modules.tool_grants.service import ToolGrantService
from app.modules.triggers.contracts import (
    TriggerInput,
    TriggerProposalInput,
)
from app.modules.triggers.service import (
    TriggerNotFoundError,
    TriggerService,
)
from app.modules.workspace_os.custom_agent import (
    CustomAgentPackageInput,
    CustomAgentPackageService,
)
from app.modules.workspace_os.custom_agent_draft import (
    CustomAgentDraftInput,
    CustomAgentDraftService,
)

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

WorkspaceDep = Annotated[Workspace, Depends(get_current_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Skills catalog
# ---------------------------------------------------------------------------


@router.get("/skills")
async def list_skills(
    workspace: WorkspaceDep,
    session: SessionDep,
    agent_id: int | None = None,
) -> dict[str, Any]:
    service = AgentDocumentService(session)
    skills = await service.list_skills(workspace_id=workspace.id, agent_id=agent_id)
    return {
        "schema_version": "intelligence_skills.v1",
        "items": [skill.model_dump(mode="json") for skill in skills],
    }


@router.post("/skills")
async def upsert_skill(
    payload: AgentSkillInput,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    service = AgentDocumentService(session)
    try:
        skill = await service.upsert_skill(workspace_id=workspace.id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="skill_slug_conflict") from exc
    await session.commit()
    return {"schema_version": "intelligence_skill.v1", "skill": skill.model_dump(mode="json")}


@router.delete("/skills/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    slug: str,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> None:
    service = AgentDocumentService(session)
    deleted = await service.delete_skill(workspace_id=workspace.id, slug=slug)
    if not deleted:
        raise HTTPException(status_code=404, detail="skill_not_found")
    await session.commit()


# ---------------------------------------------------------------------------
# Agents — list + detail with rendered AGENT.md
# ---------------------------------------------------------------------------


@router.get("/agents")
async def list_agents(
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    result = await session.scalars(
        select(Agent).where(Agent.workspace_id == workspace.id).order_by(Agent.id)
    )
    agents = result.all()
    service = AgentDocumentService(session)
    grants_service = ToolGrantService(session)
    trigger_service = TriggerService(session)
    skill_groups: dict[int, int] = {}
    section_groups: dict[int, int] = {}
    grant_groups: dict[int, int] = {}
    trigger_groups: dict[int, int] = {}
    for agent in agents:
        skills = await service.list_skills(workspace_id=workspace.id, agent_id=agent.id)
        skill_groups[agent.id] = len(skills)
        sections = await service.list_sections(
            workspace_id=workspace.id,
            document_kind="agent",
            subject_type="agent",
            subject_id=agent.id,
        )
        section_groups[agent.id] = len(sections)
        grants = await grants_service.list_for_workspace(
            workspace_id=workspace.id, agent_id=agent.id
        )
        grant_groups[agent.id] = len(
            [grant for grant in grants if grant.active and is_external_tool_scope(grant.scope)]
        )
        triggers = await trigger_service.list_for_workspace(
            workspace_id=workspace.id, agent_id=agent.id
        )
        trigger_groups[agent.id] = len([trigger for trigger in triggers if trigger.active])
    return {
        "schema_version": "intelligence_agents.v1",
        "items": [
            {
                "id": agent.id,
                "name": agent.name,
                "agent_type": agent.agent_type,
                "trust_mode": agent.trust_mode,
                "is_active": agent.is_active,
                "package_key": _agent_package_key(agent),
                "permission_mode": _agent_permission_mode(agent),
                "skill_count": skill_groups.get(agent.id, 0),
                "document_section_count": section_groups.get(agent.id, 0),
                "tool_grant_count": grant_groups.get(agent.id, 0),
                "trigger_count": trigger_groups.get(agent.id, 0),
            }
            for agent in agents
        ],
    }


@router.post("/agents/custom/draft")
async def draft_custom_agent(
    payload: CustomAgentDraftInput,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    service = CustomAgentDraftService(session)
    try:
        result = await service.draft(workspace_id=workspace.id, payload=payload)
    except BudgetExceededError as exc:
        # BudgetExceededError subclasses RuntimeError, so it MUST be caught first.
        # A token-cap hit is a distinct condition from the chain being down.
        raise HTTPException(status_code=429, detail="budget_exceeded") from exc
    except RuntimeError as exc:
        # generate_with_fallback raises RuntimeError when the whole CONTROL_CHAIN is
        # exhausted. The draft persists nothing, so a clean 503 lets the wizard show
        # its inline retry instead of leaking a raw provider 500.
        raise HTTPException(status_code=503, detail="draft_unavailable") from exc
    return {
        "schema_version": "custom_agent_draft.v1",
        "agent_kind": result.agent_kind,
        "name": result.name,
        "sections": [section.model_dump(mode="json") for section in result.sections],
        "brain_scopes": result.brain_scopes,
        "tool_scopes": result.tool_scopes,
        "trigger_sources": result.trigger_sources,
        "permission_mode": result.permission_mode,
        "trust_mode": result.trust_mode,
    }


@router.post("/agents/custom", status_code=status.HTTP_201_CREATED)
async def create_custom_agent(
    payload: CustomAgentPackageInput,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    # Direct creation: the wizard's review/confirm step is the owner-approval gate.
    # (The propose()/action-runtime path remains for the BI "create_agent" command but
    # is no longer reached from the wizard endpoint.)
    service = CustomAgentPackageService(session)
    try:
        result = await service.create(workspace_id=workspace.id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        # Mirrors upsert_skill's handling: a concurrent double-submit could race past
        # the in-service idempotency check and hit a unique constraint.
        raise HTTPException(status_code=409, detail="agent_already_exists") from exc
    await session.commit()
    return {
        "schema_version": "custom_agent_package.v1",
        "created": result.created,
        "agent": {
            "id": result.agent.id,
            "name": result.agent.name,
            "agent_type": result.agent.agent_type,
            "trust_mode": result.agent.trust_mode,
            "is_active": bool(result.agent.is_active),
        },
        "package_key": result.package_key,
        "permission_mode": result.permission_mode,
        "document_section_count": result.document_section_count,
        "skill_count": result.skill_count,
        "tool_grant_count": result.tool_grant_count,
        "trigger_count": result.trigger_count,
    }


@router.get("/agents/{agent_id}")
async def get_agent_detail(
    agent_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="agent_not_found")

    service = AgentDocumentService(session)
    sections = await service.list_sections(
        workspace_id=workspace.id,
        document_kind="agent",
        subject_type="agent",
        subject_id=agent.id,
    )
    skills = await service.list_skills(workspace_id=workspace.id, agent_id=agent.id)
    grants = await ToolGrantService(session).list_for_workspace(
        workspace_id=workspace.id, agent_id=agent.id
    )
    grants = [grant for grant in grants if is_external_tool_scope(grant.scope)]
    triggers = await TriggerService(session).list_for_workspace(
        workspace_id=workspace.id, agent_id=agent.id
    )
    recent_actions = await _recent_agent_actions(
        session=session, workspace_id=workspace.id, agent_id=agent.id
    )
    rendered = render_agent_md(agent, sections, skills)
    return {
        "schema_version": "intelligence_agent_detail.v1",
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "agent_type": agent.agent_type,
            "trust_mode": agent.trust_mode,
            "is_active": agent.is_active,
            "package_key": _agent_package_key(agent),
            "permission_mode": _agent_permission_mode(agent),
            "contact_scope": agent.contact_scope,
        },
        "enforced_config": {
            "permission_mode": _agent_permission_mode(agent),
            "trust_mode": agent.trust_mode,
            "is_active": bool(agent.is_active),
            "contact_scope": agent.contact_scope,
            "brain_scopes": _list_config(agent.knowledge_config, "brain_scopes"),
            "tool_scopes": _list_config(agent.tools_config, "tool_scopes"),
            "channel_mode": str((agent.channel_config or {}).get("mode") or ""),
        },
        "drift_warnings": [
            warning.to_response()
            for warning in _agent_drift_warnings(agent=agent, sections=sections)
        ],
        "sections": [section.model_dump(mode="json") for section in sections],
        "skills": [skill.model_dump(mode="json") for skill in skills],
        "tool_grants": [grant.model_dump(mode="json") for grant in grants],
        "triggers": [trigger.model_dump(mode="json") for trigger in triggers],
        "recent_actions": recent_actions,
        "rendered": rendered.model_dump(mode="json"),
    }


@router.post(
    "/agents/{agent_id}/tool-grants/propose",
    status_code=status.HTTP_201_CREATED,
)
async def propose_agent_tool_grant_change(
    agent_id: int,
    payload: ToolGrantProposalInput,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="agent_not_found")
    if payload.scope not in TELEGRAM_TOOL_SCOPES:
        raise HTTPException(status_code=400, detail="unsupported_tool_scope")

    grants = await ToolGrantService(session).list_for_workspace(
        workspace_id=workspace.id, agent_id=agent.id
    )
    matching_grant = next(
        (grant for grant in grants if grant.scope == payload.scope),
        None,
    )
    if payload.action == "grant" and matching_grant is not None and matching_grant.active:
        raise HTTPException(status_code=409, detail="tool_grant_already_active")
    if payload.action == "revoke" and (
        matching_grant is None or not matching_grant.active
    ):
        raise HTTPException(status_code=409, detail="tool_grant_not_active")

    state_token = _tool_grant_state_token(matching_grant)
    digest_seed = (
        f"{workspace.id}:{agent.id}:{payload.action}:{payload.scope}:"
        f"{state_token}:{payload.reason.strip()}"
    )
    digest = hashlib.sha256(digest_seed.encode("utf-8")).hexdigest()[:24]
    proposal_id = f"agent-tool-grant:{digest}"
    idempotency_key = f"agent-tool-grant:{digest}"
    title = (
        f"{_tool_scope_label(payload.scope)} ruxsatini "
        f"{'qo‘shish' if payload.action == 'grant' else 'o‘chirish'}"
    )
    proposal = CommercialActionProposal(
        proposal_id=proposal_id,
        workspace_id=workspace.id,
        conversation_id=0,
        customer_id=0,
        action_type="agent.update_tool_grant",
        lifecycle_state="waiting_approval",
        execution_mode="suggest_only",
        risk_level=_tool_grant_risk(payload.action, payload.scope),
        requires_approval=True,
        executor_runtime="workspace_os",
        priority="medium",
        confidence=1.0,
        reason_code="agent_tool_grant_change_requires_owner_approval",
        source_refs=[f"agent_tool_grant:{agent.id}:{payload.scope}"],
        payload={
            "title": title,
            "summary": (
                f"{agent.name} agenti uchun {_tool_scope_label(payload.scope).lower()} "
                f"ruxsati {'qo‘shiladi' if payload.action == 'grant' else 'o‘chiriladi'}."
            ),
            "agent_id": agent.id,
            "agent_name": agent.name,
            "operation": payload.action,
            "tool_scope": payload.scope,
            "tool_scope_label": _tool_scope_label(payload.scope),
            "grant_reason": payload.reason.strip(),
        },
        idempotency_key=idempotency_key,
        correlation_id=payload.correlation_id,
        trace_id=f"trace:{proposal_id}",
    )
    repository = CommercialSpineRepository(session)
    created = await repository.persist_action_proposal(proposal)
    if not created:
        existing = await repository.get_action_proposal(
            workspace_id=workspace.id,
            proposal_id=proposal.proposal_id,
        )
        proposal = existing or proposal
    await session.commit()
    return {
        "schema_version": "agent_tool_grant_proposal.v1",
        "created": created,
        "proposal": proposal.model_dump(mode="json"),
    }


@dataclass(frozen=True)
class _AgentDriftWarning:
    code: str
    title_uz: str
    detail_uz: str
    document_value: str | None
    enforced_value: str

    def to_response(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "title_uz": self.title_uz,
            "detail_uz": self.detail_uz,
            "document_value": self.document_value,
            "enforced_value": self.enforced_value,
        }


def _agent_package_key(agent: Agent) -> str:
    persona = agent.persona or {}
    package_key = str(persona.get("package_key") or "").strip()
    if package_key:
        return package_key
    return str(agent.agent_type or "custom")


def _tool_grant_state_token(grant: Any | None) -> str:
    if grant is None:
        return "missing"
    if grant.active:
        return f"active:{grant.id}:{grant.granted_at.isoformat()}"
    revoked = grant.revoked_at.isoformat() if grant.revoked_at else "revoked"
    return f"revoked:{grant.id}:{revoked}"


def _tool_grant_risk(operation: str, scope: str) -> str:
    if operation == "revoke":
        return "low"
    definition = TELEGRAM_TOOL_DEFINITIONS.get(scope)
    return definition.risk_level if definition is not None else "medium"


def _tool_grant_read_item(grant: Any) -> dict[str, Any]:
    item = grant.model_dump(mode="json")
    scope = str(item.get("scope") or "")
    definition = TELEGRAM_TOOL_DEFINITIONS.get(scope)
    item["connector"] = "telegram" if scope.startswith("telegram.") else "unknown"
    item["scope_label"] = _tool_scope_label(scope)
    item["scope_description"] = _tool_scope_description(scope)
    if definition is not None:
        item["risk_level"] = definition.risk_level
        item["mutates_external_state"] = definition.mutates_external_state
        item["requires_action_proposal"] = definition.requires_action_proposal
        item["runtime_boundary"] = definition.runtime_boundary
    return item


def _tool_scope_label(scope: str) -> str:
    definition = TELEGRAM_TOOL_DEFINITIONS.get(scope)
    if definition is not None:
        return definition.label_uz
    return scope.replace(".", " ").replace("_", " ")


def _tool_scope_description(scope: str) -> str:
    definition = TELEGRAM_TOOL_DEFINITIONS.get(scope)
    if definition is not None:
        return definition.description_uz
    return "Ushbu integratsiya ruxsati agent ishida ishlatiladi."


def _trigger_state_token(trigger: Any | None) -> str:
    if trigger is None:
        return "missing"
    return f"{'active' if trigger.active else 'inactive'}:{trigger.id}:{trigger.updated_at.isoformat()}"


def _trigger_event_label(event_source: str) -> str:
    labels = {
        "channel_message_received": "Yangi Telegram xabar",
        "conversation_state_changed": "Suhbat holati o‘zgardi",
        "customer_stage_changed": "Mijoz bosqichi o‘zgardi",
        "source_added": "Manba qo‘shildi",
        "source_changed": "Manba yangilandi",
        "schedule": "Jadval",
        "owner_bi_command": "BI buyrug‘i",
        "integration_webhook": "Integratsiya signali",
        "task_due": "Vazifa muddati",
        "catalog_conflict_detected": "Katalog konflikti",
    }
    return labels.get(event_source, event_source.replace("_", " "))


def _agent_permission_mode(agent: Agent) -> str:
    tools_config = agent.tools_config or {}
    return str(tools_config.get("permission_mode") or "ask_always")


def _list_config(config: dict[str, Any] | None, key: str) -> list[str]:
    value = (config or {}).get(key)
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _agent_drift_warnings(
    *, agent: Agent, sections: list[Any]
) -> list[_AgentDriftWarning]:
    runtime_section = next(
        (
            section
            for section in sections
            if getattr(section, "section_key", None) == "runtime_config"
        ),
        None,
    )
    if runtime_section is None:
        return []

    document_permission = _runtime_config_value(runtime_section.body, "Permission mode")
    enforced_permission = _agent_permission_mode(agent)
    if document_permission and document_permission != enforced_permission:
        return [
            _AgentDriftWarning(
                code="permission_mode_drift",
                title_uz="Hujjat va haqiqiy ruxsat mos emas",
                detail_uz=(
                    "AGENT.md boshqa ruxsat rejimini ko'rsatmoqda. Ishlashda "
                    "haqiqiy sozlama ishlatiladi."
                ),
                document_value=document_permission,
                enforced_value=enforced_permission,
            )
        ]
    return []


def _runtime_config_value(body: str, label: str) -> str | None:
    prefix = f"{label}:"
    for raw_line in str(body or "").splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


async def _recent_agent_actions(
    *, session: AsyncSession, workspace_id: int, agent_id: int
) -> list[dict[str, Any]]:
    result = await session.scalars(
        select(CommercialActionProposalRecord)
        .where(CommercialActionProposalRecord.workspace_id == workspace_id)
        .order_by(CommercialActionProposalRecord.created_at.desc())
        .limit(30)
    )
    rows: list[dict[str, Any]] = []
    for proposal in result.all():
        payload = proposal.payload or {}
        raw = proposal.raw_proposal or {}
        payload_agent_id = payload.get("agent_id") or raw.get("agent_id")
        if payload_agent_id is None:
            continue
        if str(payload_agent_id) != str(agent_id):
            continue
        rows.append(
            {
                "proposal_id": proposal.proposal_id,
                "action_type": proposal.action_type,
                "lifecycle_state": proposal.lifecycle_state,
                "risk_level": proposal.risk_level,
                "reason_code": proposal.reason_code,
                "summary_uz": _action_summary_uz(proposal.action_type),
                "created_at": proposal.created_at.isoformat(),
            }
        )
        if len(rows) >= 6:
            break
    return rows


def _action_summary_uz(action_type: str) -> str:
    labels = {
        "send_reply": "Javob yuborish taklifi",
        "send_status_message": "Mijozga holat xabari",
        "edit_reply": "Javobni tahrirlash",
        "edit_sent_reply": "Yuborilgan javobni tuzatish",
        "create_business_task": "Egaga vazifa taklifi",
        "schedule_sales_follow_up": "Qayta yozish taklifi",
        "catalog.propose_update": "Katalog yangilash taklifi",
        "agent.propose_create": "Yangi agent taklifi",
    }
    return labels.get(action_type, action_type.replace("_", " "))


# ---------------------------------------------------------------------------
# Tool grants (Phase 7 — Integrations)
# ---------------------------------------------------------------------------


@router.get("/tool-catalog")
async def list_tool_catalog(
    workspace: WorkspaceDep,
    connector: Annotated[str | None, Query(min_length=1)] = None,
) -> dict[str, Any]:
    """Return owner-visible integration tools agents can be granted.

    Telegram is the first MCP-style connector. The catalog is the backend-owned
    source for scope labels, risk, mutation behavior, and approval semantics so
    future connectors can extend the same contract without UI hardcoding.
    """

    _ = workspace
    requested = (connector or "").strip().lower()
    definitions = [
        definition
        for definition in TELEGRAM_TOOL_DEFINITIONS.values()
        if not requested or definition.connector == requested
    ]
    return {
        "schema_version": "intelligence_tool_catalog.v1",
        "items": [definition.model_dump(mode="json") for definition in definitions],
    }


@router.get("/tool-grants")
async def list_tool_grants(
    workspace: WorkspaceDep,
    session: SessionDep,
    agent_id: int | None = None,
) -> dict[str, Any]:
    service = ToolGrantService(session)
    grants = await service.list_for_workspace(
        workspace_id=workspace.id, agent_id=agent_id
    )
    grants = [grant for grant in grants if is_external_tool_scope(grant.scope)]
    return {
        "schema_version": "intelligence_tool_grants.v1",
        "items": [_tool_grant_read_item(grant) for grant in grants],
    }


# ---------------------------------------------------------------------------
# Triggers (Phase 5)
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/triggers")
async def list_agent_triggers(
    agent_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="agent_not_found")
    service = TriggerService(session)
    triggers = await service.list_for_workspace(
        workspace_id=workspace.id, agent_id=agent_id
    )
    return {
        "schema_version": "intelligence_triggers.v1",
        "items": [trigger.model_dump(mode="json") for trigger in triggers],
    }


@router.post(
    "/agents/{agent_id}/triggers/propose",
    status_code=status.HTTP_201_CREATED,
)
async def propose_agent_trigger_change(
    agent_id: int,
    payload: TriggerProposalInput,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="agent_not_found")

    trigger_input = (
        payload.to_trigger_input(owner_agent_id=agent_id)
        if payload.operation == "create"
        else None
    )
    existing_trigger = None
    state_token = "new"
    if payload.operation == "create" and trigger_input is not None:
        trigger_key = trigger_input.compute_idempotency_key()
        existing = await TriggerService(session).list_for_workspace(
            workspace_id=workspace.id,
            agent_id=agent.id,
        )
        existing_trigger = next(
            (trigger for trigger in existing if trigger.idempotency_key == trigger_key),
            None,
        )
        if existing_trigger is not None and existing_trigger.active:
            raise HTTPException(status_code=409, detail="trigger_already_active")
        state_token = _trigger_state_token(existing_trigger)
    elif payload.operation == "deactivate":
        existing = await TriggerService(session).list_for_workspace(
            workspace_id=workspace.id,
            agent_id=agent.id,
        )
        existing_trigger = next(
            (trigger for trigger in existing if trigger.id == payload.trigger_id),
            None,
        )
        if existing_trigger is None:
            raise HTTPException(status_code=404, detail="trigger_not_found")
        if not existing_trigger.active:
            raise HTTPException(status_code=409, detail="trigger_not_active")
        state_token = _trigger_state_token(existing_trigger)

    digest_seed = (
        f"{workspace.id}:{agent.id}:{payload.operation}:"
        f"{payload.trigger_id or ''}:{payload.event_source or ''}:"
        f"{payload.action_proposal_type or ''}:{state_token}:"
        f"{payload.matching_scope}:{payload.notes.strip()}"
    )
    digest = hashlib.sha256(digest_seed.encode("utf-8")).hexdigest()[:24]
    proposal_id = f"agent-trigger:{digest}"
    title = (
        f"{_trigger_event_label(str(payload.event_source))} triggerini qo‘shish"
        if payload.operation == "create"
        else f"{_trigger_event_label(existing_trigger.event_source if existing_trigger else '')} triggerini o‘chirish"
    )
    proposal = CommercialActionProposal(
        proposal_id=proposal_id,
        workspace_id=workspace.id,
        conversation_id=0,
        customer_id=0,
        action_type="agent.update_trigger",
        lifecycle_state="waiting_approval",
        execution_mode="suggest_only",
        risk_level="medium",
        requires_approval=True,
        executor_runtime="workspace_os",
        priority="medium",
        confidence=1.0,
        reason_code="agent_trigger_change_requires_owner_approval",
        source_refs=[f"agent_trigger:{agent.id}:{payload.operation}"],
        payload={
            "title": title,
            "summary": (
                f"{agent.name} agenti uchun trigger "
                f"{'qo‘shiladi' if payload.operation == 'create' else 'o‘chiriladi'}."
            ),
            "agent_id": agent.id,
            "agent_name": agent.name,
            "operation": payload.operation,
            "trigger_id": payload.trigger_id,
            "trigger_label": _trigger_event_label(
                str(payload.event_source or getattr(existing_trigger, "event_source", ""))
            ),
            "trigger": (
                trigger_input.model_dump(mode="json", exclude_none=True)
                if trigger_input is not None
                else None
            ),
        },
        idempotency_key=f"agent-trigger:{digest}",
        correlation_id=payload.correlation_id,
        trace_id=f"trace:{proposal_id}",
    )
    repository = CommercialSpineRepository(session)
    created = await repository.persist_action_proposal(proposal)
    if not created:
        existing_proposal = await repository.get_action_proposal(
            workspace_id=workspace.id,
            proposal_id=proposal.proposal_id,
        )
        proposal = existing_proposal or proposal
    await session.commit()
    return {
        "schema_version": "agent_trigger_proposal.v1",
        "created": created,
        "proposal": proposal.model_dump(mode="json"),
    }


@router.post("/agents/{agent_id}/triggers")
async def create_agent_trigger(
    agent_id: int,
    payload: TriggerInput,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    if payload.owner_agent_id != agent_id:
        raise HTTPException(
            status_code=400, detail="owner_agent_id must match the path agent_id"
        )
    service = TriggerService(session)
    try:
        trigger = await service.create(workspace_id=workspace.id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {
        "schema_version": "intelligence_trigger.v1",
        "trigger": trigger.model_dump(mode="json"),
    }


@router.delete(
    "/triggers/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def deactivate_trigger(
    trigger_id: int,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> None:
    service = TriggerService(session)
    try:
        await service.deactivate(workspace_id=workspace.id, trigger_id=trigger_id)
    except TriggerNotFoundError as exc:
        raise HTTPException(status_code=404, detail="trigger_not_found") from exc
    await session.commit()


@router.post("/agents/{agent_id}/sections")
async def upsert_agent_section(
    agent_id: int,
    payload: AgentDocumentSectionInput,
    workspace: WorkspaceDep,
    session: SessionDep,
) -> dict[str, Any]:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="agent_not_found")

    if payload.document_kind != "agent" or payload.subject_type != "agent":
        raise HTTPException(
            status_code=400,
            detail="document_kind must be 'agent' and subject_type must be 'agent'",
        )
    if payload.subject_id != agent_id:
        raise HTTPException(
            status_code=400, detail="subject_id must match the path agent_id"
        )

    service = AgentDocumentService(session)
    section = await service.upsert_section(workspace_id=workspace.id, payload=payload)
    await session.commit()
    return {
        "schema_version": "intelligence_agent_section.v1",
        "section": section.model_dump(mode="json"),
    }
