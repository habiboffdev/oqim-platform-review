from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_document import AgentDocumentSection
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import (
    AgentDocumentSectionInput,
    AgentSkillInput,
)
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.tool_catalog import external_tool_scopes, internal_capability_scopes
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.modules.triggers.contracts import TriggerInput
from app.modules.triggers.service import TriggerService
from app.modules.workspace_os.default_packages import (
    DEFAULT_AGENT_ORDER,
    DEFAULT_AGENT_PACKAGES,
    AgentPackageSpec,
    SkillSpec,
    TriggerSpec,
)

VALID_PERMISSION_MODES = frozenset({"ask_always", "auto_approve", "full_access"})


@dataclass(frozen=True)
class WorkspaceOSProvisioningResult:
    workspace_id: int
    selected_agent_keys: tuple[str, ...]
    agent_ids: dict[str, int] = field(default_factory=dict)
    agent_count: int = 0
    skill_count: int = 0
    document_section_count: int = 0
    tool_grant_count: int = 0
    trigger_count: int = 0


class WorkspaceOSProvisioner:
    """Idempotently assemble the workspace operating system.

    This boundary is intentionally boring: it writes deterministic system
    records and leaves semantic extraction/reasoning to upstream LLM-backed
    services. Re-running it should never duplicate agents, skills, triggers, or
    grants. Owner-edited document sections are preserved.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._documents = AgentDocumentService(session)
        self._grants = ToolGrantService(session)
        self._triggers = TriggerService(session)

    async def provision(
        self,
        *,
        workspace: Workspace,
        profile: dict[str, Any] | None = None,
        preferences: dict[str, Any] | None = None,
        documents: bool = True,
    ) -> WorkspaceOSProvisioningResult:
        """Assemble the workspace operating system.

        When ``documents`` is False, the deterministic BUSINESS.md / AGENT.md
        section provisioning is skipped — agents, grants, triggers, and skills
        are still created. This lets onboarding bootstrap the default agents
        while the new doc-gen orchestrator owns document content. Skill
        *document* sections are part of doc provisioning and are skipped too;
        skill records themselves are still created.
        """
        preferences = preferences or {}
        profile = profile or {}
        selected_keys = _selected_default_agents(preferences.get("default_agents"))
        permission_mode = _permission_mode_from_preferences(preferences)

        document_sections = 0
        if documents:
            document_sections += await self._provision_business_document(
                workspace=workspace,
                profile=profile,
                permission_mode=permission_mode,
            )

        agent_ids: dict[str, int] = {}
        skill_count = 0
        tool_grant_count = 0
        trigger_count = 0

        for key in selected_keys:
            package = DEFAULT_AGENT_PACKAGES[key]
            agent = await self._upsert_agent(
                workspace=workspace,
                package=package,
                permission_mode=permission_mode,
            )
            agent_ids[key] = agent.id
            if documents:
                document_sections += await self._provision_agent_document(
                    workspace_id=workspace.id,
                    agent=agent,
                    package=package,
                    permission_mode=permission_mode,
                )
            created_skills, skill_sections = await self._provision_agent_skills(
                workspace_id=workspace.id,
                agent=agent,
                package=package,
                documents=documents,
            )
            skill_count += created_skills
            document_sections += skill_sections
            tool_grant_count += await self._provision_tool_grants(
                workspace_id=workspace.id,
                agent=agent,
                package=package,
            )
            trigger_count += await self._provision_triggers(
                workspace_id=workspace.id,
                agent=agent,
                package=package,
                permission_mode=permission_mode,
            )

        return WorkspaceOSProvisioningResult(
            workspace_id=workspace.id,
            selected_agent_keys=selected_keys,
            agent_ids=agent_ids,
            agent_count=len(agent_ids),
            skill_count=skill_count,
            document_section_count=document_sections,
            tool_grant_count=tool_grant_count,
            trigger_count=trigger_count,
        )

    async def _provision_business_document(
        self,
        *,
        workspace: Workspace,
        profile: dict[str, Any],
        permission_mode: str,
    ) -> int:
        business_profile = _dict(profile.get("business_profile"))
        preferences = _dict(profile.get("preferences"))
        sources = _dict(profile.get("sources"))
        owner_rules = _dict(profile.get("owner_rules"))
        offer = _clean_text(business_profile.get("offer_summary")) or (
            workspace.description or "Biznes konteksti hali o'rganilmoqda."
        )
        category = _clean_text(workspace.type) or _clean_text(business_profile.get("category")) or "general"
        language = _clean_text(business_profile.get("preferred_language")) or "uzbek_latin"
        tone = _clean_text(business_profile.get("tone")) or "business-like"

        sections = (
            (
                "business_overview",
                "Biznes haqida",
                f"Nomi: {workspace.name}\nYo'nalish: {category}\nAsosiy taklif yoki support sohasi: {offer}",
            ),
            (
                "what_we_sell_or_support",
                "Nimani sotadi yoki support qiladi",
                "Brain katalogi, bilim bazasi, qoidalar, manba dalillari va ega tahrirlari haqiqat manbasi bo'ladi. Bu workspace mahsulot, xizmat, kurs, ko'chmas mulk, klinika xizmati sotishi yoki katalogsiz support qilishi mumkin.",
            ),
            (
                "voice_and_language",
                "Ovoz va til",
                f"Asosiy til: {language}\nBoshlang'ich ohang: {tone}\nOvoz approved suhbat namunalari va ega tahrirlari orqali yaxshilanadi.",
            ),
            (
                "source_priority",
                "Manba ustuvorligi",
                _source_priority_text(sources),
            ),
            (
                "owner_rules",
                "Ega qoidalari",
                _owner_rules_text(owner_rules),
            ),
            (
                "permission_policy",
                "Ruxsat siyosati",
                f"Asosiy ruxsat rejimi: {_permission_mode_label(permission_mode)}. Xavfli yuborishlar, to'lov, narx/ombor o'zgarishi, katalog o'chirish, agent yaratish, ruxsat o'zgarishi va integratsiya yozuvlari policy va audit talab qiladi.",
            ),
            (
                "missing_data_behavior",
                "Ma'lumot yetishmasa",
                "Dalil yetishmasa, agent nima yetishmayotganini aytadi, tabiiy keyingi savol beradi yoki egaga vazifa ochadi. Taxmin qilmaydi.",
            ),
            (
                "operating_preferences",
                "Ishlash afzalliklari",
                _preferences_text(preferences),
            ),
        )

        count = 0
        for order, (key, title, body) in enumerate(sections, start=10):
            await self._upsert_section_preserving_owner(
                workspace_id=workspace.id,
                payload=AgentDocumentSectionInput(
                    document_kind="business",
                    subject_type="workspace",
                    subject_id=None,
                    section_key=key,
                    title=title,
                    body=body,
                    order_index=order,
                    source_evidence=[{"source_ref": "onboarding:personalized_profile"}],
                    generated_by="workspace_os_provisioner",
                ),
            )
            count += 1
        return count

    async def _upsert_agent(
        self,
        *,
        workspace: Workspace,
        package: AgentPackageSpec,
        permission_mode: str,
    ) -> Agent:
        existing = await self._find_agent_by_package(workspace_id=workspace.id, package_key=package.key)
        trust_mode = _trust_mode_from_permission_mode(permission_mode)
        persona = {
            "schema_version": "agent_package.v1",
            "package_key": package.key,
            "package_version": "default.2026-05-17",
            "role": package.display_name,
            "mission": package.mission,
        }
        knowledge_config = {
            "brain_scopes": list(package.brain_scopes),
            "use_catalog": "catalog" in package.brain_scopes,
            "use_knowledge": True,
            "retrieval_core": True,
        }
        tools_config = {
            "tool_scopes": list(package.tool_scopes),
            "permission_mode": permission_mode,
        }
        channel_config = {
            "mode": "workspace_events",
            "trigger_sources": [trigger.event_source for trigger in package.triggers],
        }

        if existing is None:
            agent = Agent(
                workspace_id=workspace.id,
                name=package.display_name,
                is_default=True,
                is_active=True,
                persona=persona,
                instructions=package.mission,
                example_responses=[],
                knowledge_config=knowledge_config,
                channel_config=channel_config,
                tools_config=tools_config,
                trust_mode=trust_mode,
                auto_send_threshold=0.9 if permission_mode != "ask_always" else 0.0,
                escalation_topics=[],
                agent_type=package.agent_type,
                contact_scope=package.contact_scope,
            )
            self._session.add(agent)
            await self._session.flush()
            return agent

        existing.name = package.display_name
        existing.is_default = True
        existing.is_active = True
        existing.persona = {**(existing.persona or {}), **persona}
        existing.instructions = package.mission
        existing.knowledge_config = knowledge_config
        existing.channel_config = channel_config
        existing.tools_config = tools_config
        existing.trust_mode = trust_mode
        existing.agent_type = package.agent_type
        existing.contact_scope = package.contact_scope
        await self._session.flush()
        return existing

    async def _find_agent_by_package(self, *, workspace_id: int, package_key: str) -> Agent | None:
        result = await self._session.scalars(
            select(Agent).where(Agent.workspace_id == workspace_id).order_by(Agent.id.asc())
        )
        for agent in result.all():
            persona = agent.persona or {}
            if persona.get("package_key") == package_key:
                return agent
            if agent.agent_type == package_key and agent.is_default:
                return agent
        return None

    async def _provision_agent_document(
        self,
        *,
        workspace_id: int,
        agent: Agent,
        package: AgentPackageSpec,
        permission_mode: str,
    ) -> int:
        count = 0
        for order, (key, title, body) in enumerate(package.sections, start=10):
            await self._upsert_section_preserving_owner(
                workspace_id=workspace_id,
                payload=AgentDocumentSectionInput(
                    document_kind="agent",
                    subject_type="agent",
                    subject_id=agent.id,
                    section_key=key,
                    title=title,
                    body=body,
                    order_index=order,
                    source_evidence=[{"source_ref": f"agent_package:{package.key}"}],
                    generated_by="workspace_os_provisioner",
                ),
            )
            count += 1

        await self._upsert_section_preserving_owner(
            workspace_id=workspace_id,
            payload=AgentDocumentSectionInput(
                document_kind="agent",
                subject_type="agent",
                subject_id=agent.id,
                section_key="runtime_config",
                title="Ruxsatlar va chegaralar",
                body=_agent_runtime_text(
                    permission_mode=permission_mode,
                    brain_scopes=package.brain_scopes,
                    tool_scopes=package.tool_scopes,
                ),
                order_index=90,
                source_evidence=[{"source_ref": f"agent_package:{package.key}"}],
                generated_by="workspace_os_provisioner",
            ),
        )
        return count + 1

    async def _provision_agent_skills(
        self,
        *,
        workspace_id: int,
        agent: Agent,
        package: AgentPackageSpec,
        documents: bool = True,
    ) -> tuple[int, int]:
        skill_count = 0
        section_count = 0
        for skill in package.skills:
            slug = f"{package.key.replace('_', '-')}-{skill.slug}"
            created = await self._documents.upsert_skill(
                workspace_id=workspace_id,
                payload=AgentSkillInput(
                    slug=slug,
                    name=skill.name,
                    description=skill.description,
                    instructions=skill.instructions,
                    when_to_use=skill.when_to_use,
                    when_not_to_use=skill.when_not_to_use,
                    tools=list(skill.tools),
                    agent_id=agent.id,
                    enabled=True,
                ),
            )
            skill_count += 1
            if documents:
                section_count += await self._provision_skill_document(
                    workspace_id=workspace_id,
                    skill_id=created.id,
                    spec=skill,
                    source_ref=f"agent_package:{package.key}:skill:{skill.slug}",
                )
        return skill_count, section_count

    async def _provision_skill_document(
        self,
        *,
        workspace_id: int,
        skill_id: int,
        spec: SkillSpec,
        source_ref: str,
    ) -> int:
        sections = (
            ("purpose", "Maqsad", spec.description),
            ("instructions", "Ko'rsatma", spec.instructions),
            ("use", "Qachon ishlatiladi", spec.when_to_use),
            ("avoid", "Qachon ishlatilmaydi", spec.when_not_to_use),
        )
        for order, (key, title, body) in enumerate(sections, start=10):
            await self._upsert_section_preserving_owner(
                workspace_id=workspace_id,
                payload=AgentDocumentSectionInput(
                    document_kind="skill",
                    subject_type="skill",
                    subject_id=skill_id,
                    section_key=key,
                    title=title,
                    body=body,
                    order_index=order,
                    source_evidence=[{"source_ref": source_ref}],
                    generated_by="workspace_os_provisioner",
                ),
            )
        return len(sections)

    async def _provision_tool_grants(
        self,
        *,
        workspace_id: int,
        agent: Agent,
        package: AgentPackageSpec,
    ) -> int:
        count = 0
        for scope in external_tool_scopes(package.tool_scopes):
            await self._grants.grant(
                workspace_id=workspace_id,
                payload=ToolGrantInput(
                    agent_id=agent.id,
                    scope=scope,
                    granted_by="workspace_os_provisioner",
                    grant_reason=f"{package.display_name} uchun asosiy integratsiya ruxsati.",
                    audit_metadata={
                        "package_key": package.key,
                        "package_version": "default.2026-05-17",
                    },
                ),
            )
            count += 1
        return count

    async def _provision_triggers(
        self,
        *,
        workspace_id: int,
        agent: Agent,
        package: AgentPackageSpec,
        permission_mode: str,
    ) -> int:
        count = 0
        for trigger in package.triggers:
            await self._triggers.create(
                workspace_id=workspace_id,
                payload=TriggerInput(
                    owner_agent_id=agent.id,
                    event_source=trigger.event_source,
                    action_proposal_type=trigger.action_proposal_type,
                    matching_scope=_trigger_scope(trigger),
                    permission_mode=permission_mode,
                    notes=trigger.notes,
                ),
            )
            count += 1
        return count

    async def _upsert_section_preserving_owner(
        self,
        *,
        workspace_id: int,
        payload: AgentDocumentSectionInput,
    ) -> None:
        existing = await self._find_section(workspace_id=workspace_id, payload=payload)
        if existing is not None and existing.generated_by == "owner":
            return
        await self._documents.upsert_section(workspace_id=workspace_id, payload=payload)

    async def _find_section(
        self,
        *,
        workspace_id: int,
        payload: AgentDocumentSectionInput,
    ) -> AgentDocumentSection | None:
        query = select(AgentDocumentSection).where(
            AgentDocumentSection.workspace_id == workspace_id,
            AgentDocumentSection.document_kind == payload.document_kind,
            AgentDocumentSection.subject_type == payload.subject_type,
            AgentDocumentSection.section_key == payload.section_key,
        )
        if payload.subject_id is None:
            query = query.where(AgentDocumentSection.subject_id.is_(None))
        else:
            query = query.where(AgentDocumentSection.subject_id == payload.subject_id)
        return await self._session.scalar(query)


def _selected_default_agents(raw: Any) -> tuple[str, ...]:
    selected: list[str]
    if isinstance(raw, list):
        selected = [str(item) for item in raw if str(item) in DEFAULT_AGENT_PACKAGES]
    elif isinstance(raw, dict):
        selected = [str(key) for key, enabled in raw.items() if enabled and str(key) in DEFAULT_AGENT_PACKAGES]
    else:
        selected = list(DEFAULT_AGENT_ORDER)

    # BI is the operating rail and command surface. Keep it available even when
    # early onboarding clients omit it.
    if "bi" not in selected:
        selected.append("bi")

    ordered = [key for key in DEFAULT_AGENT_ORDER if key in set(selected)]
    return tuple(ordered)


def _permission_mode_from_preferences(preferences: dict[str, Any]) -> str:
    raw = str(preferences.get("permission_mode") or "").strip()
    if raw in VALID_PERMISSION_MODES:
        return raw
    if preferences.get("safe_autopilot") is True:
        return "auto_approve"
    return "ask_always"


def _trust_mode_from_permission_mode(permission_mode: str) -> str:
    # Two trust states only: autopilot (run + send) or disabled (off). full_access
    # and auto_approve both mean "let the agent act" -> autopilot; everything else
    # (incl. the ask_always default) -> disabled.
    if permission_mode in {"full_access", "auto_approve"}:
        return "autopilot"
    return "disabled"


def _trigger_scope(trigger: TriggerSpec) -> dict[str, str]:
    scope = dict(trigger.matching_scope)
    if trigger.required_tool_scope:
        scope["required_tool_scope"] = trigger.required_tool_scope
    return scope


def _agent_runtime_text(
    *,
    permission_mode: str,
    brain_scopes: tuple[str, ...] | list[str],
    tool_scopes: tuple[str, ...] | list[str],
) -> str:
    brain = ", ".join(_brain_scope_label(scope) for scope in brain_scopes) or "hali tanlanmagan"
    external_permissions = (
        ", ".join(_tool_scope_label(scope) for scope in external_tool_scopes(tool_scopes)) or "hali berilmagan"
    )
    capabilities = (
        ", ".join(_tool_scope_label(scope) for scope in internal_capability_scopes(tool_scopes))
        or "ichki qobiliyat tanlanmagan"
    )
    return (
        f"Ish rejimi: {_permission_mode_label(permission_mode)}.\n"
        f"Tayanadigan bo'limlar: {brain}.\n"
        f"Ichki qobiliyatlar: {capabilities}.\n"
        f"Tashqi integratsiya ruxsatlari: {external_permissions}.\n"
        "Xavfli ishlar tasdiqsiz bajarilmaydi; har bir amal auditda saqlanadi."
    )


def _permission_mode_label(value: str) -> str:
    if value == "full_access":
        return "to'liq ruxsat"
    if value == "auto_approve":
        return "ishonchli past-xavf ishlarni avtomatik bajarish"
    return "har safar egadan tasdiq so'rash"


def _brain_scope_label(value: str) -> str:
    labels = {
        "catalog": "katalog",
        "knowledge": "bilim bazasi",
        "rules": "qoidalar",
        "voice": "ovoz uslubi",
        "examples": "suhbat namunalari",
        "sources": "manbalar",
        "issues": "muammolar",
        "conversation_state": "suhbat holati",
        "tasks": "vazifalar",
    }
    return labels.get(value, value.replace("_", " "))


def _tool_scope_label(value: str) -> str:
    labels = {
        "telegram.read_messages": "Telegram suhbatini o'qish",
        "telegram.send_message": "Telegram javob yuborish",
        "telegram.send_reaction": "Telegram reaksiyasi qo'yish",
        "telegram.edit_message": "yuborilgan javobni tahrirlash",
        "telegram.watch_channel": "Telegram kanalni kuzatish",
        "telegram.fetch_media": "Telegram media ochish",
        "telegram.sync_history": "suhbat tarixini yangilash",
        "brain.search": "Brain ichidan dalil qidirish",
        "conversation.get_context": "suhbat kontekstini ko'rish",
        "conversation.propose_reply": "javob taklif qilish",
        "action.create_proposal": "tasdiqlanadigan amal taklif qilish",
        "source.ingest": "manbani qayta o'qish",
        "catalog.search": "katalogdan qidirish",
        "catalog.propose_product_change": "katalog o'zgarishini taklif qilish",
        "task.propose": "vazifa taklif qilish",
    }
    return labels.get(value, value.replace(".", " ").replace("_", " "))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _source_priority_text(sources: dict[str, Any]) -> str:
    if not sources:
        return (
            "Hali faol manba qo'shilmagan. Manba qo'shilmaguncha egasi tasdiqlagan faktlar "
            "va yangi tasdiqlangan Brain obyektlariga tayaniladi."
        )
    return (
        "Avval egasi tasdiqlagan manbalarga tayaniladi. Yangi Telegram, sayt, fayl, "
        "rasm, audio va qo'lda kiritilgan manbalar eski yoki arxiv dalillardan ustun turadi. "
        "Konflikt bo'lsa, jim tanlanmaydi, egaga ko'rsatiladi."
    )


def _owner_rules_text(owner_rules: dict[str, Any]) -> str:
    if not owner_rules:
        return "Hali egaga xos qoida qo'shilmagan. Xavfli ishlar tasdiqlanadigan taklif bo'lib chiqadi."
    notes = _clean_text(owner_rules.get("notes"))
    if notes:
        return notes
    return "Onboarding vaqtida ega qoidalari berilgan. Ular yuqori ustuvor workspace siyosati sifatida ishlatiladi."


def _preferences_text(preferences: dict[str, Any]) -> str:
    if not preferences:
        return "Hali batafsil ishlash afzalliklari qo'shilmagan."
    lines: list[str] = []
    labels = {
        "reply_mode": "Javob rejimi",
        "safe_autopilot": "Past xavf ishlarni avtomatik bajarish",
        "escalation_destination": "Egaga o'tkazish joyi",
        "quiet_hours": "Tinch vaqt",
    }
    for key, label in labels.items():
        if key in preferences:
            lines.append(f"{label}: {preferences[key]}")
    return "\n".join(lines) if lines else "Afzalliklar workspace profilida saqlangan."
