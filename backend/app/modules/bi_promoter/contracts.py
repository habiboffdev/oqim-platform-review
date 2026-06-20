from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.commercial_spine.contracts import CommercialActionProposal


class BIPromoterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


BIQuestionKind = Literal[
    "pipeline_summary",
    "attention_queue",
    "source_freshness",
    "who_bought_what",
    "hot_customers",
    "stalled_opportunities",
    "product_channel_breakdown",
]


class BIInsightRequest(BIPromoterModel):
    schema_version: Literal["bi_insight_request.v1"] = "bi_insight_request.v1"
    workspace_id: int = Field(gt=0)
    question_kind: BIQuestionKind
    source_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(min_length=1)


class BIInsight(BIPromoterModel):
    schema_version: Literal["bi_insight.v1"] = "bi_insight.v1"
    workspace_id: int = Field(gt=0)
    insight_id: str = Field(min_length=1)
    insight_type: BIQuestionKind
    answer: str = Field(min_length=1)
    metrics: dict[str, Any] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    freshness: Literal["projection_current", "projection_partial", "degraded"]
    suggested_action_proposal_ids: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)


class BIAnalyticsDashboard(BIPromoterModel):
    schema_version: Literal["bi_analytics_dashboard.v1"] = (
        "bi_analytics_dashboard.v1"
    )
    workspace_id: int = Field(gt=0)
    summary: dict[str, Any] = Field(default_factory=dict)
    breakdowns: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    insights: list[BIInsight] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    freshness: Literal["projection_current", "projection_partial", "degraded"]
    degraded_reasons: list[str] = Field(default_factory=list)


class BIInvestigationRequest(BIPromoterModel):
    schema_version: Literal["bi_investigation_request.v1"] = (
        "bi_investigation_request.v1"
    )
    workspace_id: int = Field(gt=0)
    investigation_ref: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    limit: int = Field(default=100, ge=1, le=250)


class BIInvestigationFinding(BIPromoterModel):
    schema_version: Literal["bi_investigation_finding.v1"] = (
        "bi_investigation_finding.v1"
    )
    finding_ref: str = Field(min_length=1)
    finding_type: Literal[
        "attention_queue",
        "stalled_opportunity",
        "data_quality",
        "policy_block",
        "source_freshness",
    ]
    severity: Literal["low", "medium", "high"]
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_action: str | None = Field(default=None, min_length=1)


class BIInvestigationFixCandidate(BIPromoterModel):
    schema_version: Literal["bi_investigation_fix_candidate.v1"] = (
        "bi_investigation_fix_candidate.v1"
    )
    target_ref: str = Field(min_length=1)
    proposal_type: Literal[
        "business_brain_update_candidate",
        "customer_state_fix_candidate",
        "commercial_action_proposal_candidate",
    ]
    proposed_value: dict[str, Any]
    evidence_refs: list[str] = Field(min_length=1)
    risk_tier: Literal["low", "medium", "high", "critical"]
    approval_state: Literal["proposed"] = "proposed"


class BIInvestigationResult(BIPromoterModel):
    schema_version: Literal["bi_investigation_result.v1"] = (
        "bi_investigation_result.v1"
    )
    workspace_id: int = Field(gt=0)
    investigation_ref: str = Field(min_length=1)
    status: Literal["ready", "degraded"]
    findings: list[BIInvestigationFinding] = Field(default_factory=list)
    fix_candidates: list[BIInvestigationFixCandidate] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    freshness: Literal["projection_current", "projection_partial", "degraded"]
    degraded_reasons: list[str] = Field(default_factory=list)
    llm_trace_ids: list[str] = Field(default_factory=list)


BICommandKind = Literal["create_agent", "create_owner_task", "create_reply_action"]
BICommandPermissionMode = Literal["ask_always", "auto_approve", "full_access"]
BICommandTaskKind = Literal[
    "business",
    "meeting",
    "delivery",
    "stock",
    "call",
    "payment",
    "follow_up",
]


