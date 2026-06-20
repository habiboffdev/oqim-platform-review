from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.modules.agent_documents.contracts import (
    AgentDocumentSectionInput,
    AgentSkillInput,
)
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.commercial_spine.contracts import CommercialActionProposal, CommercialEvent
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.telegram_tools.contracts import (
    TELEGRAM_EDIT_MESSAGE,
    TELEGRAM_FETCH_MEDIA,
    TELEGRAM_READ_MESSAGES,
    TELEGRAM_SEND_MESSAGE,
    TELEGRAM_SYNC_HISTORY,
    TELEGRAM_TOOL_SCOPES,
    TELEGRAM_WATCH_CHANNEL,
)
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.modules.triggers.contracts import TriggerInput
from app.modules.triggers.service import TriggerService

PermissionMode = Literal["ask_always", "auto_approve", "full_access"]
AgentKind = Literal["seller", "support", "follow_up", "custom"]

VALID_BRAIN_SCOPES = frozenset(
    {
        "catalog",
        "knowledge",
        "rules",
        "voice",
        "examples",
        "sources",
        "conversation_state",
        "tasks",
        "issues",
    }
)
VALID_TRIGGER_SOURCES = frozenset({"channel_message_received"})
DEFAULT_BRAIN_SCOPES = ("knowledge", "rules", "voice", "examples")
DEFAULT_TOOL_SCOPES = (TELEGRAM_READ_MESSAGES,)

KIND_DEFAULTS: dict[str, dict[str, Any]] = {
    "seller": {
        "brain_scopes": ["catalog", "knowledge", "rules", "voice", "examples"],
        "tool_scopes": [TELEGRAM_READ_MESSAGES, TELEGRAM_SEND_MESSAGE, TELEGRAM_FETCH_MEDIA],
        "trigger_sources": ["channel_message_received"],
        "permission_mode": "auto_approve",
    },
    "support": {
        "brain_scopes": ["knowledge", "rules", "voice", "examples"],
        "tool_scopes": [TELEGRAM_READ_MESSAGES, TELEGRAM_SEND_MESSAGE],
        "trigger_sources": ["channel_message_received"],
        "permission_mode": "auto_approve",
    },
    "follow_up": {
        "brain_scopes": ["knowledge", "rules", "voice", "examples"],
        "tool_scopes": [TELEGRAM_READ_MESSAGES, TELEGRAM_SEND_MESSAGE],
        "trigger_sources": [],
        "permission_mode": "ask_always",
    },
    "custom": {
        "brain_scopes": list(DEFAULT_BRAIN_SCOPES),
        "tool_scopes": [TELEGRAM_READ_MESSAGES],
        "trigger_sources": [],
        "permission_mode": "ask_always",
    },
}


def derive_kind_defaults(agent_kind: str) -> dict[str, Any]:
    """Default brain/tool/trigger scopes + permission mode for an agent kind.

    Unknown kinds fall back to the conservative custom defaults.
    """
    base = KIND_DEFAULTS.get(agent_kind, KIND_DEFAULTS["custom"])
    return {
        "brain_scopes": list(base["brain_scopes"]),
        "tool_scopes": list(base["tool_scopes"]),
        "trigger_sources": list(base["trigger_sources"]),
        "permission_mode": base["permission_mode"],
    }


