"""Provider-neutral CRM connector contracts.

Pure value types + the deterministic role ladder shared by the sync plane and
every ``CrmProvider`` adapter. NO I/O, NO new semantic detectors — the stage
ladder maps over the already-shipped conversation-state reducer facts
(``app/modules/agent_conversation_state/reducer.py``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.modules.agent_conversation_state.funnel import FunnelStage, funnel_stage

# --- the approved stage ladder -------------------------------------------------
# OQIM advances a lead FORWARD through new -> negotiation -> qualified; the human
# closes won/lost in the CRM (synced back in Slice 3). Terminal roles are part of
# the order (for comparison / webhook reconcile) but OQIM never *targets* them.
ROLE_ORDER: tuple[str, ...] = ("new", "negotiation", "qualified", "won", "lost")


def role_index(role: str) -> int:
    """Position in ``ROLE_ORDER``; ``-1`` for any unknown role."""
    try:
        return ROLE_ORDER.index(role)
    except ValueError:
        return -1


# Owner-facing Uzbek labels for CRM roles. These appear in the amoCRM notes the
# business reads (the platform is Uzbek-first); the raw role keys never surface.
ROLE_LABELS_UZ: dict[str, str] = {
    "new": "Yangi",
    "negotiation": "Muzokara",
    "qualified": "Malakali",
    "won": "Muvaffaqiyatli",
    "lost": "Yopilgan",
}


def crm_role_label(role: str) -> str:
    """The owner-facing Uzbek label for a CRM role (falls back to the raw role)."""
    return ROLE_LABELS_UZ.get(role, role)


_CRM_ROLE = {
    FunnelStage.NEW: "new",
    FunnelStage.ENGAGED: "new",
    FunnelStage.QUALIFIED: "negotiation",
    FunnelStage.LEAD_CAPTURED: "negotiation",
    FunnelStage.HANDED_OFF: "qualified",
}


def crm_role(stage: FunnelStage) -> str:
    """The amoCRM pipeline role for a funnel stage (won/lost stay human-owned)."""
    return _CRM_ROLE[stage]


def target_role_for_facts(facts: dict) -> str:
    """Deterministic CRM role over the blessed reducer facts. Delegates to the
    canonical funnel (qualified<-handoff; negotiation<-buying|contact; else new).
    Never returns ``won``/``lost`` — those are human-owned; the caller enforces
    monotonicity (only advance when the target outranks the current role). (#426)"""
    return crm_role(funnel_stage(facts))


# --- OAuth / token value types -------------------------------------------------
@dataclass(frozen=True)
class CrmTokens:
    access_token: str
    refresh_token: str
    expires_at: datetime


@dataclass(frozen=True)
class CrmOAuthCallback:
    """Opaque callback payload. ``raw_params`` carries provider-specific bits
    (amoCRM's account host arrives only in the ``referer`` query param) so the
    seam never leaks a provider detail into the route signature."""

    code: str
    raw_params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CrmConnectionBootstrap:
    tokens: CrmTokens
    provider_account_ref: str


# --- write inputs --------------------------------------------------------------
@dataclass(frozen=True)
class CrmContactInput:
    name: str
    phone: str | None
    channel_label: str  # e.g. "telegram_dm:@user"


@dataclass(frozen=True)
class CrmLeadInput:
    name: str
    pipeline_id: str
    stage_id: str
    contact_id: str


# --- pipeline / lead reads -----------------------------------------------------
@dataclass(frozen=True)
class CrmPipelineStatus:
    stage_id: str
    name: str
    sort: int
    kind: str  # "active" | "won" | "lost" | "unsorted"


@dataclass(frozen=True)
class CrmPipeline:
    pipeline_id: str
    name: str
    is_main: bool
    statuses: list[CrmPipelineStatus]


@dataclass(frozen=True)
class CrmFieldEnum:
    enum_id: str
    value: str


@dataclass(frozen=True)
class CrmFieldDef:
    key_id: str | None        # provider field id as string (amoCRM custom_fields[].id)
    code: str | None          # stable field_code when present ("PHONE", "price", ...)
    name: str
    type: str                 # raw discovered type; neutral coercion is S4
    enums: tuple[CrmFieldEnum, ...] = ()


@dataclass(frozen=True)
class CrmUser:
    user_id: str
    name: str


@dataclass(frozen=True)
class CrmTaskType:
    task_type_id: str
    name: str


@dataclass(frozen=True)
class CrmAccountSchema:
    """Raw provider account-schema snapshot read on connect (the *discover* side
    of the configurable layer). S0 populates ``pipelines``; custom fields, users,
    and task types are added in S2."""

    pipelines: list[CrmPipeline]
    custom_fields: dict[str, list[CrmFieldDef]] = field(default_factory=dict)  # entity -> fields
    users: list[CrmUser] = field(default_factory=list)
    task_types: list[CrmTaskType] = field(default_factory=list)


@dataclass(frozen=True)
class CrmLeadSnapshot:
    lead_id: str
    stage_id: str
    value: int | None
    notes: list[str]
    custom_fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CrmContactSnapshot:
    contact_id: str
    name: str
    phone: str | None


@dataclass(frozen=True)
class CrmStageEvent:
    kind: str  # "status_lead" | "responsible_lead" | "note_lead" | "update_lead"
    lead_id: str
    status_id: str | None = None
    value: int | None = None  # the lead price amoCRM carries on status/update events
    author_id: int | None = None  # the user who made the change; 0 = OQIM/API, None = unknown


@dataclass(frozen=True)
class CrmWebhookBatch:
    account_subdomain: str
    events: list[CrmStageEvent]


# --- typed errors (raised by providers, handled by routes/workers) -------------
class CrmAuthError(Exception):
    """OAuth exchange/refresh failed unrecoverably (bad code, invalid_grant,
    missing subdomain). Routes turn this into the ``?amocrm=error`` redirect;
    the refresher marks the connection degraded."""


class CrmUnauthorizedError(Exception):
    """A 401 from an API call. The worker refreshes + retries once; the provider
    itself never silently retries."""
