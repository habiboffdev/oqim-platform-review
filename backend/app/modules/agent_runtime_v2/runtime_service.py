"""Agent Runtime service (P5a S4): compose config, context, and runtime output.

End-to-end reply decision for one customer turn: load the agent's send policy,
gather conversation history (and the agent's kind) from the SHARED
`AgentRuntimeContextService`, generate a reply + confidence, then return a
draft/proposal outcome. On the interactive hot path grounding is NOT pre-fetched
— the agent retrieves catalog / KB / rules / voice on demand via the
`knowledge_*` tools (action/setup modes opt back into eager grounding via the
profile). The channel pipeline is the single place that may schedule visible
auto-send.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.models.commercial_action import CommercialActionProposalRecord
from app.models.conversation import Conversation
from app.models.crm_connection import CrmConnection
from app.modules.agent_business_actions.service import handoff_kind_from_refs
from app.modules.agent_conversation_state.service import AgentConversationStateService
from app.modules.agent_memory.seller_adapter import (
    render_authority_lines,
    render_style_lines,
    render_warning_codes,
)
from app.modules.agent_memory.service import AgentMemoryService
from app.modules.agent_runtime_context.contracts import AgentRuntimeContextRequest
from app.modules.agent_runtime_context.service import AgentRuntimeContextService
from app.modules.agent_runtime_v2.budget import BudgetService
from app.modules.agent_runtime_v2.confidence import score_confidence
from app.modules.agent_runtime_v2.config_loader import AgentConfig, AgentConfigLoader
from app.modules.agent_runtime_v2.faithfulness import FaithfulnessVerdict, judge_faithfulness
from app.modules.agent_runtime_v2.finalization_guard import finalize_customer_visible_reply
from app.modules.agent_runtime_v2.grounding import format_agent_grounding
from app.modules.agent_runtime_v2.hermes.engine import HermesEngineAdapter
from app.modules.agent_runtime_v2.reply_runtime import SendAction
from app.modules.agent_runtime_v2.runtime_profile import (
    RuntimeProfile,
    RuntimeProfileCompiler,
)
from app.modules.agent_talking.contracts import TalkBundle
from app.modules.catalog_authority.contracts import (
    CatalogAuthorityBundle,
    CatalogAuthorityMedia,
    CatalogAuthorityOffer,
    CatalogAuthorityProduct,
)
from app.modules.catalog_authority.service import CatalogAuthorityService
from app.modules.crm_connector.lead_links import active_lead_link, crm_stage_label
from app.modules.crm_connector.stage_map import resolve_pipeline_view

logger = logging.getLogger(__name__)

_ROLE_LABEL = {"customer": "Customer", "seller": "Seller"}
_DEFAULT_AGENT_KIND = "custom_agent"
_INTERACTIVE_AGENT_RECENT_MESSAGE_LIMIT = 12
_INTERACTIVE_AGENT_TRANSCRIPT_EVENT_LIMIT = 8
_TRUTH_FAMILY_TYPES = {
    "catalog_product",
    "catalog_variant",
    "catalog_offer",
    "catalog_media",
    "knowledge_fact",
    "seller_rule_fact",
    "business_source_media_fact",
    "business_source_fact",
}


@dataclass(frozen=True)
class AgentRuntimeOutcome:
    action: SendAction
    reply_text: str
    confidence: float
    agent_id: int
    reason: str
    talk_bundle: TalkBundle | None = None
    agent_actions: list[dict[str, Any]] = field(default_factory=list)
    turn_details: dict[str, Any] | None = None
    telemetry: dict[str, Any] | None = None
    tool_errors: int = 0
    committed_action_refs: list[str] = field(default_factory=list)
    intelligence_payloads: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _GatheredContext:
    grounding: list[str]
    history: list[str]
    agent_kind: str
    voice_examples: list[str]
    authority_warnings: list[str]
    session_summary: str = ""
    transcript_hits: list[str] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    authority_bundle: CatalogAuthorityBundle | None = None
    runtime_profile: RuntimeProfile | None = None
    static_context: dict[str, Any] = field(default_factory=dict)
    has_media_context: bool = False


@dataclass(frozen=True)
class _AgentTurnContext:
    """Everything the LLM reply phase needs, gathered from the DB up front so the
    slow Hermes loop can run with NO database session held open."""

    config: AgentConfig
    gathered: _GatheredContext
    agent_id: int
    customer_message: str
    customer_query_text: str | None = None
    conversation_id: int | None = None
    hermes_run_id: str | None = None
    reply_to_message_ref: str | None = None
    turn_session_id: int | None = None
    turn_revision_start: int | None = None
    agent_session_id: int | None = None
    hermes_session_id: str | None = None
    session_db: Any | None = None
    budget_exceeded: bool = False
    # Behavioral: the latest compact conversation state the reply loop reads.
    conversation_state: dict[str, Any] = field(default_factory=dict)
    # Telemetry-only: an inline dict serialized into hermes_runs.details. NOT a
    # typed carrier — nothing reads it for behavior (see #412).
    runtime_context_packet: dict[str, Any] | None = None
    # The CURRENT turn's media (TurnMediaPart list) staged by the dispatcher,
    # forwarded to engine.run -> ToolContext -> the Gemini boundary. Never replayed.
    current_turn_media: list = field(default_factory=list)
    # Bare live-call rendering of this turn's media (e.g. "[Voice message]"),
    # forwarded to engine.run -> ToolContext -> the boundary swap. None for
    # text-only turns -> zero overhead.
    live_media_text: str | None = None


class AgentRuntimeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run_turn(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        customer_message: str,
        customer_query_text: str | None = None,
        conversation_id: int | None = None,
        hermes_run_id: str | None = None,
        reply_to_message_ref: str | None = None,
        turn_session_id: int | None = None,
        turn_revision_start: int | None = None,
        agent_session_id: int | None = None,
        hermes_session_id: str | None = None,
        session_db: Any | None = None,
    ) -> AgentRuntimeOutcome:
        """One-shot reply decision. Convenience for callers that don't manage the
        connection budget (evals, tests). Latency-sensitive callers (the reply
        bridge) instead call gather_turn_context, commit to release the
        connection, then run_from_context — so the slow loop pins nothing.

        NOTE: this convenience path does NOT thread inbound media
        (``current_turn_media`` / ``live_media_text``); only the dispatcher (the
        real production caller) stages media. Add both here if an eval/test ever
        needs to exercise native media perception through ``run_turn``."""
        ctx = await self.gather_turn_context(
            workspace_id=workspace_id,
            agent_id=agent_id,
            customer_message=customer_message,
            customer_query_text=customer_query_text,
            conversation_id=conversation_id,
            hermes_run_id=hermes_run_id,
            reply_to_message_ref=reply_to_message_ref,
            turn_session_id=turn_session_id,
            turn_revision_start=turn_revision_start,
            agent_session_id=agent_session_id,
            hermes_session_id=hermes_session_id,
            session_db=session_db,
        )
        return await self.run_from_context(ctx)

    async def gather_turn_context(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        customer_message: str,
        customer_query_text: str | None = None,
        conversation_id: int | None = None,
        hermes_run_id: str | None = None,
        reply_to_message_ref: str | None = None,
        turn_session_id: int | None = None,
        turn_revision_start: int | None = None,
        agent_session_id: int | None = None,
        hermes_session_id: str | None = None,
        session_db: Any | None = None,
        current_turn_media: list | None = None,
        live_media_text: str | None = None,
    ) -> _AgentTurnContext:
        """DB phase: load the agent config and ground the turn. Read-only, so the
        caller can commit/close immediately after to release the connection
        before the slow LLM loop."""
        config = await AgentConfigLoader(self.session).load(
            workspace_id=workspace_id, agent_id=agent_id
        )
        budget_exceeded = await BudgetService(self.session).is_exhausted(
            workspace_id=workspace_id
        )
        # Compile from the real agent kind (config.agent_kind) BEFORE retrieval so the
        # profile is correct even on degraded/empty-query turns (the old post-retrieval
        # compile fell back to custom_agent when context build returned the default).
        profile = RuntimeProfileCompiler().compile_agent(
            config=config,
            agent_kind=config.agent_kind,
        )
        query_text = (customer_query_text or customer_message or "").strip()
        conversation_state: dict[str, Any] = {}
        if agent_session_id is not None:
            conversation_state = await AgentConversationStateService(
                self.session
            ).latest_compact_state(
                workspace_id=workspace_id,
                agent_session_id=agent_session_id,
            )
        handoffs = await _open_handoff_state(
            self.session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )
        if handoffs:
            conversation_state = {**conversation_state, "handoffs": handoffs}
        try:
            crm_state = await _load_crm_state(
                self.session,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
            )
        except Exception:
            logger.warning("crm_state injection failed", exc_info=True)
            crm_state = None
        if crm_state:
            conversation_state = {**conversation_state, "crm": crm_state}
        gathered = await self._gather_context(
            workspace_id=workspace_id,
            agent_id=agent_id,
            customer_message=query_text,
            conversation_id=conversation_id,
            agent_session_id=agent_session_id,
            hermes_session_id=hermes_session_id,
            enable_eager_grounding=profile.retrieval_policy.enable_eager_grounding,
        )
        bundle = None
        if profile.retrieval_policy.enable_eager_grounding:
            # Compute the authority query only when eager grounding is on — lazy
            # (route-first) turns never use it, so don't pay for it (#415).
            authority_query = _authority_query_text(
                customer_query_text=query_text,
                conversation_state=conversation_state,
                session_summary=gathered.session_summary,
            )
            if authority_query:
                try:
                    bundle = await CatalogAuthorityService(self.session).resolve(
                        workspace_id=workspace_id, query=authority_query
                    )
                except Exception:
                    bundle = None
        if bundle is not None:
            gathered = _with_scoped_catalog_authority(gathered, bundle)
        gathered = replace(
            gathered,
            authority_bundle=bundle,
            runtime_profile=profile,
            agent_kind=config.agent_kind,
        )
        agent_material_refs = [
            f"agent:{agent_id}:AGENT.md",
            "prompt:agent_runtime.hermes_reply:1.0.0",
        ]
        static_context = _static_context_payload(
            gathered.static_context,
            profile_hash=profile.profile_hash,
            agent_material_refs=agent_material_refs,
            tool_grants=profile.allowed_tool_names,
        )
        dynamic_transcript_hits = (
            []
            if conversation_state
            else list(gathered.transcript_hits or gathered.history)
        )
        dynamic_context = _dynamic_context_payload(
            customer_turn_text=customer_message,
            customer_query_text=query_text,
            session_summary=gathered.session_summary,
            transcript_hits=dynamic_transcript_hits,
            conversation_state=conversation_state,
            authority_lines=gathered.grounding,
            style_lines=gathered.voice_examples,
            policy_warnings=gathered.authority_warnings,
        )
        # Telemetry-only payload (serialized into hermes_runs.details). conversation_state
        # is carried as a first-class context field, not here — the dispatcher blob only
        # needs the derived conversation_state_chars (inside dynamic_context).
        runtime_context_packet = {
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "agent_session_id": agent_session_id,
            "hermes_session_id": hermes_session_id,
            "customer_turn_text": customer_message,
            "customer_query_text": query_text,
            "session_summary": gathered.session_summary,
            "transcript_hits": dynamic_transcript_hits,
            "authority_lines": list(gathered.grounding),
            "style_lines": list(gathered.voice_examples),
            "policy_warnings": list(gathered.authority_warnings),
            "agent_material_refs": agent_material_refs,
            "tool_grants": list(profile.allowed_tool_names),
            "cache_keys": list(static_context["cache_keys"]),
            "static_context": static_context,
            "dynamic_context": dynamic_context,
            "telemetry": dict(gathered.telemetry),
        }
        return _AgentTurnContext(
            config=config,
            gathered=gathered,
            agent_id=agent_id,
            customer_message=customer_message,
            customer_query_text=query_text,
            conversation_id=conversation_id,
            hermes_run_id=hermes_run_id,
            reply_to_message_ref=reply_to_message_ref,
            turn_session_id=turn_session_id,
            turn_revision_start=turn_revision_start,
            agent_session_id=agent_session_id,
            hermes_session_id=hermes_session_id,
            session_db=session_db,
            budget_exceeded=budget_exceeded,
            conversation_state=conversation_state,
            runtime_context_packet=runtime_context_packet,
            current_turn_media=current_turn_media or [],
            live_media_text=live_media_text,
        )

    async def run_from_context(self, ctx: _AgentTurnContext) -> AgentRuntimeOutcome:
        """LLM phase: run the grounded reply loop + send decision. Touches NO
        database — the caller MUST release/commit the gather transaction before
        calling this, so the multi-second Hermes loop never pins a connection
        idle-in-transaction."""
        if ctx.budget_exceeded:
            # Fail fast: a capped workspace must not enter the Hermes loop (the
            # generic loop may retry provider errors -> ~8-29s burn + a
            # confusing "API call failed" draft). Terminal PROPOSE, empty text.
            return AgentRuntimeOutcome(
                action=SendAction.PROPOSE,
                reply_text="",
                confidence=0.0,
                agent_id=ctx.agent_id,
                reason="budget_exceeded: workspace daily token cap reached",
            )
        # gather_turn_context always sets runtime_profile (see the replace() there),
        # so the old post-gather compile_agent fallback was dead code (#415).
        profile = ctx.gathered.runtime_profile
        assert profile is not None, "gather_turn_context must set runtime_profile"
        result = await HermesEngineAdapter().run(
            config=ctx.config,
            profile=profile,
            customer_message=ctx.customer_message,
            grounding=ctx.gathered.grounding,
            history=ctx.gathered.history,
            voice_examples=ctx.gathered.voice_examples,
            authority_warnings=ctx.gathered.authority_warnings,
            conversation_id=ctx.conversation_id,
            hermes_run_id=ctx.hermes_run_id,
            reply_to_message_ref=ctx.reply_to_message_ref,
            turn_session_id=ctx.turn_session_id,
            turn_revision_start=ctx.turn_revision_start,
            agent_kind=ctx.gathered.agent_kind,
            hermes_session_id=ctx.hermes_session_id,
            session_db=ctx.session_db,
            agent_session_id=ctx.agent_session_id,
            conversation_state=ctx.conversation_state,
            current_turn_media=ctx.current_turn_media,
            live_media_text=ctx.live_media_text,
        )
        authority = ctx.gathered.authority_bundle
        verdict = FaithfulnessVerdict(claims=[])
        unsupported = 0
        faithfulness_mode = "deferred_critic"
        if (
            profile.action_policy.faithfulness_required
            and result.reply_text
        ):
            # Semantic verifier: the LLM judge decides which claims are authority
            # claims. Deterministic code only consumes the structured verdict.
            faithfulness_authority = _authority_with_tool_lines(
                authority
                if authority is not None
                else CatalogAuthorityBundle(query=ctx.customer_query_text or ctx.customer_message or ""),
                result.tool_authority_lines,
            )
            verdict = await judge_faithfulness(
                reply_text=result.reply_text,
                authority=faithfulness_authority,
                workspace_id=ctx.config.workspace_id,
            )
            unsupported = verdict.unsupported_authority_claims
            faithfulness_mode = "hot_path_judge"
        finalization = finalize_customer_visible_reply(
            reply_text=result.reply_text,
            faithfulness=verdict,
            committed_action_refs=list(result.committed_action_refs or []),
        )
        reply_text = finalization.customer_visible_text
        talk_bundle = result.talk_bundle
        if finalization.blocked:
            talk_bundle = None
        confidence = score_confidence(
            grounding_hits=result.grounding_hits,
            tool_errors=result.tool_errors,
            authority_warnings=result.authority_warnings,
            unsupported_authority_claims=unsupported,
        )
        action = SendAction.PROPOSE
        reason = "pending_send_policy"
        if talk_bundle is not None:
            talk_bundle.confidence = confidence
        bundle_size = 0
        warning_count = 0
        missing_field_count = 0
        if authority is not None:
            bundle_size = len(authority.products) + len(authority.offers)
            warning_count = len(authority.warnings)
            missing_field_count = len(authority.missing_fields)
        telemetry = build_turn_telemetry(
            profile_hash=profile.profile_hash,
            profile_kind=profile.profile_kind,
            execution_mode=profile.execution_mode,
            allowed_tool_names=profile.allowed_tool_names,
            authority_bundle_size=bundle_size,
            warning_count=warning_count,
            missing_field_count=missing_field_count,
            unsupported_authority_claims=unsupported,
            faithfulness_mode=faithfulness_mode,
            confidence=confidence,
            decision=action.value,
            context_efficiency=_context_efficiency_payload(ctx.runtime_context_packet),
            finalization=finalization.telemetry,
        )
        return AgentRuntimeOutcome(
            action=action,
            reply_text=reply_text,
            confidence=confidence,
            agent_id=ctx.agent_id,
            reason=reason,
            talk_bundle=talk_bundle,
            agent_actions=list(result.agent_actions or []),
            turn_details=result.turn_details,
            telemetry=telemetry,
            tool_errors=result.tool_errors,
            committed_action_refs=list(result.committed_action_refs or []),
            intelligence_payloads=list(result.intelligence_payloads or []),
        )

    async def _gather_context(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        customer_message: str,
        conversation_id: int | None,
        agent_session_id: int | None = None,
        hermes_session_id: str | None = None,
        enable_eager_grounding: bool = False,
    ) -> _GatheredContext:
        query = (customer_message or "").strip()
        if not query:
            return _GatheredContext(
                grounding=[],
                history=[],
                voice_examples=[],
                authority_warnings=[],
                agent_kind=_DEFAULT_AGENT_KIND,
                telemetry=_empty_turn_context_telemetry(),
            )
        started_at = time.perf_counter()
        try:
            context = await AgentRuntimeContextService(self.session).build(
                AgentRuntimeContextRequest(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    agent_session_id=agent_session_id,
                    hermes_session_id=hermes_session_id,
                    query_text=query,
                    recent_message_limit=_INTERACTIVE_AGENT_RECENT_MESSAGE_LIMIT,
                    transcript_event_limit=_INTERACTIVE_AGENT_TRANSCRIPT_EVENT_LIMIT,
                    # Grounding is skipped on the interactive hot path by default
                    # (enable_eager_grounding=False for interactive turns); the
                    # agent retrieves on demand via the knowledge_* tools. action/
                    # setup modes opt back in via the profile.
                    include_grounding=enable_eager_grounding,
                    enable_contextual_rank=False,
                    enable_query_rewrite=False,
                    enable_agentic_search=False,
                    enable_rerank=False,
                )
            )
        except Exception:
            # Best-effort context: a miss/failure yields no evidence, so the
            # runtime lowers confidence and escalates rather than crashing.
            return _GatheredContext(
                grounding=[],
                history=[],
                voice_examples=[],
                authority_warnings=[],
                agent_kind=_DEFAULT_AGENT_KIND,
                telemetry=_empty_turn_context_telemetry(
                    latency_ms=_elapsed_ms(started_at),
                    degraded_reason="agent_runtime_context_unavailable",
                ),
            )
        recent_messages = getattr(context, "recent_messages", None)
        # Conversation continuity is owned by the Hermes session (host-resume);
        # history here only feeds memory-lane rendering, never the turn prompt.
        history = _format_history(recent_messages, exclude=query)
        transcript_hits = list(getattr(context, "transcript_hits", []) or [])
        session_summary = str(getattr(context, "session_summary", "") or "")
        has_media_context = _has_media_context(recent_messages)
        memory = AgentMemoryService(self.session).assemble_turn_memory(
            context,
            history=history,
        )
        truth_evidence = render_authority_lines(memory) or _legacy_truth_evidence(
            context.grounding
        )
        voice_examples = render_style_lines(memory)
        authority_warnings = render_warning_codes(memory.warnings)
        return _GatheredContext(
            grounding=truth_evidence,
            history=history,
            session_summary=session_summary,
            transcript_hits=transcript_hits,
            voice_examples=voice_examples,
            authority_warnings=authority_warnings,
            agent_kind=getattr(context, "agent_kind", _DEFAULT_AGENT_KIND) or _DEFAULT_AGENT_KIND,
            has_media_context=has_media_context,
            telemetry=_turn_context_telemetry(
                context_telemetry=getattr(context, "telemetry", {}) or {},
                truth_evidence=truth_evidence,
                history=history,
                voice_examples=voice_examples,
                authority_warnings=authority_warnings,
                latency_ms=_elapsed_ms(started_at),
            ),
            static_context=_context_static_payload(context),
        )


def _legacy_truth_evidence(grounding: Any) -> list[str]:
    """Keep old/simple grounding candidates useful without promoting voice facts.

    Some tests and thin retrieval responses only have contextual_text on catalog
    candidates. The structured seller lane builder may skip those if no product
    ref exists, so this formats only truth-family candidates.
    """
    families = getattr(grounding, "families", None)
    if not isinstance(families, dict):
        return []
    truth_families = {
        key: value for key, value in families.items() if key in _TRUTH_FAMILY_TYPES
    }
    return format_agent_grounding(SimpleNamespace(families=truth_families))


# Spec 2026-06-11 honest-seller-loop: a queued handoff older than this reads
# as "stale" to the agent. Deliberately a code constant for the pilot; promote
# to settings when owners need to tune it without a deploy.
_HANDOFF_STALE_MINUTES = 60


async def _open_handoff_state(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation_id: int | None,
) -> list[dict[str, Any]]:
    """True recorded status of this conversation's handoffs, for
    conversation_state. The agent speaks escalation status FROM this — it can
    no longer invent 'they will call you urgently' (live failure 2026-06-10)."""
    if conversation_id is None:
        return []
    rows = (
        (
            await session.execute(
                select(CommercialActionProposalRecord)
                .where(
                    CommercialActionProposalRecord.workspace_id == workspace_id,
                    CommercialActionProposalRecord.conversation_id == conversation_id,
                    CommercialActionProposalRecord.action_type == "create_business_task",
                    CommercialActionProposalRecord.lifecycle_state.in_(
                        ("proposed", "approved")
                    ),
                )
                .order_by(CommercialActionProposalRecord.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    now = utc_now()
    out: list[dict[str, Any]] = []
    for row in rows:
        kind = handoff_kind_from_refs(list(row.source_refs or []))
        if kind is None:
            continue
        age_minutes = max(0, int((now - row.created_at).total_seconds() // 60))
        state = "acknowledged" if row.lifecycle_state == "approved" else "queued"
        out.append(
            {
                "kind": kind,
                "state": state,
                "age_minutes": age_minutes,
                "stale": state == "queued" and age_minutes >= _HANDOFF_STALE_MINUTES,
            }
        )
    return out


async def _load_crm_state(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation_id: int | None,
) -> dict[str, Any] | None:
    """Local CRM lead-link state for conversation_state["crm"] — DB-only, free.

    Carries the stage role/label, the human-touch authority flag (the warm-lead
    signal the prompt reads), the deal value, and the lead ref. ``None`` when
    there is no active connection or no link. NEVER does network I/O (spec §5.1).
    The deal_value pre-inject replaces the retired `crm.context` tool round-trip:
    the model gets the latest stage + deal value for free, no LLM call."""
    if conversation_id is None:
        return None
    link = await active_lead_link(
        session, workspace_id=workspace_id, conversation_id=conversation_id
    )
    if link is None:
        return None
    role = link.synced_stage_role or link.desired_stage_role
    state: dict[str, Any] = {"stage_role": role, "stage_authority": link.stage_authority}
    conv = await session.get(Conversation, link.conversation_id)
    if conv is not None and conv.deal_value is not None:
        # conversation_state is JSON-bound (json.dumps'd in the dynamic-context byte
        # estimate + persisted as snapshots), so it must hold a JSON-native number,
        # never a raw Decimal — a Decimal here raised TypeError in gather_turn_context
        # and quarantined the turn (no reply, live 2026-06-15). deal_value is a
        # whole-so'm price.
        state["deal_value"] = int(conv.deal_value)
    if link.provider_lead_id:
        connection = await session.get(CrmConnection, link.connection_id)
        config = connection.pipeline_config if connection else {}
        view = resolve_pipeline_view(config, link.pipeline_id)
        stage_id = (
            link.last_observed_stage_id
            or link.last_synced_stage_id
            or ((view["stage_map"]).get(role) or {}).get("stage_id")
        )
        label = crm_stage_label(config, stage_id, pipeline_id=link.pipeline_id)
        if label:
            state["stage_label"] = label
        provider = connection.provider if connection else "amocrm"
        state["lead_ref"] = f"{provider}:lead:{link.provider_lead_id}"
    # S4 SEE: a DB-only capability menu of owner-blessed inject:true fields (key,
    # label, type, enum LABELS) — no current values (that needs network).
    from app.models.agent import Agent
    from app.models.agent_session import AgentSession
    from app.modules.agent_runtime_v2.config_loader import resolve_crm_fields

    agent_id = await session.scalar(
        select(AgentSession.agent_id)
        .where(AgentSession.conversation_id == link.conversation_id)
        .limit(1)
    )
    if agent_id is not None:
        agent = await session.get(Agent, agent_id)
        fields_cfg = (
            resolve_crm_fields(getattr(agent, "channel_config", None) or {})
            if agent
            else None
        )
        if fields_cfg:
            menu = []
            for key, fc in fields_cfg.items():
                if fc.get("inject") is not True:
                    continue
                item = {
                    "key": key,
                    "label": fc.get("label") or key,
                    "type": fc.get("type") or "text",
                }
                if fc.get("enum_map"):
                    item["enums"] = list(fc["enum_map"].keys())
                menu.append(item)
            if menu:
                state["fields"] = menu
    return state


_CATALOG_TRUTH_PREFIXES = (
    "[CATALOG]",
    "[MAHSULOT]",
    "[VARIANT]",
    "[TAKLIF]",
    "[MAHSULOT MEDIA]",
    "[PRODUCT]",
    "[OFFER]",
    "[MEDIA]",
    "[MISSING]",
)


def _with_scoped_catalog_authority(
    gathered: _GatheredContext,
    bundle: CatalogAuthorityBundle,
) -> _GatheredContext:
    authority_lines = _catalog_authority_prompt_lines(bundle)
    non_catalog_lines = [
        line
        for line in gathered.grounding
        if not _is_catalog_truth_line(line)
    ]
    grounding = [*authority_lines, *non_catalog_lines]
    warnings = [*gathered.authority_warnings, *_catalog_warning_codes(bundle)]
    telemetry = _telemetry_with_scoped_catalog_authority(
        gathered.telemetry,
        truth_evidence_count=len(grounding),
        catalog_authority_line_count=len(authority_lines),
    )
    return replace(
        gathered,
        grounding=grounding,
        authority_warnings=list(dict.fromkeys(warnings)),
        telemetry=telemetry,
    )


def _catalog_authority_prompt_lines(bundle: CatalogAuthorityBundle) -> list[str]:
    lines = list(bundle.approved_authority_lines())
    missing = set(bundle.missing_fields)
    if "price" in missing:
        offered_titles = {
            _normalize_title(offer.product_title)
            for offer in bundle.offers
            if offer.authority_state == "approved" and offer.product_title
        }
        for product in bundle.products:
            if product.authority_state != "approved":
                continue
            if _normalize_title(product.title) in offered_titles:
                continue
            lines.append(
                f"[MISSING] {product.title}: approved price/offer is not available"
            )
    return list(dict.fromkeys(line for line in lines if line.strip()))


def _authority_query_text(
    *,
    customer_query_text: str,
    conversation_state: dict[str, Any],
    session_summary: str,
) -> str:
    parts = [(customer_query_text or "").strip()]
    state = conversation_state or {}
    active_intent = str(state.get("active_intent") or "").strip()
    if active_intent:
        parts.append(active_intent)
    for item in state.get("selected_items") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("product_ref") or item.get("offer_ref") or "").strip()
        if ref:
            parts.append(ref.replace(":", " "))
    summary = (session_summary or "").strip()
    if summary:
        parts.append(summary[:320])
    return " ".join(part for part in parts if part).strip()


def _authority_with_tool_lines(
    authority: CatalogAuthorityBundle,
    tool_lines: list[str],
) -> CatalogAuthorityBundle:
    lines = [line for line in tool_lines if str(line).strip()]
    if not lines:
        return authority
    existing = set(authority.approved_authority_lines())
    synthetic_products = []
    synthetic_offers = []
    synthetic_media = []
    for line in lines:
        if line in existing:
            continue
        if line.startswith("[PRODUCT] "):
            synthetic_products.append(
                CatalogAuthorityProduct(
                    fact_id=_tool_authority_ref(line),
                    title=line.removeprefix("[PRODUCT] ").strip(),
                    authority_state="approved",
                    source_refs=["hermes_tool:knowledge_search_catalog"],
                )
            )
        elif line.startswith("[OFFER] "):
            label, _, detail = line.removeprefix("[OFFER] ").partition(":")
            price, currency = _split_price_currency(detail.strip())
            synthetic_offers.append(
                CatalogAuthorityOffer(
                    fact_id=_tool_authority_ref(line),
                    product_title=label.strip() or None,
                    price=price,
                    currency=currency,
                    stock_state=(detail.strip() if not price else None),
                    authority_state="approved",
                    source_refs=["hermes_tool:knowledge_search_catalog"],
                )
            )
        elif line.startswith("[MEDIA] "):
            label, _, detail = line.removeprefix("[MEDIA] ").partition(":")
            synthetic_media.append(
                CatalogAuthorityMedia(
                    media_ref=_tool_authority_ref(line),
                    product_title=label.strip() or None,
                    caption=detail.strip(),
                    authority_state="approved",
                    source_refs=["hermes_tool:knowledge_search_catalog"],
                )
            )
    if not (synthetic_products or synthetic_offers or synthetic_media):
        return authority
    return authority.model_copy(
        update={
            "products": [*authority.products, *synthetic_products],
            "offers": [*authority.offers, *synthetic_offers],
            "media": [*authority.media, *synthetic_media],
            "source_refs": list(
                dict.fromkeys(
                    [
                        *authority.source_refs,
                        "hermes_tool:knowledge_search_catalog",
                    ]
                )
            ),
            "authority_states": sorted(
                set([*authority.authority_states, "approved"])
            ),
        }
    )


def _tool_authority_ref(line: str) -> str:
    digest = hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]
    return f"tool_authority:{digest}"


def _split_price_currency(detail: str) -> tuple[str | None, str | None]:
    if not detail:
        return None, None
    parts = detail.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isalpha() and len(parts[1]) <= 6:
        return parts[0].strip() or None, parts[1].strip() or None
    return detail, None


def _catalog_warning_codes(bundle: CatalogAuthorityBundle) -> list[str]:
    codes: list[str] = []
    for warning in bundle.warnings:
        code = warning.code
        if warning.field:
            code = f"{code}:{warning.field}"
        if warning.detail:
            code = f"{code}:{warning.detail}"
        codes.append(code)
    return codes


def _is_catalog_truth_line(line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in _CATALOG_TRUTH_PREFIXES)


def _normalize_title(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _telemetry_with_scoped_catalog_authority(
    telemetry: dict[str, Any],
    *,
    truth_evidence_count: int,
    catalog_authority_line_count: int,
) -> dict[str, Any]:
    next_telemetry = dict(telemetry or {})
    grounding = dict(next_telemetry.get("grounding") or {})
    grounding["truth_evidence_count"] = truth_evidence_count
    grounding["catalog_authority_scoped"] = True
    grounding["catalog_authority_line_count"] = catalog_authority_line_count
    next_telemetry["grounding"] = grounding
    return next_telemetry


def _has_media_context(messages: Any) -> bool:
    if not isinstance(messages, list):
        return False
    for message in messages:
        if (
            getattr(message, "media_type", None)
            or getattr(message, "media_description", None)
            or getattr(message, "transcription", None)
        ):
            return True
    return False


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _empty_turn_context_telemetry(
    *,
    latency_ms: float = 0.0,
    degraded_reason: str | None = None,
) -> dict[str, Any]:
    degraded_reasons = [degraded_reason] if degraded_reason else []
    return {
        "schema_version": "turn_context_telemetry.v1",
        "latency": {"total_ms": latency_ms},
        "grounding": {
            "truth_evidence_count": 0,
            "history_count": 0,
            "voice_example_count": 0,
            "authority_warning_count": 0,
            "candidate_count": 0,
            "source_ref_count": 0,
            "evidence_backed": False,
            "retrieval_channels": [],
            "degraded_reasons": degraded_reasons,
            "degraded_count": len(degraded_reasons),
        },
        "context_runtime": {},
    }


def _turn_context_telemetry(
    *,
    context_telemetry: dict[str, Any],
    truth_evidence: list[str],
    history: list[str],
    voice_examples: list[str],
    authority_warnings: list[str],
    latency_ms: float,
) -> dict[str, Any]:
    grounding = dict(context_telemetry.get("grounding") or {})
    latency = dict(context_telemetry.get("latency") or {})
    degraded_reasons = list(grounding.get("degraded_reasons") or [])
    return {
        "schema_version": "turn_context_telemetry.v1",
        "latency": {
            "total_ms": latency_ms,
            "agent_runtime_context_total_ms": latency.get("total_ms"),
            "grounding_ms": latency.get("grounding_ms"),
            "documents_ms": latency.get("documents_ms"),
            "recent_messages_ms": latency.get("recent_messages_ms"),
        },
        "grounding": {
            "truth_evidence_count": len(truth_evidence),
            "history_count": len(history),
            "voice_example_count": len(voice_examples),
            "authority_warning_count": len(authority_warnings),
            "candidate_count": int(grounding.get("candidate_count") or 0),
            "family_counts": dict(grounding.get("family_counts") or {}),
            "source_ref_count": int(grounding.get("source_ref_count") or 0),
            "selected_fact_count": int(grounding.get("selected_fact_count") or 0),
            "missing_evidence_count": int(grounding.get("missing_evidence_count") or 0),
            "unavailable_family_count": int(grounding.get("unavailable_family_count") or 0),
            "degraded_count": int(grounding.get("degraded_count") or 0),
            "evidence_backed": bool(grounding.get("evidence_backed")),
            "retrieval_channels": list(grounding.get("retrieval_channels") or []),
            "rerank_state": grounding.get("rerank_state"),
            "degraded_reasons": degraded_reasons,
            "avg_top_score": grounding.get("avg_top_score"),
        },
        "context_runtime": context_telemetry,
    }


def _format_history(messages: Any, *, exclude: str, limit: int = 30) -> list[str]:
    if not isinstance(messages, list):
        return []
    lines: list[str] = []
    for message in messages:
        content = (getattr(message, "content", "") or "").strip()
        if not content:
            media_type = getattr(message, "media_type", None)
            content = f"[{media_type}]" if media_type else ""
        if not content or content == exclude:
            continue
        label = _ROLE_LABEL.get(getattr(message, "sender_type", ""), "Customer")
        ref = _message_ref(message)
        suffix = f" ({ref})" if ref else ""
        lines.append(f"{label}{suffix}: {content}")
    return lines[-limit:]


def _context_static_payload(context: Any) -> dict[str, Any]:
    cache_plan = getattr(context, "cache_plan", None)
    if cache_plan is None:
        return {}
    return {
        "schema_version": "agent_static_context_ref.v1",
        "cache_key": str(getattr(cache_plan, "cache_key", "") or ""),
        "material_hash": str(getattr(cache_plan, "material_hash", "") or ""),
        "invalidation_refs": list(getattr(cache_plan, "invalidation_refs", []) or []),
        "cacheable": bool(getattr(cache_plan, "cacheable", True)),
    }


def _static_context_payload(
    base: dict[str, Any],
    *,
    profile_hash: str,
    agent_material_refs: list[str],
    tool_grants: tuple[str, ...],
) -> dict[str, Any]:
    payload = dict(base or {})
    cache_key = str(payload.get("cache_key") or "")
    material_hash = str(payload.get("material_hash") or "")
    cache_keys = [
        key
        for key in [
            cache_key,
            f"profile:{profile_hash}",
            *agent_material_refs,
            *[f"tool_grant:{scope}" for scope in tool_grants],
        ]
        if key
    ]
    return {
        "schema_version": "agent_static_context_ref.v1",
        "cache_key": cache_key,
        "material_hash": material_hash,
        "cache_keys": cache_keys,
        "invalidation_refs": list(payload.get("invalidation_refs") or []),
        "cacheable": bool(payload.get("cacheable", True)),
    }


def _dynamic_context_payload(
    *,
    customer_turn_text: str,
    customer_query_text: str,
    session_summary: str,
    transcript_hits: list[str],
    conversation_state: dict[str, Any],
    authority_lines: list[str],
    style_lines: list[str],
    policy_warnings: list[str],
) -> dict[str, Any]:
    dynamic_payload = {
        "customer_turn_text": customer_turn_text,
        "customer_query_text": customer_query_text,
        "session_summary": session_summary,
        "transcript_hits": transcript_hits,
        "conversation_state": conversation_state,
        "authority_lines": authority_lines,
        "style_lines": style_lines,
        "policy_warnings": policy_warnings,
    }
    estimated_bytes = len(
        # default=str so a stray non-JSON-native value (e.g. a Decimal) degrades to
        # its string form instead of crashing this telemetry byte-count and poisoning
        # the whole turn — matches the sibling dumps below (live 2026-06-15).
        json.dumps(
            dynamic_payload, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
    )
    return {
        "schema_version": "agent_dynamic_turn_context_ref.v1",
        "customer_turn_chars": len(customer_turn_text or ""),
        "customer_query_chars": len(customer_query_text or ""),
        "session_summary_chars": len(session_summary or ""),
        "transcript_hit_count": len(transcript_hits or []),
        "conversation_state_chars": len(
            json.dumps(
                conversation_state or {},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        ),
        "authority_line_count": len(authority_lines or []),
        "style_line_count": len(style_lines or []),
        "policy_warning_count": len(policy_warnings or []),
        "estimated_bytes": estimated_bytes,
        "estimated_tokens": max(1, estimated_bytes // 4),
        "full_history_rebuild": False,
    }


def _context_efficiency_payload(packet: dict[str, Any] | None) -> dict[str, Any]:
    if packet is None:
        return {
            "schema_version": "agent_context_efficiency.v1",
            "available": False,
        }
    static_context = dict(packet.get("static_context") or {})
    dynamic_context = dict(packet.get("dynamic_context") or {})
    # material_hash already lives in static_context (and the runtime_context_packet
    # blob) — not re-exposed here (#412 dedupe).
    return {
        "schema_version": "agent_context_efficiency.v1",
        "available": True,
        "static_cache_key": static_context.get("cache_key"),
        "static_cache_key_count": len(static_context.get("cache_keys") or []),
        "dynamic_estimated_bytes": int(dynamic_context.get("estimated_bytes") or 0),
        "dynamic_estimated_tokens": int(dynamic_context.get("estimated_tokens") or 0),
        "dynamic_transcript_hit_count": int(
            dynamic_context.get("transcript_hit_count") or 0
        ),
        "dynamic_conversation_state_chars": int(
            dynamic_context.get("conversation_state_chars") or 0
        ),
        "full_history_rebuild": bool(dynamic_context.get("full_history_rebuild")),
    }


def _message_ref(message: Any) -> str:
    local_id = getattr(message, "id", None)
    telegram_id = getattr(message, "telegram_message_id", None)
    refs: list[str] = []
    if local_id is not None:
        refs.append(f"ref: message:{local_id}")
    if telegram_id is not None:
        refs.append(f"telegram: {telegram_id}")
    return ", ".join(refs)


def build_turn_telemetry(
    *,
    profile_hash: str,
    profile_kind: str,
    execution_mode: str,
    allowed_tool_names: tuple[str, ...],
    authority_bundle_size: int,
    warning_count: int,
    missing_field_count: int,
    unsupported_authority_claims: int,
    confidence: float,
    decision: str,
    faithfulness_mode: str = "deferred_critic",
    context_efficiency: dict[str, Any] | None = None,
    finalization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured per-turn telemetry persisted on HermesRun.details so an
    operator can answer: which profile ran, which tools were exposed, how big the
    authority bundle was, the faithfulness verdict, and the send decision."""
    return {
        "schema_version": "agent_turn_telemetry.v1",
        "profile_hash": profile_hash,
        "profile_kind": profile_kind,
        "execution_mode": execution_mode,
        "tools_exposed": list(allowed_tool_names),
        "authority": {
            "bundle_size": authority_bundle_size,
            "warning_count": warning_count,
            "missing_field_count": missing_field_count,
        },
        "faithfulness": {
            "mode": faithfulness_mode,
            "unsupported_authority_claims": unsupported_authority_claims,
        },
        "finalization": finalization
        or {
            "schema_version": "customer_reply_finalization_guard.v1",
            "available": False,
        },
        "context_efficiency": context_efficiency
        or {
            "schema_version": "agent_context_efficiency.v1",
            "available": False,
        },
        "confidence": confidence,
        "decision": decision,
    }
