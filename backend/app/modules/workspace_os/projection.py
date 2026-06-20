from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.agent_skill import AgentSkill
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.tool_grant import ToolGrant
from app.models.trigger import Trigger
from app.models.workspace import Workspace
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.onboarding_learning.source_progress import (
    build_onboarding_source_learning_projection,
)
from app.modules.tool_catalog import external_tool_scopes
from app.modules.workspace_os.contracts import (
    WorkspaceOSActionStatus,
    WorkspaceOSAgentStatus,
    WorkspaceOSDocumentSectionPreview,
    WorkspaceOSDocumentStatus,
    WorkspaceOSIssue,
    WorkspaceOSProjection,
    WorkspaceOSReadiness,
    WorkspaceOSSourceStatus,
    WorkspaceOSTaskStatus,
)
from app.modules.workspace_os.default_packages import (
    DEFAULT_AGENT_ORDER,
    DEFAULT_AGENT_PACKAGES,
    REQUIRED_BUSINESS_SECTION_KEYS,
)


class WorkspaceOSProjectionService:
    """Build the owner-facing operating-system projection without mutation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = CommercialSpineRepository(session)

    async def build(self, *, workspace: Workspace) -> WorkspaceOSProjection:
        agents = await self._list_agents(workspace_id=workspace.id)
        sections = await self._list_sections(workspace_id=workspace.id)
        skills = await self._list_skills(workspace_id=workspace.id)
        grants = await self._list_grants(workspace_id=workspace.id)
        triggers = await self._list_triggers(workspace_id=workspace.id)

        issues: list[WorkspaceOSIssue] = []
        agent_statuses = self._agent_statuses(
            agents=agents,
            sections=sections,
            skills=skills,
            grants=grants,
            triggers=triggers,
            issues=issues,
            include_missing_agent_issues=bool(workspace.onboarding_completed),
        )
        document_status = self._document_status(sections=sections, issues=issues)
        source_status = await self._source_status(workspace_id=workspace.id)
        action_status = await self._action_status(workspace_id=workspace.id)
        task_status = await self._task_status()

        if source_status.summary.get("total", 0) == 0:
            issues.append(
                WorkspaceOSIssue(
                    code="sources_missing",
                    severity="info",
                    target_kind="sources",
                    target_ref="workspace:sources",
                    title_uz="Manba hali qo'shilmagan",
                    detail_uz=(
                        "Javoblar ishonchli bo'lishi uchun fayl, sayt, Telegram "
                        "kanal yoki qo'lda yozilgan ma'lumot qo'shing."
                    ),
                    action_label_uz="Manba qo'shish",
                )
            )
        if action_status.needs_approval > 0:
            issues.append(
                WorkspaceOSIssue(
                    code="actions_need_approval",
                    severity="warning",
                    target_kind="actions",
                    target_ref="workspace:actions",
                    title_uz="Tasdiq kutayotgan ishlar bor",
                    detail_uz=(f"{action_status.needs_approval} ta agent taklifi egadan tasdiq kutyapti."),
                    action_label_uz="Ko'rib chiqish",
                )
            )

        readiness = _readiness(issues=issues, agent_statuses=agent_statuses)
        return WorkspaceOSProjection(
            workspace_id=workspace.id,
            workspace_name=workspace.name,
            onboarding_completed=bool(workspace.onboarding_completed),
            telegram_connected=bool(workspace.telegram_connected),
            generated_at=utc_now(),
            readiness=readiness,
            agents=agent_statuses,
            documents=document_status,
            sources=source_status,
            actions=action_status,
            tasks=task_status,
        )

    async def _list_agents(self, *, workspace_id: int) -> list[Agent]:
        return list(
            (
                await self._session.scalars(select(Agent).where(Agent.workspace_id == workspace_id).order_by(Agent.id))
            ).all()
        )

    async def _list_sections(self, *, workspace_id: int) -> list[AgentDocumentSection]:
        return list(
            (
                await self._session.scalars(
                    select(AgentDocumentSection)
                    .where(AgentDocumentSection.workspace_id == workspace_id)
                    .order_by(
                        AgentDocumentSection.document_kind,
                        AgentDocumentSection.subject_id,
                        AgentDocumentSection.order_index,
                    )
                )
            ).all()
        )

    async def _list_skills(self, *, workspace_id: int) -> list[AgentSkill]:
        return list(
            (
                await self._session.scalars(
                    select(AgentSkill).where(AgentSkill.workspace_id == workspace_id).order_by(AgentSkill.id)
                )
            ).all()
        )

    async def _list_grants(self, *, workspace_id: int) -> list[ToolGrant]:
        return list(
            (
                await self._session.scalars(
                    select(ToolGrant).where(ToolGrant.workspace_id == workspace_id).order_by(ToolGrant.id)
                )
            ).all()
        )

    async def _list_triggers(self, *, workspace_id: int) -> list[Trigger]:
        return list(
            (
                await self._session.scalars(
                    select(Trigger).where(Trigger.workspace_id == workspace_id).order_by(Trigger.id)
                )
            ).all()
        )

    def _agent_statuses(
        self,
        *,
        agents: list[Agent],
        sections: list[AgentDocumentSection],
        skills: list[AgentSkill],
        grants: list[ToolGrant],
        triggers: list[Trigger],
        issues: list[WorkspaceOSIssue],
        include_missing_agent_issues: bool = True,
    ) -> list[WorkspaceOSAgentStatus]:
        agents_by_key = {_agent_package_key(agent): agent for agent in agents if _agent_package_key(agent)}
        skills_by_agent = _count_by_agent(skills)
        skill_names_by_agent = _skill_names_by_agent(skills)
        sections_by_agent = _count_sections_by_agent(sections)
        section_previews_by_agent = _section_previews_by_agent(sections)
        grants_by_agent = _grants_by_agent(grants)
        triggers_by_agent = _triggers_by_agent(triggers)

        statuses: list[WorkspaceOSAgentStatus] = []
        for package_key in DEFAULT_AGENT_ORDER:
            package = DEFAULT_AGENT_PACKAGES[package_key]
            agent = agents_by_key.get(package_key)
            if agent is None:
                if include_missing_agent_issues:
                    issues.append(
                        WorkspaceOSIssue(
                            code="agent_missing",
                            severity="critical",
                            target_kind="agent",
                            target_ref=f"agent_package:{package_key}",
                            title_uz=f"{package.display_name} yo'q",
                            detail_uz="Onboarding agentni to'liq yaratmagan. Qayta yig'ish kerak.",
                            action_label_uz="Qayta yig'ish",
                        )
                    )
                statuses.append(
                    WorkspaceOSAgentStatus(
                        package_key=package_key,
                        present=False,
                        name=package.display_name,
                        agent_type=package.agent_type,
                    )
                )
                continue

            agent_grants = grants_by_agent.get(agent.id, [])
            expected_external_scopes = set(external_tool_scopes(package.tool_scopes))
            agent_grants = [grant for grant in agent_grants if grant.scope in expected_external_scopes]
            active_grants = [grant for grant in agent_grants if grant.active]
            active_scopes = {grant.scope for grant in active_grants}
            configured_capabilities = set(_agent_configured_tool_scopes(agent))
            missing_capabilities = sorted(set(package.tool_scopes) - configured_capabilities)
            missing_scopes = sorted(expected_external_scopes - active_scopes)
            agent_triggers = triggers_by_agent.get(agent.id, [])
            active_triggers = [trigger for trigger in agent_triggers if trigger.active]
            missing_trigger_count = max(0, len(package.triggers) - len(active_triggers))
            document_section_count = sections_by_agent.get(agent.id, 0)
            skill_count = skills_by_agent.get(agent.id, 0)

            health = "ready"
            if (
                not agent.is_active
                or missing_capabilities
                or missing_scopes
                or missing_trigger_count
                or document_section_count == 0
                or skill_count == 0
            ):
                health = "degraded"
                issues.append(
                    WorkspaceOSIssue(
                        code="agent_degraded",
                        severity="warning",
                        target_kind="agent",
                        target_ref=f"agent:{agent.id}",
                        title_uz=f"{agent.name} to'liq tayyor emas",
                        detail_uz=_agent_degraded_detail(
                            is_active=agent.is_active,
                            missing_capabilities=missing_capabilities,
                            missing_scopes=missing_scopes,
                            missing_trigger_count=missing_trigger_count,
                            document_section_count=document_section_count,
                            skill_count=skill_count,
                        ),
                        action_label_uz="Agentni tekshirish",
                    )
                )

            statuses.append(
                WorkspaceOSAgentStatus(
                    package_key=package_key,
                    present=True,
                    id=agent.id,
                    name=agent.name,
                    agent_type=agent.agent_type,
                    is_active=bool(agent.is_active),
                    permission_mode=str((agent.tools_config or {}).get("permission_mode") or "ask_always"),
                    trust_mode=agent.trust_mode,
                    skill_count=skill_count,
                    document_section_count=document_section_count,
                    capability_count=len(configured_capabilities),
                    tool_grant_count=len(agent_grants),
                    active_tool_grant_count=len(active_grants),
                    trigger_count=len(agent_triggers),
                    active_trigger_count=len(active_triggers),
                    missing_capability_scopes=missing_capabilities,
                    missing_tool_scopes=missing_scopes,
                    missing_trigger_count=missing_trigger_count,
                    skill_names=skill_names_by_agent.get(agent.id, [])[:3],
                    document_preview=section_previews_by_agent.get(agent.id, [])[:3],
                    health=health,
                )
            )
        return statuses

    def _document_status(
        self,
        *,
        sections: list[AgentDocumentSection],
        issues: list[WorkspaceOSIssue],
    ) -> WorkspaceOSDocumentStatus:
        business_sections = [
            section
            for section in sections
            if section.document_kind == "business" and section.subject_type == "workspace"
        ]
        present_business_keys = {section.section_key for section in business_sections}
        missing_business_sections = sorted(set(REQUIRED_BUSINESS_SECTION_KEYS) - present_business_keys)
        if missing_business_sections:
            issues.append(
                WorkspaceOSIssue(
                    code="business_md_incomplete",
                    severity="warning",
                    target_kind="document",
                    target_ref="document:BUSINESS.md",
                    title_uz="BUSINESS.md to'liq emas",
                    detail_uz=(
                        "Biznes konteksti uchun kerakli bo'limlar yetishmayapti: "
                        + ", ".join(missing_business_sections)
                    ),
                    action_label_uz="To'ldirish",
                )
            )

        return WorkspaceOSDocumentStatus(
            business_section_count=len(business_sections),
            agent_section_count=sum(1 for section in sections if section.document_kind == "agent"),
            skill_section_count=sum(1 for section in sections if section.document_kind == "skill"),
            owner_edited_section_count=sum(1 for section in sections if section.generated_by == "owner"),
            missing_business_sections=missing_business_sections,
            sections_preview=[
                _section_preview(section)
                for section in sorted(business_sections, key=lambda item: item.order_index)[:5]
            ],
            business_md_ready=not missing_business_sections,
        )

    async def _source_status(self, *, workspace_id: int) -> WorkspaceOSSourceStatus:
        source_facts = await self._repository.list_facts(
            workspace_id=workspace_id,
            fact_type="business_source_fact",
            limit=250,
        )
        source_learning_projections = await self._repository.list_projections(
            workspace_id=workspace_id,
            projection_type="business_source_learning",
            limit=250,
        )
        projection = build_onboarding_source_learning_projection(
            source_facts=source_facts,
            source_learning_projections=source_learning_projections,
        )
        return WorkspaceOSSourceStatus(
            status=str(projection.get("status") or "idle"),
            summary=dict(projection.get("summary") or {}),
            sources=list(projection.get("sources") or []),
        )

    async def _action_status(self, *, workspace_id: int) -> WorkspaceOSActionStatus:
        rows = (
            await self._session.execute(
                select(CommercialActionProposalRecord.lifecycle_state).where(
                    CommercialActionProposalRecord.workspace_id == workspace_id
                )
            )
        ).all()
        counts = Counter(str(row[0] or "") for row in rows)
        return WorkspaceOSActionStatus(
            needs_approval=counts.get("proposed", 0) + counts.get("needs_approval", 0),
            scheduled=counts.get("scheduled", 0),
            running=counts.get("running", 0),
            done=counts.get("done", 0) + counts.get("executed", 0),
            failed=counts.get("failed", 0),
            rejected=counts.get("rejected", 0),
        )

    async def _task_status(self) -> WorkspaceOSTaskStatus:
        return WorkspaceOSTaskStatus(
            proposed=0,
            active=0,
            done=0,
            failed=0,
        )


def _agent_package_key(agent: Agent) -> str:
    persona = dict(agent.persona or {})
    package_key = str(persona.get("package_key") or "").strip()
    if package_key:
        return package_key
    if agent.is_default and agent.agent_type in DEFAULT_AGENT_PACKAGES:
        return agent.agent_type
    return ""


def _agent_configured_tool_scopes(agent: Agent) -> tuple[str, ...]:
    tools_config = dict(agent.tools_config or {})
    raw = tools_config.get("tool_scopes")
    if not isinstance(raw, list):
        return ()
    return tuple(str(scope).strip() for scope in raw if str(scope).strip())


def _count_by_agent(items: list[AgentSkill]) -> dict[int, int]:
    counts: defaultdict[int, int] = defaultdict(int)
    for item in items:
        if item.agent_id is not None and item.enabled:
            counts[int(item.agent_id)] += 1
    return dict(counts)


def _skill_names_by_agent(items: list[AgentSkill]) -> dict[int, list[str]]:
    names: defaultdict[int, list[str]] = defaultdict(list)
    for item in items:
        if item.agent_id is not None and item.enabled:
            names[int(item.agent_id)].append(item.name)
    return {agent_id: values[:5] for agent_id, values in names.items()}


def _count_sections_by_agent(sections: list[AgentDocumentSection]) -> dict[int, int]:
    counts: defaultdict[int, int] = defaultdict(int)
    for section in sections:
        if section.document_kind == "agent" and section.subject_id is not None:
            counts[int(section.subject_id)] += 1
    return dict(counts)


def _section_previews_by_agent(
    sections: list[AgentDocumentSection],
) -> dict[int, list[WorkspaceOSDocumentSectionPreview]]:
    previews: defaultdict[int, list[WorkspaceOSDocumentSectionPreview]] = defaultdict(list)
    for section in sorted(sections, key=lambda item: item.order_index):
        if section.document_kind == "agent" and section.subject_id is not None:
            previews[int(section.subject_id)].append(_section_preview(section))
    return dict(previews)


def _section_preview(section: AgentDocumentSection) -> WorkspaceOSDocumentSectionPreview:
    evidence = section.source_evidence if isinstance(section.source_evidence, list) else []
    return WorkspaceOSDocumentSectionPreview(
        section_key=section.section_key,
        title=section.title,
        body_preview=_preview_text(section.body),
        generated_by=section.generated_by,
        source_evidence_count=len(evidence),
    )


def _preview_text(value: str, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _grants_by_agent(grants: list[ToolGrant]) -> dict[int, list[ToolGrant]]:
    result: defaultdict[int, list[ToolGrant]] = defaultdict(list)
    for grant in grants:
        result[int(grant.agent_id)].append(grant)
    return dict(result)


def _triggers_by_agent(triggers: list[Trigger]) -> dict[int, list[Trigger]]:
    result: defaultdict[int, list[Trigger]] = defaultdict(list)
    for trigger in triggers:
        result[int(trigger.owner_agent_id)].append(trigger)
    return dict(result)


def _agent_degraded_detail(
    *,
    is_active: bool,
    missing_capabilities: list[str],
    missing_scopes: list[str],
    missing_trigger_count: int,
    document_section_count: int,
    skill_count: int,
) -> str:
    parts: list[str] = []
    if not is_active:
        parts.append("agent o'chirilgan")
    if missing_capabilities:
        parts.append(f"{len(missing_capabilities)} ta ichki qobiliyat yetishmayapti")
    if missing_scopes:
        parts.append(f"{len(missing_scopes)} ta tashqi ruxsat yetishmayapti")
    if missing_trigger_count:
        parts.append(f"{missing_trigger_count} ta trigger yetishmayapti")
    if document_section_count == 0:
        parts.append("AGENT.md bo'limlari yo'q")
    if skill_count == 0:
        parts.append("skill ulanmagan")
    return "; ".join(parts) if parts else "Konfiguratsiyani tekshirish kerak."


def _readiness(
    *,
    issues: list[WorkspaceOSIssue],
    agent_statuses: list[WorkspaceOSAgentStatus],
) -> WorkspaceOSReadiness:
    critical_count = sum(1 for issue in issues if issue.severity == "critical")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    ready_agents = sum(1 for agent in agent_statuses if agent.health == "ready")
    percent = round((ready_agents / max(len(DEFAULT_AGENT_ORDER), 1)) * 100)

    if all(not agent.present for agent in agent_statuses):
        status = "not_provisioned"
        percent = 0
    elif critical_count:
        status = "degraded"
        percent = min(percent, 60)
    elif warning_count:
        status = "needs_review"
        percent = min(max(percent, 70), 90)
    else:
        status = "ready"
        percent = 100
    return WorkspaceOSReadiness(status=status, percent=percent, issues=issues)
