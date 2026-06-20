"""Phase-1 draft of a custom agent: LLM drafts the behavior sections, the rest
is derived deterministically from the kind. Persists NOTHING — the wizard's
review/create step is the only write path.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.llm import generate_structured_json
from app.brain.llm_policy import CONTROL_CHAIN
from app.brain.prompt_payload import prompt_cache_payload_for_asset
from app.brain.prompt_registry import PromptAsset, get_prompt_registry
from app.modules.workspace_os.custom_agent import (
    AgentKind,
    WizardSectionDraft,
    _brain_scope_label,
    _permission_mode_label,
    _tool_scope_label,
    _tool_scope_text,
    _trigger_source_label,
    _trust_mode_from_permission_mode,
    derive_kind_defaults,
)

_KIND_LABEL = {
    "seller": "sotuvchi agent",
    "support": "qo'llab-quvvatlash agenti",
    "follow_up": "kuzatuv (follow-up) agenti",
    "custom": "maxsus agent",
}
_CUSTOM_AGENT_DRAFT_PROMPT_ID = "agent_documents.custom_agent_draft"
_CUSTOM_AGENT_DRAFT_PROMPT_VERSION = "1.0.0"

_FALLBACK_WHEN = (
    "Bu agent unga biriktirilgan trigger, BI buyrug'i yoki egadan kelgan topshiriq "
    "bo'lganda ishlaydi. Ishni boshlashdan oldin Brain dalillari, suhbat konteksti va "
    "ruxsat rejimini tekshiradi."
)
_FALLBACK_NEVER = (
    "Narx, ombor, to'lov, yetkazish muddati, shaxsiy ma'lumot, tibbiy/huquqiy da'vo, "
    "katalog o'chirish yoki biznes egasi roziligini taxmin qilmaydi."
)


class CustomAgentDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_kind: AgentKind = "custom"
    name: str = Field(min_length=2, max_length=120)
    # min_length 8 matches CustomAgentPackageInput.mission: the wizard sends the
    # drafted role body as `mission`, and on a degraded LLM the role body falls back
    # to does_what — so does_what must be long enough to satisfy mission's min.
    does_what: str = Field(min_length=8, max_length=600)
    when_replies: str = Field(default="", max_length=600)
    never_does: str = Field(default="", max_length=600)

    @field_validator("name", "does_what", "when_replies", "never_does", mode="before")
    @classmethod
    def _clean(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        return " ".join(value.split()).strip()


class _DraftedBehavior(BaseModel):
    """Schema-constrained LLM output: the three behavior section bodies."""

    role: str = ""
    when_to_act: str = ""
    never_guess: str = ""


@dataclass(frozen=True)
class CustomAgentDraftResult:
    agent_kind: str
    name: str
    sections: list[WizardSectionDraft]
    brain_scopes: list[str]
    tool_scopes: list[str]
    trigger_sources: list[str]
    permission_mode: str
    trust_mode: str


class CustomAgentDraftService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def draft(
        self, *, workspace_id: int, payload: CustomAgentDraftInput
    ) -> CustomAgentDraftResult:
        defaults = derive_kind_defaults(payload.agent_kind)
        behavior = await self._draft_behavior(workspace_id=workspace_id, payload=payload)

        # The role body becomes `mission` on create (min_length 8). Guard against a
        # too-short LLM body (empty OR tiny like "ok") by falling back to does_what,
        # which is itself validated >= 8 — so the role section is always >= 8 chars.
        role_body = behavior.role.strip()
        if len(role_body) < 8:
            role_body = payload.does_what
        when_body = behavior.when_to_act.strip() or (payload.when_replies or _FALLBACK_WHEN)
        never_body = behavior.never_guess.strip() or (payload.never_does or _FALLBACK_NEVER)

        brain_text = (
            f"Brain bo'limlari: "
            f"{', '.join(_brain_scope_label(s) for s in defaults['brain_scopes'])}. "
            "Dalil yetishmasa, javob yoki o'zgarishni taxmin qilmaydi; egaga savol, "
            "vazifa yoki tasdiqlanadigan taklif chiqaradi."
        )
        permissions_label = (
            ", ".join(_tool_scope_label(scope) for scope in defaults["tool_scopes"])
            or "hali berilmagan"
        )
        triggers_label = (
            ", ".join(_trigger_source_label(t) for t in defaults["trigger_sources"])
            or "faqat ega yoki BI buyrug'i bilan"
        )
        runtime_text = (
            f"Ish rejimi: {_permission_mode_label(defaults['permission_mode'])}.\n"
            f"Berilgan ruxsatlar: {permissions_label}.\n"
            f"Ishga tushishi: {triggers_label}.\n"
            "Xavfli ishlar tasdiqsiz bajarilmaydi; har bir amal auditda saqlanadi."
        )

        sections = [
            WizardSectionDraft(section_key="role", title="Rol", body=role_body, order_index=10),
            WizardSectionDraft(section_key="when_to_act", title="Qachon ishlaydi", body=when_body, order_index=11),
            WizardSectionDraft(section_key="brain_and_sources", title="Nimaga tayanadi", body=brain_text, order_index=12),
            WizardSectionDraft(section_key="tools", title="Integratsiya ruxsatlari", body=_tool_scope_text(defaults["tool_scopes"]), order_index=13),
            WizardSectionDraft(section_key="never_guess", title="Nimani taxmin qilmaydi", body=never_body, order_index=14),
            WizardSectionDraft(section_key="runtime_config", title="Ruxsatlar va chegaralar", body=runtime_text, order_index=15),
        ]

        return CustomAgentDraftResult(
            agent_kind=payload.agent_kind,
            name=payload.name,
            sections=sections,
            brain_scopes=defaults["brain_scopes"],
            tool_scopes=defaults["tool_scopes"],
            trigger_sources=defaults["trigger_sources"],
            permission_mode=defaults["permission_mode"],
            trust_mode=_trust_mode_from_permission_mode(defaults["permission_mode"]),
        )

    async def _draft_behavior(
        self, *, workspace_id: int, payload: CustomAgentDraftInput
    ) -> _DraftedBehavior:
        prompt = (
            f"Agent turi: {_KIND_LABEL.get(payload.agent_kind, 'maxsus agent')}.\n"
            f"Agent nomi: {payload.name}.\n"
            f"Nima qiladi: {payload.does_what}\n"
            f"Qachon javob beradi: {payload.when_replies or '(belgilanmagan)'}\n"
            f"Nimani qilmaydi: {payload.never_does or '(belgilanmagan)'}\n\n"
            "Quyidagi JSON maydonlarini to'ldiring: "
            "role (agent roli/vazifasi), when_to_act (qachon ishlashi), "
            "never_guess (nimani taxmin qilmasligi)."
        )
        raw = await generate_structured_json(
            chain=CONTROL_CHAIN,
            system=_custom_agent_draft_system_prompt(),
            prompt=prompt,
            response_schema=_DraftedBehavior,
            operation="custom_agent_draft",
            workspace_id=workspace_id,
            prompt_cache=_custom_agent_draft_prompt_cache(),
        )
        return _DraftedBehavior.model_validate(raw or {})


def _custom_agent_draft_system_prompt() -> str:
    return _custom_agent_draft_prompt_asset().body.strip()


def _custom_agent_draft_prompt_cache() -> dict | None:
    return prompt_cache_payload_for_asset(
        _custom_agent_draft_prompt_asset(),
        cache_scope="agent_documents.custom_agent_draft",
    )


@lru_cache(maxsize=1)
def _custom_agent_draft_prompt_asset() -> PromptAsset:
    return get_prompt_registry().load(
        _CUSTOM_AGENT_DRAFT_PROMPT_ID,
        version=_CUSTOM_AGENT_DRAFT_PROMPT_VERSION,
    )
