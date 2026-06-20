from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_session import AgentSession, AgentSessionEvent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.workspace import Workspace
from app.modules.agent_documents.contracts import AgentSkillRead
from app.modules.agent_documents.renderer import (
    render_agent_md,
    render_business_md,
    render_skill_md,
)
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.agent_runtime_context.contracts import (
    AgentRuntimeCachePlan,
    AgentRuntimeContext,
    AgentRuntimeContextRequest,
    AgentRuntimeDocumentContext,
    AgentRuntimeKind,
    AgentRuntimeMessage,
    AgentRuntimePermissionContext,
)
from app.modules.agent_sessions.service import AgentSessionService
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.retrieval_core.contracts import RetrievalAgentGroundingRequest
from app.modules.retrieval_core.service import RetrievalCoreService
from app.modules.tool_catalog import external_tool_scopes, internal_capability_scopes
from app.modules.tool_grants.service import ToolGrantService

DEFAULT_FACT_TYPES_BY_AGENT_KIND: dict[AgentRuntimeKind, tuple[str, ...]] = {
    "seller_agent": (
        "catalog_product",
        "catalog_variant",
        "catalog_offer",
        "catalog_media",
        "knowledge_fact",
        "seller_rule_fact",
        "voice_fact",
        "business_source_media_fact",
    ),
    "support_agent": (
        "knowledge_fact",
        "seller_rule_fact",
        "voice_fact",
        "business_source_media_fact",
    ),
    "catalog_update_agent": (
        "catalog_product",
        "catalog_media",
        "business_source_fact",
        "business_source_media_fact",
    ),
    "follow_up_agent": (
        "seller_rule_fact",
        "voice_fact",
        "conversation_pair_fact",
    ),
    "bi_agent": (
        "catalog_product",
        "knowledge_fact",
        "seller_rule_fact",
        "voice_fact",
        "business_source_fact",
    ),
    "custom_agent": (
        "catalog_product",
        "knowledge_fact",
        "seller_rule",
        "voice_fact",
    ),
    "promoter_agent": (
        "catalog_product",
        "knowledge_fact",
        "seller_rule",
    ),
    "setup_agent": (
        "catalog_product",
        "catalog_media",
        "knowledge_fact",
        "seller_rule_fact",
        "voice_fact",
        "business_source_fact",
        "business_source_media_fact",
    ),
}