class WizardSectionDraft(BaseModel):
    """One AGENT.md section flowing through the wizard (draft -> review -> create).

    Named distinctly from brain.contracts.AgentSectionDraft (a different model with
    evidence_refs/confidence) to avoid cross-module name confusion.
    """

    model_config = ConfigDict(extra="forbid")

    section_key: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4000)
    order_index: int = Field(default=10, ge=0, le=999)

    # mode="before" so whitespace-only input is collapsed BEFORE min_length checks,
    # otherwise "   " passes min_length=1 and then cleans to "".
    @field_validator("section_key", "title", "body", mode="before")
    @classmethod
    def _clean(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        return " ".join(value.split()).strip()


class CustomAgentPackageInput(BaseModel):
    """Owner-facing request for a full custom agent package.

    This contract creates the runtime package around the agent: readable
    AGENT.md sections, enforced config, starter skill, scoped tool grants, and
    optional triggers. It deliberately avoids free-form provider/tool strings
    from the UI so custom agents scale without silently gaining unsafe tools.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    mission: str = Field(min_length=8, max_length=2000)
    agent_kind: AgentKind = "custom"
    sections: list[WizardSectionDraft] | None = None
    permission_mode: PermissionMode = "ask_always"
    brain_scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_BRAIN_SCOPES))
    tool_scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_TOOL_SCOPES))
    trigger_sources: list[str] = Field(default_factory=list)
    starter_skill_name: str | None = Field(default=None, max_length=120)
    starter_skill_instructions: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, max_length=160)

    @field_validator("name", "mission", "starter_skill_name", "starter_skill_instructions")
    @classmethod
    def _clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()).strip()

    @field_validator("brain_scopes")
    @classmethod
    def _valid_brain_scopes(cls, value: list[str]) -> list[str]:
        normalized = _dedupe([str(item).strip() for item in value if str(item).strip()])
        unknown = [item for item in normalized if item not in VALID_BRAIN_SCOPES]
        if unknown:
            raise ValueError(f"unknown brain scopes: {', '.join(unknown)}")
        return normalized or list(DEFAULT_BRAIN_SCOPES)

    @field_validator("tool_scopes")
    @classmethod
    def _valid_tool_scopes(cls, value: list[str]) -> list[str]:
        normalized = _dedupe([str(item).strip() for item in value if str(item).strip()])
        unknown = [item for item in normalized if item not in TELEGRAM_TOOL_SCOPES]
        if unknown:
            raise ValueError(f"unknown Telegram tool scopes: {', '.join(unknown)}")
        return normalized

    @field_validator("trigger_sources")
    @classmethod
    def _valid_trigger_sources(cls, value: list[str]) -> list[str]:
        normalized = _dedupe([str(item).strip() for item in value if str(item).strip()])
        unknown = [item for item in normalized if item not in VALID_TRIGGER_SOURCES]
        if unknown:
            raise ValueError(f"unknown trigger sources: {', '.join(unknown)}")
        return normalized

    @model_validator(mode="after")
    def _triggers_need_read_grant(self) -> CustomAgentPackageInput:
        if "channel_message_received" in self.trigger_sources and TELEGRAM_READ_MESSAGES not in self.tool_scopes:
            self.tool_scopes = [TELEGRAM_READ_MESSAGES, *self.tool_scopes]
        return self


@dataclass(frozen=True)
class CustomAgentPackageResult:
    agent: Agent
    created: bool
    package_key: str
    permission_mode: str
    document_section_count: int
    skill_count: int
    tool_grant_count: int
    trigger_count: int


@dataclass(frozen=True)
class CustomAgentPackageProposalResult:
    proposal: CommercialActionProposal
    created: bool
    package_key: str
    permission_mode: str


class CustomAgentPackageService:
    """Create custom agent packages using the same OS primitives as defaults."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._documents = AgentDocumentService(session)
        self._grants = ToolGrantService(session)
        self._triggers = TriggerService(session)
        self._events = CommercialSpineRepository(session)

    async def propose(
        self,
        *,
        workspace_id: int,
        payload: CustomAgentPackageInput,
        actor_ref: str = "owner",
        correlation_id: str | None = None,
    ) -> CustomAgentPackageProposalResult:
        normalized = _normalized_payload(payload)
        idem = payload.idempotency_key or _digest_json(normalized)
        package_key = _package_key(name=payload.name, idempotency_key=idem)
        proposal_id = f"agent-package-proposal:{idem}"
        existing = await self._events.get_action_proposal(
            workspace_id=workspace_id,
            proposal_id=proposal_id,
        )
        if existing is not None:
            return CustomAgentPackageProposalResult(
                proposal=existing,
                created=False,
                package_key=package_key,
                permission_mode=payload.permission_mode,
            )

        proposed_payload = payload.model_dump(mode="json")
        risk_level = _custom_agent_risk(payload)
        proposal = CommercialActionProposal(
            proposal_id=proposal_id,
            workspace_id=workspace_id,
            conversation_id=0,
            customer_id=0,
            action_type="agent.create_custom_package",
            lifecycle_state="waiting_approval",
            execution_mode="suggest_only",
            risk_level=risk_level,
            requires_approval=True,
            executor_runtime="workspace_os",
            priority="high" if risk_level in {"high", "critical"} else "medium",
            confidence=0.92,
            reason_code="custom_agent_requires_owner_approval",
            source_refs=[f"agent_package_request:{idem}"],
            payload={
                "title": f"{payload.name} agentini yaratish",
                "summary": "Yangi agent faqat tasdiqdan keyin yaratiladi.",
                "customer_name": "Workspace sozlamasi",
                "custom_agent_package": proposed_payload,
                "package_key": package_key,
                "actor_ref": actor_ref,
                "risk_notes": _custom_agent_risk_notes(payload),
            },
            idempotency_key=f"agent-package-proposal:{idem}",
            correlation_id=correlation_id or f"agent-package-proposal:{idem}",
            trace_id=f"trace:agent-package-proposal:{idem}",
        )
        created = await self._events.persist_action_proposal(proposal)
        if created:
            await self._events.append_event(
                CommercialEvent(
                    event_id=f"event:{proposal.proposal_id}:created",
                    workspace_id=workspace_id,
                    source_type="agent_package_proposal",
                    source_ref=proposal.proposal_id,
                    actor_type=actor_ref,
                    correlation_id=proposal.correlation_id or proposal.proposal_id,
                    idempotency_key=f"event:{proposal.idempotency_key}",
                    payload={
                        "proposal_id": proposal.proposal_id,
                        "action_type": proposal.action_type,
                        "agent_name": payload.name,
                        "permission_mode": payload.permission_mode,
                        "tool_scopes": list(payload.tool_scopes),
                        "trigger_sources": list(payload.trigger_sources),
                    },
                )
            )
        return CustomAgentPackageProposalResult(
            proposal=proposal,
            created=created,
            package_key=package_key,
            permission_mode=payload.permission_mode,
        )

    async def create(
        self,
        *,
        workspace_id: int,
        payload: CustomAgentPackageInput,
    ) -> CustomAgentPackageResult:
        normalized = _normalized_payload(payload)
        idem = payload.idempotency_key or _digest_json(normalized)
        existing = await self._find_by_idempotency(workspace_id=workspace_id, idempotency_key=idem)
        created = existing is None

        if existing is None:
            package_key = _package_key(name=payload.name, idempotency_key=idem)
            agent = Agent(
                workspace_id=workspace_id,
                name=payload.name,
                is_default=False,
                is_active=True,
                persona={
                    "schema_version": "agent_package.v1",
                    "package_key": package_key,
                    "package_version": "custom.2026-05-17",
                    "role": payload.name,
                    "mission": payload.mission,
                    "custom": True,
                    "creation_idempotency_key": idem,
                },
                instructions=payload.mission,
                example_responses=[],
                knowledge_config=_knowledge_config(payload.brain_scopes),
                channel_config={
                    "mode": "workspace_events",
                    "trigger_sources": list(payload.trigger_sources),
                },
                tools_config={
                    "tool_scopes": list(payload.tool_scopes),
                    "permission_mode": payload.permission_mode,
                    "custom": True,
                },
                trust_mode=_trust_mode_from_permission_mode(payload.permission_mode),
                auto_send_threshold=0.9 if payload.permission_mode != "ask_always" else 0.0,
                escalation_topics=[],
                agent_type=payload.agent_kind,
                contact_scope="business",
            )
            self._session.add(agent)
            await self._session.flush()
        else:
            agent = existing
            package_key = str((agent.persona or {}).get("package_key") or "custom")

        document_sections = await self._upsert_agent_sections(
            workspace_id=workspace_id,
            agent=agent,
            payload=payload,
            package_key=package_key,
        )
        skill_count, skill_sections = await self._upsert_starter_skill(
            workspace_id=workspace_id,
            agent=agent,
            payload=payload,
            package_key=package_key,
        )
        tool_grants = await self._upsert_tool_grants(
            workspace_id=workspace_id,
            agent=agent,
            payload=payload,
            package_key=package_key,
        )
        triggers = await self._upsert_triggers(
            workspace_id=workspace_id,
            agent=agent,
            payload=payload,
            package_key=package_key,
        )
        await self._audit_created(
            workspace_id=workspace_id,
            agent=agent,
            payload=payload,
            package_key=package_key,
            idempotency_key=idem,
            created=created,
        )

        return CustomAgentPackageResult(
            agent=agent,
            created=created,
            package_key=package_key,
            permission_mode=payload.permission_mode,
            document_section_count=document_sections + skill_sections,
            skill_count=skill_count,
            tool_grant_count=tool_grants,
            trigger_count=triggers,
        )

    async def _find_by_idempotency(
        self, *, workspace_id: int, idempotency_key: str
    ) -> Agent | None:
        result = await self._session.scalars(
            select(Agent).where(Agent.workspace_id == workspace_id).order_by(Agent.id.asc())
        )
        for agent in result.all():
            persona = agent.persona or {}
            if persona.get("creation_idempotency_key") == idempotency_key:
                return agent
        return None

    async def _upsert_agent_sections(
        self,
        *,
        workspace_id: int,
        agent: Agent,
        payload: CustomAgentPackageInput,
        package_key: str,
    ) -> int:
        # Section order is derived from list position by the enumerate() loop below;
        # WizardSectionDraft.order_index is not used here (callers send sections in order).
        if payload.sections:
            sections = tuple(
                (item.section_key, item.title, item.body)
                for item in payload.sections
            )
        else:
            sections = (
                (
                    "role",
                    "Rol",
                    payload.mission,
                ),
                (
                    "when_to_act",
                    "Qachon ishlaydi",
                    "Bu agent unga biriktirilgan trigger, BI buyrug'i yoki egadan kelgan topshiriq bo'lganda ishlaydi. Ishni boshlashdan oldin Brain dalillari, suhbat konteksti va ruxsat rejimini tekshiradi.",
                ),
                (
                    "brain_and_sources",
                    "Nimaga tayanadi",
                    f"Brain bo'limlari: {', '.join(_brain_scope_label(scope) for scope in payload.brain_scopes)}. Dalil yetishmasa, javob yoki o'zgarishni taxmin qilmaydi; egaga savol, vazifa yoki tasdiqlanadigan taklif chiqaradi.",
                ),
                (
                    "tools",
                    "Integratsiya ruxsatlari",
                    _tool_scope_text(payload.tool_scopes),
                ),
                (
                    "never_guess",
                    "Nimani taxmin qilmaydi",
                    "Narx, ombor, to'lov, yetkazish muddati, shaxsiy ma'lumot, tibbiy/huquqiy da'vo, katalog o'chirish yoki biznes egasi roziligini taxmin qilmaydi.",
                ),
                (
                    "runtime_config",
                    "Ruxsatlar va chegaralar",
                    _runtime_config_text(payload),
                ),
            )
        for order, (key, title, body) in enumerate(sections, start=10):
            await self._documents.upsert_section(
                workspace_id=workspace_id,
                payload=AgentDocumentSectionInput(
                    document_kind="agent",
                    subject_type="agent",
                    subject_id=agent.id,
                    section_key=key,
                    title=title,
                    body=body,
                    order_index=order,
                    source_evidence=[{"source_ref": f"agent_package:{package_key}"}],
                    generated_by="owner",
                ),
            )
        return len(sections)

    async def _upsert_starter_skill(
        self,
        *,
        workspace_id: int,
        agent: Agent,
        payload: CustomAgentPackageInput,
        package_key: str,
    ) -> tuple[int, int]:
        slug = f"custom-agent-{agent.id}-core"
        name = payload.starter_skill_name or f"{payload.name} asosiy ko'nikmasi"
        instructions = payload.starter_skill_instructions or payload.mission
        skill = await self._documents.upsert_skill(
            workspace_id=workspace_id,
            payload=AgentSkillInput(
                slug=slug,
                name=name,
                description=f"{payload.name} uchun boshlang'ich ish ko'nikmasi.",
                instructions=instructions,
                when_to_use="Agent vazifasi shu ko'nikmaga mos kelganda va dalil yetarli bo'lganda.",
                when_not_to_use="Dalil yoki ruxsat yetishmasa; xavfli o'zgarishlar tasdiqsiz bajarilmaydi.",
                tools=["brain.search", *payload.tool_scopes],
                agent_id=agent.id,
                enabled=True,
            ),
        )
        skill_sections = (
            ("purpose", "Maqsad", f"{payload.name} agentiga asosiy ish qobiliyatini beradi."),
            ("instructions", "Ko'rsatma", instructions),
            ("use", "Qachon ishlatiladi", "BI buyrug'i, trigger yoki ega topshirig'i shu agent vazifasiga mos kelganda."),
            ("avoid", "Qachon ishlatilmaydi", "Dalilsiz javob, tasdiqsiz yuborish yoki ruxsatsiz integratsiya yozuvi kerak bo'lganda."),
        )
        for order, (key, title, body) in enumerate(skill_sections, start=10):
            await self._documents.upsert_section(
                workspace_id=workspace_id,
                payload=AgentDocumentSectionInput(
                    document_kind="skill",
                    subject_type="skill",
                    subject_id=skill.id,
                    section_key=key,
                    title=title,
                    body=body,
                    order_index=order,
                    source_evidence=[{"source_ref": f"agent_package:{package_key}:skill:core"}],
                    generated_by="owner",
                ),
            )
        return 1, len(skill_sections)

    async def _upsert_tool_grants(
        self,
        *,
        workspace_id: int,
        agent: Agent,
        payload: CustomAgentPackageInput,
        package_key: str,
    ) -> int:
        count = 0
        for scope in payload.tool_scopes:
            await self._grants.grant(
                workspace_id=workspace_id,
                payload=ToolGrantInput(
                    agent_id=agent.id,
                    scope=scope,
                    granted_by="owner",
                    grant_reason=f"{payload.name} agenti uchun tanlangan Telegram ruxsati.",
                    audit_metadata={
                        "package_key": package_key,
                        "package_version": "custom.2026-05-17",
                    },
                ),
            )
            count += 1
        return count

    async def _upsert_triggers(
        self,
        *,
        workspace_id: int,
        agent: Agent,
        payload: CustomAgentPackageInput,
        package_key: str,
    ) -> int:
        count = 0
        for source in payload.trigger_sources:
            if source == "channel_message_received":
                await self._triggers.create(
                    workspace_id=workspace_id,
                    payload=TriggerInput(
                        owner_agent_id=agent.id,
                        event_source=source,
                        action_proposal_type="agent.custom_propose_action",
                        matching_scope={
                            "agent_route": "custom",
                            "custom_agent_id": str(agent.id),
                            "package_key": package_key,
                            "required_tool_scope": TELEGRAM_READ_MESSAGES,
                        },
                        permission_mode=payload.permission_mode,
                        notes=f"{payload.name} yangi Telegram xabarlari bo'yicha ish taklif qiladi.",
                    ),
                )
                count += 1
        return count

    async def _audit_created(
        self,
        *,
        workspace_id: int,
        agent: Agent,
        payload: CustomAgentPackageInput,
        package_key: str,
        idempotency_key: str,
        created: bool,
    ) -> None:
        await self._events.append_event(
            CommercialEvent(
                event_id=f"agent-package:{agent.id}:created",
                workspace_id=workspace_id,
                source_type="agent_package_runtime",
                source_ref=f"agent:{agent.id}",
                actor_type="owner",
                correlation_id=f"agent-package:{agent.id}",
                idempotency_key=f"agent-package:{idempotency_key}",
                payload={
                    "agent_id": agent.id,
                    "agent_name": payload.name,
                    "package_key": package_key,
                    "created": created,
                    "permission_mode": payload.permission_mode,
                    "brain_scopes": list(payload.brain_scopes),
                    "tool_scopes": list(payload.tool_scopes),
                    "trigger_sources": list(payload.trigger_sources),
                },
            )
        )


def _normalized_payload(payload: CustomAgentPackageInput) -> dict[str, Any]:
    return {
        "name": payload.name,
        "mission": payload.mission,
        "agent_kind": payload.agent_kind,
        "permission_mode": payload.permission_mode,
        "brain_scopes": sorted(payload.brain_scopes),
        "tool_scopes": sorted(payload.tool_scopes),
        "trigger_sources": sorted(payload.trigger_sources),
        "starter_skill_name": payload.starter_skill_name or "",
        "starter_skill_instructions": payload.starter_skill_instructions or "",
    }


def _digest_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _package_key(*, name: str, idempotency_key: str) -> str:
    return f"custom:{_slug(name)}:{idempotency_key[:8]}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:48] or "agent"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _knowledge_config(brain_scopes: list[str]) -> dict[str, Any]:
    return {
        "brain_scopes": list(brain_scopes),
        "use_catalog": "catalog" in brain_scopes,
        "use_knowledge": "knowledge" in brain_scopes,
        "retrieval_core": True,
    }


def _trust_mode_from_permission_mode(permission_mode: str) -> str:
    # Two trust states only: "autopilot" (run + send) or "disabled" (off). Automation
    # choices (full_access / auto_approve) map to autopilot; everything else is
    # disabled. A wizard-created agent stays off until the owner opts into autopilot.
    if permission_mode in {"full_access", "auto_approve"}:
        return "autopilot"
    return "disabled"


def _tool_scope_text(tool_scopes: list[str]) -> str:
    if not tool_scopes:
        return "Bu agentga hozircha tashqi integratsiya ruxsati berilmagan. BI yoki ega keyin ruxsat qo'shishi mumkin."
    return (
        "Bu agent quyidagi Telegram ruxsatlari orqali ishlaydi: "
        f"{', '.join(_tool_scope_label(scope) for scope in tool_scopes)}. "
        "Telegram akkaunt kaliti agentga berilmaydi; har bir ish policy, qayta yubormaslik himoyasi va auditdan o'tadi."
    )


def _runtime_config_text(payload: CustomAgentPackageInput) -> str:
    brain = ", ".join(_brain_scope_label(scope) for scope in payload.brain_scopes) or "hali tanlanmagan"
    permissions = ", ".join(_tool_scope_label(scope) for scope in payload.tool_scopes) or "hali berilmagan"
    triggers = ", ".join(_trigger_source_label(item) for item in payload.trigger_sources) or "faqat ega yoki BI buyrug'i bilan"
    return (
        f"Ish rejimi: {_permission_mode_label(payload.permission_mode)}.\n"
        f"Tayanadigan bo'limlar: {brain}.\n"
        f"Berilgan ruxsatlar: {permissions}.\n"
        f"Ishga tushishi: {triggers}.\n"
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
        TELEGRAM_READ_MESSAGES: "suhbatni o'qish",
        TELEGRAM_SEND_MESSAGE: "javob yuborish",
        TELEGRAM_EDIT_MESSAGE: "yuborilgan javobni tahrirlash",
        TELEGRAM_WATCH_CHANNEL: "kanalni kuzatish",
        TELEGRAM_FETCH_MEDIA: "media ochish",
        TELEGRAM_SYNC_HISTORY: "suhbat tarixini yangilash",
    }
    return labels.get(value, value.replace(".", " ").replace("_", " "))


def _custom_agent_risk(payload: CustomAgentPackageInput) -> str:
    if payload.permission_mode == "full_access":
        return "high"
    if TELEGRAM_SEND_MESSAGE in payload.tool_scopes:
        return "high"
    if payload.trigger_sources:
        return "medium"
    return "medium"


def _custom_agent_risk_notes(payload: CustomAgentPackageInput) -> list[str]:
    notes: list[str] = []
    if payload.permission_mode == "full_access":
        notes.append("To'liq ruxsat yuqori xavfli: egadan tasdiq talab qilinadi.")
    if TELEGRAM_SEND_MESSAGE in payload.tool_scopes:
        notes.append("Telegramga javob yuborish tashqi yozuv hisoblanadi.")
    if payload.trigger_sources:
        notes.append("Trigger yoqilsa agent yangi voqealarda ish taklif qiladi.")
    return notes or ["Yangi agent workspace xatti-harakatini o'zgartiradi."]


def _trigger_source_label(value: str) -> str:
    if value == "channel_message_received":
        return "yangi Telegram xabari kelganda"
    return value.replace("_", " ")