class BICommandInput(BIPromoterModel):
    schema_version: Literal["bi_command_input.v1"] = "bi_command_input.v1"
    workspace_id: int = Field(gt=0)
    command_kind: BICommandKind
    command_text: str = Field(min_length=8, max_length=2000)
    agent_name: str | None = Field(default=None, min_length=2, max_length=120)
    permission_mode: BICommandPermissionMode = "ask_always"
    brain_scopes: list[str] = Field(
        default_factory=lambda: ["knowledge", "rules", "voice", "examples"]
    )
    tool_scopes: list[str] = Field(default_factory=lambda: ["telegram.read_messages"])
    trigger_sources: list[str] = Field(default_factory=list)
    task_title: str | None = Field(default=None, min_length=2, max_length=160)
    task_detail: str | None = Field(default=None, min_length=2, max_length=1000)
    task_kind: BICommandTaskKind | None = None
    due_at: str | None = Field(default=None, min_length=1, max_length=80)
    customer_label: str | None = Field(default=None, min_length=1, max_length=160)
    conversation_id: int | None = Field(default=None, ge=0)
    customer_id: int | None = Field(default=None, ge=0)
    reply_text: str | None = Field(default=None, min_length=1, max_length=2000)
    source_proposal_id: str | None = Field(default=None, min_length=1, max_length=200)
    correlation_id: str = Field(default="ui:bi_command", min_length=1)

    @field_validator(
        "command_text",
        "agent_name",
        "task_title",
        "task_detail",
        "due_at",
        "customer_label",
        "source_proposal_id",
        "correlation_id",
    )
    @classmethod
    def _clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()).strip()

    @field_validator("reply_text")
    @classmethod
    def _clean_reply_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @model_validator(mode="after")
    def _validate_command_shape(self) -> BICommandInput:
        if self.command_kind == "create_agent" and not self.agent_name:
            raise ValueError("create_agent requires agent_name")
        if self.command_kind == "create_owner_task":
            if not self.task_title:
                raise ValueError("create_owner_task requires task_title")
            if not self.task_detail:
                raise ValueError("create_owner_task requires task_detail")
            if self.task_kind is None:
                raise ValueError("create_owner_task requires task_kind")
        if self.command_kind == "create_reply_action":
            if (self.conversation_id or 0) <= 0:
                raise ValueError("create_reply_action requires conversation_id")
            if (self.customer_id or 0) <= 0:
                raise ValueError("create_reply_action requires customer_id")
            if not self.reply_text:
                raise ValueError("create_reply_action requires reply_text")
        return self


class BICommandResult(BIPromoterModel):
    schema_version: Literal["bi_command_result.v1"] = "bi_command_result.v1"
    workspace_id: int = Field(gt=0)
    command_kind: BICommandKind
    status: Literal["proposal_created", "proposal_reused"]
    message_uz: str = Field(min_length=1)
    proposal: CommercialActionProposal
    action_route: Literal["/actions"] = "/actions"
    source_refs: list[str] = Field(min_length=1)


class PromoterPolicyInput(BIPromoterModel):
    schema_version: Literal["promoter_policy_input.v1"] = (
        "promoter_policy_input.v1"
    )
    workspace_id: int = Field(gt=0)
    enabled: bool = False
    approved: bool = False
    allowed_stages: list[str] = Field(default_factory=list)
    max_contacts_per_7d: int = Field(default=1, ge=0, le=20)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    correlation_id: str = Field(default="promoter_policy", min_length=1)


class PromoterPolicy(BIPromoterModel):
    schema_version: Literal["promoter_policy.v1"] = "promoter_policy.v1"
    workspace_id: int = Field(gt=0)
    enabled: bool
    approved: bool
    allowed_stages: list[str] = Field(default_factory=list)
    max_contacts_per_7d: int = Field(ge=0, le=20)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class PromoterCandidateInput(BIPromoterModel):
    schema_version: Literal["promoter_candidate_input.v1"] = (
        "promoter_candidate_input.v1"
    )
    customer_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    stage: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
    opt_out: bool = False
    contact_count_7d: int = Field(default=0, ge=0)
    customer_ref: str | None = Field(default=None, min_length=1)