class AgentRuntimeContextService:
    """Builds the durable context package every agent runtime should consume.

    The service intentionally assembles existing sources of truth instead of
    creating another brain: rendered documents, structured skills, grants,
    recent conversation history, and Retrieval Core grounding.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        retrieval: RetrievalCoreService | None = None,
    ) -> None:
        self._session = session
        self._documents = AgentDocumentService(session)
        self._grants = ToolGrantService(session)
        self._retrieval = retrieval or RetrievalCoreService(
            repository=CommercialSpineRepository(session)
        )

    async def build(self, request: AgentRuntimeContextRequest) -> AgentRuntimeContext:
        total_started = time.perf_counter()
        timings: dict[str, float] = {}

        stage_started = time.perf_counter()
        workspace = await self._workspace(request.workspace_id)
        agent = await self._agent(
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
        )
        agent_kind = _agent_kind(agent)
        timings["identity_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        business_sections = await self._documents.list_sections(
            workspace_id=request.workspace_id,
            document_kind="business",
            subject_type="workspace",
            subject_id=None,
        )
        agent_sections = await self._documents.list_sections(
            workspace_id=request.workspace_id,
            document_kind="agent",
            subject_type="agent",
            subject_id=agent.id,
        )
        skills = await self._agent_skills(
            workspace_id=request.workspace_id,
            agent_id=agent.id,
        )
        business_md = render_business_md(workspace.name, business_sections)
        agent_md = render_agent_md(agent, agent_sections, skills)
        skill_docs = [
            render_skill_md(
                skill,
                await self._documents.list_sections(
                    workspace_id=request.workspace_id,
                    document_kind="skill",
                    subject_type="skill",
                    subject_id=skill.id,
                ),
            )
            for skill in skills
        ]
        timings["documents_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        permissions = await self._permission_context(
            workspace_id=request.workspace_id,
            agent=agent,
        )
        timings["permissions_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        recent_messages = await self._recent_messages(request)
        timings["recent_messages_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        session_summary, transcript_hits = await self._agent_session_context(request)
        timings["agent_session_ms"] = _elapsed_ms(stage_started)

        grounding = None
        degraded_reasons: list[str] = []
        stage_started = time.perf_counter()
        if request.include_grounding:
            try:
                grounding = await self._retrieval.build_agent_grounding(
                    RetrievalAgentGroundingRequest(
                        workspace_id=request.workspace_id,
                        agent_kind=agent_kind,
                        requested_fact_types=(
                            list(request.requested_fact_types)
                            or list(DEFAULT_FACT_TYPES_BY_AGENT_KIND[agent_kind])
                        ),
                        requested_slots=list(request.requested_slots),
                        query_text=_effective_query_text(request, recent_messages),
                        query_modalities=list(request.query_modalities),
                        enable_semantic=request.enable_semantic,
                        enable_contextual_rank=request.enable_contextual_rank,
                        enable_query_rewrite=request.enable_query_rewrite,
                        enable_agentic_search=request.enable_agentic_search,
                        enable_rerank=request.enable_rerank,
                        include_proposed=request.include_proposed_knowledge,
                    )
                )
                degraded_reasons.extend(grounding.degraded_reasons)
            except Exception:
                degraded_reasons.append("agent_grounding_unavailable")
        timings["grounding_ms"] = _elapsed_ms(stage_started)

        documents = AgentRuntimeDocumentContext(
            business_md=business_md,
            agent_md=agent_md,
            skill_md=skill_docs,
        )
        cache_plan = _cache_plan(
            workspace_id=request.workspace_id,
            agent=agent,
            documents=documents,
            permissions=permissions,
        )
        timings["total_ms"] = _elapsed_ms(total_started)
        telemetry = {
            "schema_version": "agent_runtime_context_telemetry.v1",
            "latency": timings,
            "grounding": _grounding_telemetry(grounding),
            "request": {
                "include_grounding": request.include_grounding,
                "enable_semantic": request.enable_semantic,
                "enable_contextual_rank": request.enable_contextual_rank,
                "enable_query_rewrite": request.enable_query_rewrite,
                "enable_agentic_search": request.enable_agentic_search,
                "enable_rerank": request.enable_rerank,
                "requested_fact_types": (
                    list(request.requested_fact_types)
                    or list(DEFAULT_FACT_TYPES_BY_AGENT_KIND[agent_kind])
                ),
                "requested_slots": list(request.requested_slots),
                "query_modalities": list(request.query_modalities),
            },
            "agent_session": {
                "agent_session_id": request.agent_session_id,
                "hermes_session_id": request.hermes_session_id,
                "event_count": len(transcript_hits),
                "has_summary": bool(session_summary.strip()),
            },
        }
        return AgentRuntimeContext(
            workspace_id=request.workspace_id,
            agent_id=agent.id,
            agent_name=agent.name,
            agent_kind=agent_kind,
            documents=documents,
            permissions=permissions,
            recent_messages=recent_messages,
            session_summary=session_summary,
            transcript_hits=transcript_hits,
            grounding=grounding,
            cache_plan=cache_plan,
            prompt_sections={
                "static": {
                    "business_md": business_md.markdown,
                    "agent_md": agent_md.markdown,
                    "skill_md": [item.markdown for item in skill_docs],
                    "permissions": permissions.model_dump(mode="json"),
                },
                "dynamic": {
                    "query_text": request.query_text,
                    "conversation_id": request.conversation_id,
                    "agent_session": {
                        "agent_session_id": request.agent_session_id,
                        "hermes_session_id": request.hermes_session_id,
                        "summary": session_summary,
                        "transcript_hits": transcript_hits,
                    },
                    "recent_messages": [
                        item.model_dump(mode="json") for item in recent_messages
                    ],
                    "grounding": (
                        grounding.model_dump(mode="json") if grounding is not None else None
                    ),
                },
            },
            degraded_reasons=_unique(degraded_reasons),
            telemetry=telemetry,
        )

    async def _workspace(self, workspace_id: int) -> Workspace:
        workspace = await self._session.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError("workspace not found")
        return workspace

    async def _agent_session_context(
        self,
        request: AgentRuntimeContextRequest,
    ) -> tuple[str, list[str]]:
        if request.agent_session_id is None:
            return "", []
        agent_session = await self._session.get(
            AgentSession,
            request.agent_session_id,
            populate_existing=True,
        )
        if agent_session is None:
            return "", []
        if int(agent_session.workspace_id) != int(request.workspace_id):
            return "", []
        if int(agent_session.agent_id) != int(request.agent_id):
            return "", []
        events = await AgentSessionService(self._session).load_recent_events(
            agent_session_id=agent_session.id,
            limit=request.transcript_event_limit,
        )
        summary = agent_session.summary if request.include_agent_session_summary else ""
        return summary, [_format_agent_session_event(event) for event in events]

    async def _agent(self, *, workspace_id: int, agent_id: int) -> Agent:
        agent = await self._session.get(Agent, agent_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("agent_id does not belong to this workspace")
        if not agent.is_active:
            raise ValueError("agent is inactive")
        return agent

    async def _agent_skills(
        self,
        *,
        workspace_id: int,
        agent_id: int,
    ) -> list[AgentSkillRead]:
        skills = await self._documents.list_skills(workspace_id=workspace_id)
        return [
            skill
            for skill in skills
            if skill.enabled and skill.agent_id in (None, agent_id)
        ]

    async def _permission_context(
        self,
        *,
        workspace_id: int,
        agent: Agent,
    ) -> AgentRuntimePermissionContext:
        configured = _configured_tool_scopes(agent)
        expected_external = external_tool_scopes(configured)
        internal = internal_capability_scopes(configured)
        grants = await self._grants.list_for_workspace(
            workspace_id=workspace_id,
            agent_id=agent.id,
        )
        active_external = sorted(
            {
                grant.scope
                for grant in grants
                if grant.active and grant.scope in set(expected_external)
            }
        )
        missing_external = sorted(set(expected_external) - set(active_external))
        return AgentRuntimePermissionContext(
            internal_capabilities=sorted(internal),
            expected_external_scopes=sorted(expected_external),
            active_external_scopes=active_external,
            missing_external_scopes=missing_external,
            permission_mode=_permission_mode(agent),
            trust_mode=agent.trust_mode,
        )

    async def _recent_messages(
        self,
        request: AgentRuntimeContextRequest,
    ) -> list[AgentRuntimeMessage]:
        if request.conversation_id is None or request.recent_message_limit == 0:
            return []
        stmt = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.workspace_id == request.workspace_id,
                Conversation.id == request.conversation_id,
                Message.is_deleted.is_(False),
            )
            .order_by(
                func.coalesce(Message.telegram_timestamp, Message.created_at).desc(),
                Message.telegram_message_id.desc().nullslast(),
                Message.id.desc(),
            )
            .limit(request.recent_message_limit)
        )
        result = await self._session.scalars(stmt)
        rows = list(reversed(result.all()))
        return [
            AgentRuntimeMessage(
                id=row.id,
                conversation_id=row.conversation_id,
                sender_type=row.sender_type,
                content=row.content,
                media_type=row.media_type,
                media_description=row.media_description,
                transcription=row.transcription,
                created_at=row.created_at,
            )
            for row in rows
        ]

def _clean_optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _grounding_telemetry(grounding: Any) -> dict[str, Any]:
    if grounding is None:
        return {
            "candidate_count": 0,
            "family_counts": {},
            "source_ref_count": 0,
            "selected_fact_count": 0,
            "missing_evidence_count": 0,
            "unavailable_family_count": 0,
            "degraded_count": 0,
            "evidence_backed": False,
            "retrieval_channels": [],
            "rerank_state": None,
            "degraded_reasons": [],
            "avg_top_score": None,
        }
    families = getattr(grounding, "families", {}) or {}
    family_counts = {
        str(family): len(candidates)
        for family, candidates in families.items()
        if isinstance(candidates, list)
    }
    source_refs: list[str] = []
    for candidates in families.values():
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, dict):
                source_refs.extend(str(ref) for ref in list(candidate.get("source_refs") or []) if ref)
    trace = getattr(grounding, "trace", None)
    candidate_scores = getattr(trace, "candidate_scores", {}) if trace is not None else {}
    top_scores: list[float] = []
    if isinstance(candidate_scores, dict):
        for scores in candidate_scores.values():
            if isinstance(scores, dict) and scores:
                top_scores.append(float(max(scores.values())))
    source_ref_count = len(dict.fromkeys(source_refs))
    candidate_count = sum(family_counts.values())
    degraded_reasons = list(getattr(grounding, "degraded_reasons", []) or [])
    return {
        "candidate_count": candidate_count,
        "family_counts": family_counts,
        "source_ref_count": source_ref_count,
        "selected_fact_count": len(list(getattr(trace, "selected_fact_ids", []) or [])),
        "missing_evidence_count": len(list(getattr(grounding, "missing_evidence", []) or [])),
        "unavailable_family_count": len(list(getattr(grounding, "unavailable_families", []) or [])),
        "degraded_count": len(degraded_reasons),
        "evidence_backed": bool(candidate_count and source_ref_count),
        "retrieval_channels": list(getattr(trace, "retrieval_channels", []) or []),
        "rerank_state": getattr(trace, "rerank_state", None),
        "degraded_reasons": degraded_reasons,
        "avg_top_score": (
            round(float(sum(top_scores) / len(top_scores)), 4)
            if top_scores
            else None
        ),
    }


def _agent_kind(agent: Agent) -> AgentRuntimeKind:
    value = (agent.agent_type or "").strip().lower()
    if value in {"seller", "customer"}:
        return "seller_agent"
    if value == "support":
        return "support_agent"
    if value == "catalog_update":
        return "catalog_update_agent"
    if value == "follow_up":
        return "follow_up_agent"
    if value == "bi":
        return "bi_agent"
    if value == "promoter_agent":
        return value  # type: ignore[return-value]
    if value in {"setup", "onboarding", "setup_agent", "owner"}:
        # The Owner Agent shares the setup runtime kind -> setup execution mode
        # (operator prompt + owner toolset). agent_type stays "owner" (distinct
        # from onboarding); only the runtime profile is shared (#455).
        return "setup_agent"
    return "custom_agent"


def _configured_tool_scopes(agent: Agent) -> list[str]:
    config = dict(agent.tools_config or {})
    raw = config.get("tool_scopes")
    if not isinstance(raw, list):
        raw = config.get("enabled_tools")
    if not isinstance(raw, list):
        return []
    return _unique(str(item).strip() for item in raw if str(item).strip())


def _permission_mode(agent: Agent) -> str:
    config = dict(agent.tools_config or {})
    value = str(config.get("permission_mode") or "").strip()
    return value or agent.trust_mode


def _effective_query_text(
    request: AgentRuntimeContextRequest,
    recent_messages: list[AgentRuntimeMessage],
) -> str | None:
    query = (request.query_text or "").strip()
    if query:
        return query
    for message in reversed(recent_messages):
        if message.sender_type == "customer" and message.content.strip():
            return message.content.strip()
    return None


def _format_agent_session_event(event: AgentSessionEvent) -> str:
    label = f"{event.event_type} {event.direction}".strip()
    text = (event.text or "").strip()
    if text:
        return f"{label}: {text}"
    message_id = getattr(event, "message_id", None)
    if message_id is not None:
        return f"{label}: message:{message_id}"
    return label


def _cache_plan(
    *,
    workspace_id: int,
    agent: Agent,
    documents: AgentRuntimeDocumentContext,
    permissions: AgentRuntimePermissionContext,
) -> AgentRuntimeCachePlan:
    material: dict[str, Any] = {
        "schema_version": "agent_static_context_material.v1",
        "workspace_id": workspace_id,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "agent_kind": _agent_kind(agent),
        "trust_mode": agent.trust_mode,
        "business_md": documents.business_md.markdown,
        "agent_md": documents.agent_md.markdown,
        "skill_md": [item.markdown for item in documents.skill_md],
        "permissions": permissions.model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return AgentRuntimeCachePlan(
        cache_scope="agent",
        cache_key=f"agent-runtime-context:v1:{workspace_id}:{agent.id}:{digest[:16]}",
        material_hash=digest,
        invalidation_refs=[
            f"workspace:{workspace_id}:BUSINESS.md",
            f"agent:{agent.id}:AGENT.md",
            f"agent:{agent.id}:SKILL.md",
            f"agent:{agent.id}:tool_grants",
        ],
    )


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
