"""Contracts for Brain document generation (BUSINESS.md and later AGENT/SKILL)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class BusinessSectionSpec:
    key: str
    title: str
    guidance: str


BUSINESS_SECTIONS: tuple[BusinessSectionSpec, ...] = (
    BusinessSectionSpec("overview", "Biznes haqida", "Short description of the business: what it is, where, since when."),
    BusinessSectionSpec("what_we_sell", "Biz nimani sotamiz", "Product/service categories and lines."),
    BusinessSectionSpec("catalog_sku_rules", "Katalog va SKU qoidalari", "How catalog items and SKUs are identified and structured."),
    BusinessSectionSpec("voice_style", "Ovoz va javob uslubi", "Tone and reply style (high level; detailed voice lives in SKILL.md)."),
    BusinessSectionSpec("price_payment_policy", "Narx, chegirma va to'lov siyosati", "Price ranges, discounts, payment methods and plans."),
    BusinessSectionSpec("delivery_promises", "Yetkazib berish va va'dalar", "Delivery, meetings, and promises the business makes."),
    BusinessSectionSpec("followup_policy", "Kuzatuv siyosati", "When and how to follow up with customers."),
    BusinessSectionSpec("do_not_guess", "Taxmin qilinmaydigan narsalar", "Hard rules: what the agent must never invent (medical claims, unverified payment, stock without checking)."),
    BusinessSectionSpec("source_priority", "Manba ustuvorligi", "Which source wins when sources conflict."),
    BusinessSectionSpec("missing_data_behavior", "Ma'lumot yetishmasa", "What to do when information is missing."),
)


class BusinessSectionDraft(BaseModel):
    section_key: str = Field(description="One of the BUSINESS_SECTIONS keys")
    body: str = Field(description="The markdown body for this section, in Uzbek")
    evidence_refs: list[str] = Field(default_factory=list, description="fact ids or source refs supporting this section")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class BusinessDocumentDraft(BaseModel):
    sections: list[BusinessSectionDraft] = Field(default_factory=list)


@dataclass(frozen=True)
class AgentSectionSpec:
    key: str
    title: str
    guidance: str


AGENT_SECTIONS: tuple[AgentSectionSpec, ...] = (
    AgentSectionSpec("role_mission", "Rol va vazifa", "Who this agent is and the single mission it serves for this business."),
    AgentSectionSpec("capabilities", "Imkoniyatlar", "What the agent can do — derived strictly from its enabled tools. Never list a capability it has no tool for."),
    AgentSectionSpec("behavior_rules", "Xulq-atvor qoidalari", "How the agent should behave and reply, consistent with BUSINESS.md voice/policy. References applicable skills."),
    AgentSectionSpec("approval_rules", "Tasdiqlash qoidalari", "What the agent may do autonomously vs what needs owner approval (sends, catalog/price changes, payment confirmations)."),
    AgentSectionSpec("examples", "Misollar", "A few short example interactions in the business's language."),
    AgentSectionSpec("must_never", "Hech qachon qilmaslik kerak", "Hard prohibitions for this agent (invent stock/price, confirm unverified payment, make medical/legal claims)."),
)


class AgentSectionDraft(BaseModel):
    section_key: str = Field(description="One of the AGENT_SECTIONS keys")
    body: str = Field(description="The markdown body for this section, in the business's language")
    evidence_refs: list[str] = Field(default_factory=list, description="business section keys or fact ids supporting this section")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AgentDocumentDraft(BaseModel):
    sections: list[AgentSectionDraft] = Field(default_factory=list)


class DistilledItem(BaseModel):
    index: int = Field(description="0-based index of the pair within the distill batch")
    summary: str = Field(description="One sentence: what the owner did in response to what the customer said")
    dimension: str = Field(default="general", description="price/delivery/stock/payment/greeting/objection/followup/general")


class DistilledBatch(BaseModel):
    items: list[DistilledItem] = Field(default_factory=list)


class SynthesizedSkill(BaseModel):
    slug: str = Field(description="kebab-case skill id, e.g. price-handling")
    name: str = Field(description="Short human title")
    trigger: str = Field(description="When this skill fires (the customer behavior)")
    action: str = Field(description="How the owner should respond")
    example_phrase: str = Field(default="", description="A representative owner reply in the business's language")
    dimension: str = Field(default="general")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class LearnedSkill(SynthesizedSkill):
    evidence_conv_ids: list[int] = Field(default_factory=list)


class SkillLearnReport(BaseModel):
    pairs_used: int = 0
    clusters: int = 0
    candidates: int = 0