class PromoterCampaignInput(BIPromoterModel):
    schema_version: Literal["promoter_campaign_input.v1"] = (
        "promoter_campaign_input.v1"
    )
    workspace_id: int = Field(gt=0)
    campaign_ref: str = Field(min_length=1)
    approval_state: Literal["proposed", "approved", "rejected"]
    message_goal: str = Field(min_length=1)
    offer_refs: list[str] = Field(default_factory=list)
    candidates: list[PromoterCandidateInput] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class PromoterProjectionCampaignInput(BIPromoterModel):
    schema_version: Literal["promoter_projection_campaign_input.v1"] = (
        "promoter_projection_campaign_input.v1"
    )
    workspace_id: int = Field(gt=0)
    campaign_ref: str = Field(min_length=1)
    approval_state: Literal["proposed", "approved", "rejected"]
    message_goal: str = Field(min_length=1)
    offer_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    max_candidates: int = Field(default=50, ge=1, le=100)


class PromoterCandidateDecision(BIPromoterModel):
    schema_version: Literal["promoter_candidate_decision.v1"] = (
        "promoter_candidate_decision.v1"
    )
    customer_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    stage: str = Field(min_length=1)
    status: Literal["proposed", "skipped"]
    reason_code: str = Field(min_length=1)
    proposal_id: str | None = Field(default=None, min_length=1)
    source_refs: list[str] = Field(min_length=1)


class PromoterCampaignPlan(BIPromoterModel):
    schema_version: Literal["promoter_campaign_plan.v1"] = (
        "promoter_campaign_plan.v1"
    )
    workspace_id: int = Field(gt=0)
    campaign_ref: str = Field(min_length=1)
    status: Literal["planned", "blocked"]
    blocked_reasons: list[str] = Field(default_factory=list)
    decisions: list[PromoterCandidateDecision] = Field(default_factory=list)
    proposals: list[CommercialActionProposal] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Outreach promoter: pure value types + helpers (NO I/O)
# ---------------------------------------------------------------------------

# One source of truth for pacing defaults; a campaign stores only overrides.
PROMOTER_DEFAULT_CAPS: dict = {
    "cold_daily": 25,          # cold sends per campaign per day (warm is uncapped)
    "hours": [9, 19],          # working hours [start, end), local tz
    "tz": "Asia/Tashkent",
    "jitter_s": [180, 600],    # random gap between sends, seconds
    "active_window_h": 72,     # a dialog with activity in this window is "live" — never DM over it
}

TIERS: tuple[str, ...] = ("warm", "cold")
TARGET_STATES: tuple[str, ...] = (
    "pending", "sending", "sent", "replied", "skipped", "failed",
)


def effective_caps(overrides: dict | None) -> dict:
    """Merge a campaign's caps overrides over the defaults (non-mutating)."""
    merged = dict(PROMOTER_DEFAULT_CAPS)
    if overrides:
        merged.update({k: v for k, v in overrides.items() if v is not None})
    return merged


def within_working_hours(caps: dict, now_utc: datetime) -> bool:
    """True when ``now_utc`` falls inside the campaign's local working hours [start, end)."""
    tz = ZoneInfo(str(caps.get("tz") or PROMOTER_DEFAULT_CAPS["tz"]))
    hours = caps.get("hours") or []
    if len(hours) < 2:
        hours = PROMOTER_DEFAULT_CAPS["hours"]
    start, end = hours[0], hours[1]
    return int(start) <= now_utc.astimezone(tz).hour < int(end)


@dataclass(frozen=True)
class SegmentSpec:
    """What audience to enroll. Resolved against the account's pipeline."""
    stage_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    pipeline_id: str = ""  # required when stage_ids is non-empty (amoCRM filter DSL)
