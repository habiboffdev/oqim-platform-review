from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from app.modules.bi_promoter.contracts import (
    BIAnalyticsDashboard,
    BIInsight,
    BIInsightRequest,
    BIInvestigationFinding,
    BIInvestigationFixCandidate,
    BIInvestigationRequest,
    BIInvestigationResult,
    PromoterCampaignInput,
    PromoterCampaignPlan,
    PromoterCandidateDecision,
    PromoterCandidateInput,
    PromoterPolicy,
    PromoterPolicyInput,
    PromoterProjectionCampaignInput,
)
from app.modules.commercial_spine.contracts import (
    BusinessBrainProjection,
    CommercialActionProposal,
    CommercialDecisionTrace,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository


class BIPromoterService:
    def __init__(self, *, repository: CommercialSpineRepository) -> None:
        self._repository = repository

    async def answer(self, request: BIInsightRequest) -> BIInsight:
        context = await self._workspace_context()
        source_refs = _source_refs_from_context(context, request.source_refs)
        degraded_reasons = _degraded_reasons_from_context(context)
        if _context_projection_count(context) == 0:
            degraded_reasons.append("no_projection_data")
        if not source_refs:
            source_refs = ["bi:projection_empty"]
            degraded_reasons.append("no_projection_source_refs")
        metrics, records = _insight_payload(request.question_kind, context)
        insight = BIInsight(
            workspace_id=request.workspace_id,
            insight_id=_insight_id(request),
            insight_type=request.question_kind,
            answer=_answer_text(request.question_kind, metrics),
            metrics=metrics,
            records=records,
            source_refs=source_refs,
            confidence=_confidence(source_refs=source_refs, degraded_reasons=degraded_reasons),
            freshness=_freshness(source_refs=source_refs, degraded_reasons=degraded_reasons),
            degraded_reasons=degraded_reasons,
        )
        projection_ref = f"bi:insight:{insight.insight_id}"
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=projection_ref,
                workspace_id=request.workspace_id,
                projection_type="bi_insight",
                entity_ref=f"workspace:{request.workspace_id}",
                state=insight.model_dump(mode="json"),
                source_refs=source_refs,
                degraded=bool(degraded_reasons),
                degraded_reasons=degraded_reasons,
            )
        )
        await self._repository.persist_decision_trace(
            CommercialDecisionTrace(
                trace_id=f"bi:{insight.insight_id}",
                workspace_id=request.workspace_id,
                correlation_id=request.correlation_id,
                changed_projection_refs=[f"projection:{projection_ref}"],
                degraded_reasons=degraded_reasons,
            )
        )
        return insight

    async def dashboard(
        self,
        *,
        workspace_id: int,
        source_refs: list[str],
        correlation_id: str,
    ) -> BIAnalyticsDashboard:
        context = await self._workspace_context()
        context_source_refs = _source_refs_from_context(context, source_refs)
        degraded_reasons = _degraded_reasons_from_context(context)
        if _context_projection_count(context) == 0:
            degraded_reasons.append("no_projection_data")
        if not context_source_refs:
            context_source_refs = ["bi:projection_empty"]
            degraded_reasons.append("no_projection_source_refs")
        insight_kinds = (
            "pipeline_summary",
            "attention_queue",
            "product_channel_breakdown",
            "source_freshness",
        )
        insights = [
            await self.answer(
                BIInsightRequest(
                    workspace_id=workspace_id,
                    question_kind=kind,  # type: ignore[arg-type]
                    source_refs=source_refs,
                    correlation_id=f"{correlation_id}:{kind}",
                )
            )
            for kind in insight_kinds
        ]
        pipeline = insights[0]
        breakdown = insights[2]
        dashboard = BIAnalyticsDashboard(
            workspace_id=workspace_id,
            summary={
                "customer_count": pipeline.metrics.get("customer_count", 0),
                "opportunity_count": pipeline.metrics.get("opportunity_count", 0),
                "reply_needed_count": pipeline.metrics.get("reply_needed_count", 0),
                "orders_count": breakdown.metrics.get("orders_count", 0),
                "stalled_opportunity_count": _stalled_metrics(context)[
                    "stalled_opportunity_count"
                ],
            },
            breakdowns={
                "customer_stages": _counter_records(
                    pipeline.metrics.get("customer_stages", {})
                ),
                "opportunity_stages": _counter_records(
                    pipeline.metrics.get("opportunity_stages", {})
                ),
                "products": _breakdown_records(
                    breakdown.metrics.get("product_breakdown", {})
                ),
                "channels": _breakdown_records(
                    breakdown.metrics.get("channel_breakdown", {})
                ),
            },
            insights=insights,
            source_refs=context_source_refs,
            freshness=_freshness(
                source_refs=context_source_refs,
                degraded_reasons=degraded_reasons,
            ),
            degraded_reasons=degraded_reasons,
        )
        projection_ref = f"bi:analytics_dashboard:{workspace_id}"
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=projection_ref,
                workspace_id=workspace_id,
                projection_type="bi_analytics_dashboard",
                entity_ref=f"workspace:{workspace_id}",
                state=dashboard.model_dump(mode="json"),
                source_refs=dashboard.source_refs,
                degraded=bool(dashboard.degraded_reasons),
                degraded_reasons=dashboard.degraded_reasons,
            )
        )
        await self._repository.persist_decision_trace(
            CommercialDecisionTrace(
                trace_id=f"bi:dashboard:{workspace_id}:{correlation_id}",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                changed_projection_refs=[f"projection:{projection_ref}"],
                degraded_reasons=dashboard.degraded_reasons,
            )
        )
        return dashboard

    async def investigate(
        self,
        request: BIInvestigationRequest,
    ) -> BIInvestigationResult:
        context = await self._workspace_context()
        source_refs = _source_refs_from_context(context, request.source_refs)
        degraded_reasons = _degraded_reasons_from_context(context)
        if _context_projection_count(context) == 0:
            degraded_reasons.append("no_projection_data")
        if not source_refs:
            source_refs = ["bi:projection_empty"]
            degraded_reasons.append("no_projection_source_refs")
        findings = _investigation_findings(context)
        fix_candidates = _fix_candidates(findings)
        if not findings:
            findings = [
                BIInvestigationFinding(
                    finding_ref=f"{request.investigation_ref}:clear",
                    finding_type="source_freshness",
                    severity="low",
                    title="No urgent BI findings",
                    summary="Current Business Brain and OQIM Intelligence projections have no urgent investigation finding.",
                    source_refs=source_refs,
                    confidence=0.78,
                    suggested_action="Keep watching projection freshness.",
                )
            ]
        result = BIInvestigationResult(
            workspace_id=request.workspace_id,
            investigation_ref=request.investigation_ref,
            status="ready",
            findings=findings,
            fix_candidates=fix_candidates,
            source_refs=source_refs,
            confidence=_confidence(source_refs=source_refs, degraded_reasons=degraded_reasons),
            freshness=_freshness(source_refs=source_refs, degraded_reasons=degraded_reasons),
            degraded_reasons=degraded_reasons,
        )
        projection_ref = f"bi:investigation:{request.investigation_ref}"
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=projection_ref,
                workspace_id=request.workspace_id,
                projection_type="bi_investigation",
                entity_ref=f"workspace:{request.workspace_id}",
                state=result.model_dump(mode="json"),
                source_refs=result.source_refs,
                degraded=bool(result.degraded_reasons),
                degraded_reasons=result.degraded_reasons,
            )
        )
        await self._repository.persist_decision_trace(
            CommercialDecisionTrace(
                trace_id=f"bi:investigation:{request.workspace_id}:{request.investigation_ref}",
                workspace_id=request.workspace_id,
                correlation_id=request.correlation_id,
                changed_projection_refs=[f"projection:{projection_ref}"],
                degraded_reasons=result.degraded_reasons,
            )
        )
        return result

    async def set_promoter_policy(
        self,
        payload: PromoterPolicyInput,
    ) -> PromoterPolicy:
        policy = PromoterPolicy(
            workspace_id=payload.workspace_id,
            enabled=payload.enabled,
            approved=payload.approved,
            allowed_stages=list(dict.fromkeys(payload.allowed_stages)),
            max_contacts_per_7d=payload.max_contacts_per_7d,
            quiet_hours=dict(payload.quiet_hours),
            source_refs=_unique(payload.source_refs) or ["promoter:policy"],
            correlation_id=payload.correlation_id,
        )
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=_policy_ref(payload.workspace_id),
                workspace_id=payload.workspace_id,
                projection_type="promoter_policy",
                entity_ref=f"workspace:{payload.workspace_id}",
                state=policy.model_dump(mode="json"),
                source_refs=policy.source_refs,
            )
        )
        return policy

    async def get_promoter_policy(self, *, workspace_id: int) -> PromoterPolicy:
        projection = await self._repository.get_projection(
            workspace_id=workspace_id,
            projection_ref=_policy_ref(workspace_id),
        )
        if projection is None:
            return PromoterPolicy(
                workspace_id=workspace_id,
                enabled=False,
                approved=False,
                allowed_stages=[],
                max_contacts_per_7d=1,
                quiet_hours={},
                source_refs=["promoter:default_policy"],
                correlation_id="promoter:default_policy",
            )
        return PromoterPolicy.model_validate(projection.state)

    async def plan_campaign_from_projections(
        self,
        request: PromoterProjectionCampaignInput,
    ) -> PromoterCampaignPlan:
        context = await self._workspace_context()
        candidates = _promoter_candidates_from_context(
            context,
            max_candidates=request.max_candidates,
        )
        return await self.plan_campaign(
            PromoterCampaignInput(
                workspace_id=request.workspace_id,
                campaign_ref=request.campaign_ref,
                approval_state=request.approval_state,
                message_goal=request.message_goal,
                offer_refs=list(request.offer_refs),
                candidates=candidates,
                source_refs=_source_refs_from_context(context, request.source_refs)
                or list(request.source_refs),
                correlation_id=request.correlation_id,
            )
        )

    async def plan_campaign(
        self,
        request: PromoterCampaignInput,
    ) -> PromoterCampaignPlan:
        policy = await self.get_promoter_policy(workspace_id=request.workspace_id)
        blocked_reasons = _policy_block_reasons(policy, request)
        decisions: list[PromoterCandidateDecision] = []
        proposals: list[CommercialActionProposal] = []
        source_refs = _unique([*request.source_refs, *policy.source_refs])

        if not blocked_reasons:
            for candidate in request.candidates:
                decision = _candidate_policy_decision(
                    candidate_stage=candidate.stage,
                    candidate_opt_out=candidate.opt_out,
                    contact_count_7d=candidate.contact_count_7d,
                    policy=policy,
                )
                if decision != "eligible":
                    decisions.append(
                        PromoterCandidateDecision(
                            customer_id=candidate.customer_id,
                            conversation_id=candidate.conversation_id,
                            stage=candidate.stage,
                            status="skipped",
                            reason_code=decision,
                            source_refs=_unique([*source_refs, *candidate.source_refs]),
                        )
                    )
                    continue
                proposal = await self._proposal(
                    request=request,
                    customer_id=candidate.customer_id,
                    conversation_id=candidate.conversation_id,
                    stage=candidate.stage,
                    source_refs=_unique([*source_refs, *candidate.source_refs]),
                )
                proposals.append(proposal)
                decisions.append(
                    PromoterCandidateDecision(
                        customer_id=candidate.customer_id,
                        conversation_id=candidate.conversation_id,
                        stage=candidate.stage,
                        status="proposed",
                        reason_code="campaign_outreach_requires_approval",
                        proposal_id=proposal.proposal_id,
                        source_refs=_unique([*source_refs, *candidate.source_refs]),
                    )
                )

        if not blocked_reasons and not proposals:
            blocked_reasons.append("no_eligible_candidates")

        plan = PromoterCampaignPlan(
            workspace_id=request.workspace_id,
            campaign_ref=request.campaign_ref,
            status="blocked" if blocked_reasons else "planned",
            blocked_reasons=blocked_reasons,
            decisions=decisions,
            proposals=proposals,
            source_refs=source_refs or ["promoter:campaign"],
            confidence=0.84 if proposals else 0.5,
        )
        projection_ref = f"promoter:campaign:{request.campaign_ref}"
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=projection_ref,
                workspace_id=request.workspace_id,
                projection_type="promoter_campaign_plan",
                entity_ref=f"campaign:{request.campaign_ref}",
                state=plan.model_dump(mode="json"),
                source_refs=plan.source_refs,
                degraded=bool(blocked_reasons),
                degraded_reasons=blocked_reasons,
            )
        )
        await self._repository.persist_decision_trace(
            CommercialDecisionTrace(
                trace_id=f"promoter:{request.workspace_id}:{request.campaign_ref}",
                workspace_id=request.workspace_id,
                correlation_id=request.correlation_id,
                changed_projection_refs=[f"projection:{projection_ref}"],
                emitted_proposal_refs=[
                    f"proposal:{proposal.proposal_id}" for proposal in proposals
                ],
                degraded_reasons=blocked_reasons,
            )
        )
        return plan

    async def _proposal(
        self,
        *,
        request: PromoterCampaignInput,
        customer_id: int,
        conversation_id: int,
        stage: str,
        source_refs: list[str],
    ) -> CommercialActionProposal:
        idempotency_key = (
            f"promoter:{request.workspace_id}:{request.campaign_ref}:"
            f"{conversation_id}:{customer_id}"
        )
        proposal_id = uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex
        proposal = CommercialActionProposal(
            proposal_id=f"proposal-{proposal_id}",
            workspace_id=request.workspace_id,
            conversation_id=conversation_id,
            customer_id=customer_id,
            action_type="promoter_outreach",
            lifecycle_state="waiting_approval",
            execution_mode="ask_seller_confirmation",
            risk_level="medium",
            requires_approval=True,
            priority="medium",
            confidence=0.78,
            reason_code="promoter_campaign_requires_seller_approval",
            source_refs=source_refs,
            payload={
                "campaign_ref": request.campaign_ref,
                "message_goal": request.message_goal,
                "offer_refs": list(request.offer_refs),
                "stage": stage,
                "reply_brief": {
                    "status": "needs_seller_agent_composer",
                    "message_goal": request.message_goal,
                    "offer_refs": list(request.offer_refs),
                    "customer_stage": stage,
                    "must_use_source_refs": list(source_refs),
                },
            },
            idempotency_key=idempotency_key,
            correlation_id=request.correlation_id,
            trace_id=f"promoter:{request.workspace_id}:{request.campaign_ref}",
        )
        await self._repository.persist_action_proposal(proposal)
        return proposal

    async def _workspace_context(self) -> dict[str, tuple[BusinessBrainProjection, ...]]:
        return {
            "customers": (),
            "opportunities": (),
            "reply_needed": (),
            "commercial_states": (),
            "tasks": (),
            "follow_ups": (),
            "lifecycle": (),
            "checkpoints": (),
        }


def _insight_payload(
    question_kind: str,
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if question_kind == "attention_queue":
        records = _attention_records(context)
        return {"attention_count": len(records)}, records
    if question_kind == "source_freshness":
        records = _source_freshness_records(context)
        degraded_count = sum(1 for item in records if item["degraded_reasons"])
        return {"projection_count": len(records), "degraded_count": degraded_count}, records
    if question_kind == "who_bought_what":
        records = _order_records(context)
        return {"orders_count": len(records)}, records
    if question_kind == "hot_customers":
        records = _hot_customer_records(context)
        return {"hot_customer_count": len(records)}, records
    if question_kind == "stalled_opportunities":
        records = _stalled_records(context)
        return _stalled_metrics(context), records
    if question_kind == "product_channel_breakdown":
        return _product_channel_metrics(context), []
    return _pipeline_metrics(context), []


def _pipeline_metrics(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> dict[str, Any]:
    customers = context["customers"]
    opportunities = context["opportunities"]
    reply_needed = context["reply_needed"]
    customer_stages = Counter(_customer_stage(item.state) for item in customers)
    opportunity_stages = Counter(_opportunity_stage(item.state) for item in opportunities)
    reply_needed_count = sum(1 for item in reply_needed if _reply_needed(item.state))
    return {
        "customer_count": len(customers),
        "opportunity_count": len(opportunities),
        "reply_needed_count": reply_needed_count,
        "customer_stages": dict(customer_stages),
        "opportunity_stages": dict(opportunity_stages),
    }


def _stalled_metrics(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> dict[str, Any]:
    records = _stalled_records(context)
    return {"stalled_opportunity_count": len(records)}


def _product_channel_metrics(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> dict[str, Any]:
    product_breakdown: dict[str, dict[str, int]] = {}
    channel_breakdown: dict[str, dict[str, int]] = {}
    orders = _order_records(context)
    opportunities = context["opportunities"]
    for record in orders:
        channel = str(record.get("channel") or "unknown")
        _bump_breakdown(channel_breakdown, channel, "orders")
        for product_ref in record.get("product_refs") or ["unknown"]:
            _bump_breakdown(product_breakdown, str(product_ref), "orders")
    for projection in opportunities:
        opportunity = _dict_child(projection.state, "opportunity")
        channel = str(opportunity.get("channel") or "unknown")
        _bump_breakdown(channel_breakdown, channel, "opportunities")
        product_refs = _string_list(opportunity.get("product_refs")) or ["unknown"]
        for product_ref in product_refs:
            _bump_breakdown(product_breakdown, product_ref, "opportunities")
    return {
        "orders_count": len(orders),
        "product_breakdown": product_breakdown,
        "channel_breakdown": channel_breakdown,
    }


def _answer_text(
    question_kind: str,
    metrics: dict[str, Any],
) -> str:
    if question_kind == "who_bought_what":
        return f"{metrics['orders_count']} source-backed orders found."
    if question_kind == "hot_customers":
        return f"{metrics['hot_customer_count']} customers need sales attention."
    if question_kind == "stalled_opportunities":
        return f"{metrics['stalled_opportunity_count']} opportunities are marked stalled."
    if question_kind == "product_channel_breakdown":
        return f"{metrics['orders_count']} orders grouped by product and channel."
    if question_kind == "attention_queue":
        return f"{metrics['attention_count']} conversations need attention."
    if question_kind == "source_freshness":
        return f"{metrics['projection_count']} projections checked, {metrics['degraded_count']} degraded."
    return (
        f"{metrics['customer_count']} customers, "
        f"{metrics['opportunity_count']} opportunities, "
        f"{metrics['reply_needed_count']} conversations need attention."
    )


def _candidate_policy_decision(
    *,
    candidate_stage: str,
    candidate_opt_out: bool,
    contact_count_7d: int,
    policy: PromoterPolicy,
) -> str:
    if candidate_opt_out:
        return "customer_opted_out"
    if candidate_stage not in set(policy.allowed_stages):
        return "stage_not_allowed"
    if contact_count_7d >= policy.max_contacts_per_7d:
        return "frequency_cap_reached"
    return "eligible"


def _policy_block_reasons(
    policy: PromoterPolicy,
    request: PromoterCampaignInput,
) -> list[str]:
    reasons: list[str] = []
    if not policy.enabled:
        reasons.append("promoter_policy_disabled")
    if not policy.approved:
        reasons.append("promoter_policy_not_approved")
    if request.approval_state != "approved":
        reasons.append("campaign_not_approved")
    if bool(policy.quiet_hours.get("active")):
        reasons.append("quiet_hours_active")
    return reasons


def _customer_stage(state: dict[str, Any]) -> str:
    customer = state.get("customer")
    if isinstance(customer, dict):
        stage = customer.get("stage")
        if isinstance(stage, str) and stage:
            return stage
    return "unknown"


def _opportunity_stage(state: dict[str, Any]) -> str:
    opportunity = state.get("opportunity")
    if isinstance(opportunity, dict):
        stage = opportunity.get("stage")
        if isinstance(stage, str) and stage:
            return stage
    return "unknown"


def _reply_needed(state: dict[str, Any]) -> bool:
    reply_needed = state.get("reply_needed")
    return isinstance(reply_needed, dict) and reply_needed.get("needed") is True


def _attention_records(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for projection in context["reply_needed"]:
        if not _reply_needed(projection.state):
            continue
        reply_needed = _dict_child(projection.state, "reply_needed")
        records.append(
            {
                "customer_id": _as_int(projection.state.get("customer_id")),
                "conversation_id": _as_int(projection.state.get("conversation_id")),
                "reason": reply_needed.get("reason") or "reply_needed",
                "confidence": float(reply_needed.get("confidence") or 0.5),
                "source_refs": list(projection.source_refs),
            }
        )
    return records


def _order_records(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for projection in context["commercial_states"]:
        state = _dict_child(projection.state, "state")
        if state.get("state_type") != "order":
            continue
        product_refs = _string_list(state.get("product_refs"))
        records.append(
            {
                "customer_id": _as_int(projection.state.get("customer_id")),
                "conversation_id": _as_int(projection.state.get("conversation_id")),
                "order_ref": state.get("state_ref"),
                "order_state": state.get("state"),
                "product_refs": product_refs,
                "amount": _as_number(state.get("amount")),
                "channel": state.get("channel") or "unknown",
                "source_refs": list(projection.source_refs),
            }
        )
    return records


def _hot_customer_records(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> list[dict[str, Any]]:
    attention_by_customer = {
        item["customer_id"]: item
        for item in _attention_records(context)
        if item.get("customer_id") is not None
    }
    records: list[dict[str, Any]] = []
    for projection in context["customers"]:
        customer = _dict_child(projection.state, "customer")
        customer_id = _customer_id(projection)
        if customer_id not in attention_by_customer:
            continue
        records.append(
            {
                "customer_id": customer_id,
                "conversation_id": attention_by_customer[customer_id]["conversation_id"],
                "stage": customer.get("stage") or "unknown",
                "attention_state": customer.get("attention_state") or "needs_reply",
                "source_refs": _unique(
                    [
                        *projection.source_refs,
                        *attention_by_customer[customer_id]["source_refs"],
                    ]
                ),
            }
        )
    return records


def _stalled_records(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for projection in context["opportunities"]:
        opportunity = _dict_child(projection.state, "opportunity")
        if opportunity.get("stage") != "stalled":
            continue
        records.append(
            {
                "customer_id": _as_int(projection.state.get("customer_id")),
                "conversation_id": _as_int(projection.state.get("conversation_id")),
                "opportunity_ref": opportunity.get("opportunity_ref"),
                "product_refs": _string_list(opportunity.get("product_refs")),
                "stage": "stalled",
                "source_refs": list(projection.source_refs),
            }
        )
    return records


def _source_freshness_records(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for projections in context.values():
        for projection in projections:
            records.append(
                {
                    "projection_ref": projection.projection_ref,
                    "projection_type": projection.projection_type,
                    "source_ref_count": len(projection.source_refs),
                    "degraded_reasons": list(projection.degraded_reasons),
                }
            )
    return records


def _investigation_findings(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> list[BIInvestigationFinding]:
    findings: list[BIInvestigationFinding] = []
    for record in _attention_records(context):
        findings.append(
            BIInvestigationFinding(
                finding_ref=f"attention:{record['conversation_id']}",
                finding_type="attention_queue",
                severity="medium",
                title="Conversation needs attention",
                summary="OQIM Intelligence marks this conversation as needing a seller reply.",
                source_refs=record["source_refs"],
                confidence=float(record.get("confidence") or 0.72),
                suggested_action="Review the conversation or let Seller Agent prepare a grounded reply.",
            )
        )
    for record in _stalled_records(context):
        findings.append(
            BIInvestigationFinding(
                finding_ref=f"stalled:{record['opportunity_ref']}",
                finding_type="stalled_opportunity",
                severity="medium",
                title="Opportunity is stalled",
                summary="OQIM Intelligence marks this opportunity stage as stalled.",
                source_refs=record["source_refs"],
                confidence=0.82,
                suggested_action="Create a seller-approved sales follow-up or promoter reactivation proposal.",
            )
        )
    for projections in context.values():
        for projection in projections:
            if not projection.degraded_reasons:
                continue
            findings.append(
                BIInvestigationFinding(
                    finding_ref=f"degraded:{projection.projection_ref}",
                    finding_type="data_quality",
                    severity="high",
                    title="Projection is degraded",
                    summary="A canonical projection has degraded reasons and should be reviewed before relying on it.",
                    source_refs=projection.source_refs or [projection.projection_ref],
                    confidence=0.9,
                    suggested_action="Ask the seller to review or re-run the owning workflow.",
                )
            )
    return findings


def _fix_candidates(
    findings: list[BIInvestigationFinding],
) -> list[BIInvestigationFixCandidate]:
    candidates: list[BIInvestigationFixCandidate] = []
    for finding in findings:
        if finding.finding_type == "data_quality":
            candidates.append(
                BIInvestigationFixCandidate(
                    target_ref=finding.finding_ref.removeprefix("degraded:"),
                    proposal_type="customer_state_fix_candidate",
                    proposed_value={
                        "review_reason": "projection_degraded",
                        "finding_ref": finding.finding_ref,
                    },
                    evidence_refs=finding.source_refs,
                    risk_tier="medium",
                )
            )
        if finding.finding_type == "stalled_opportunity":
            candidates.append(
                BIInvestigationFixCandidate(
                    target_ref=finding.finding_ref.removeprefix("stalled:"),
                    proposal_type="commercial_action_proposal_candidate",
                    proposed_value={
                        "action_type": "schedule_sales_follow_up",
                        "reason_code": "stalled_opportunity_needs_follow_up",
                    },
                    evidence_refs=finding.source_refs,
                    risk_tier="medium",
                )
            )
    return candidates


def _promoter_candidates_from_context(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
    *,
    max_candidates: int,
) -> list[PromoterCandidateInput]:
    conversation_by_entity = _conversation_by_entity(context)
    candidates: list[PromoterCandidateInput] = []
    for projection in context["customers"]:
        customer = _dict_child(projection.state, "customer")
        customer_id = _customer_id(projection)
        conversation_id = _as_int(customer.get("conversation_id")) or conversation_by_entity.get(
            projection.entity_ref
        )
        if customer_id is None or conversation_id is None:
            continue
        candidates.append(
            PromoterCandidateInput(
                customer_id=customer_id,
                conversation_id=conversation_id,
                stage=str(customer.get("stage") or "unknown"),
                source_refs=list(projection.source_refs) or [projection.projection_ref],
                opt_out=bool(customer.get("opt_out") or customer.get("do_not_contact")),
                contact_count_7d=int(customer.get("promoter_contact_count_7d") or 0),
                customer_ref=customer.get("customer_ref"),
            )
        )
        if len(candidates) >= max_candidates:
            break
    return candidates


def _conversation_by_entity(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for family in ("opportunities", "reply_needed", "commercial_states", "follow_ups"):
        for projection in context[family]:
            conversation_id = _as_int(projection.state.get("conversation_id"))
            if conversation_id is not None:
                result.setdefault(projection.entity_ref, conversation_id)
    return result


def _source_refs_from_context(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
    initial: list[str],
) -> list[str]:
    return _unique(
        [
            *initial,
            *[
                ref
                for projections in context.values()
                for projection in projections
                for ref in projection.source_refs
            ],
        ]
    )


def _degraded_reasons_from_context(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> list[str]:
    return _unique(
        [
            reason
            for projections in context.values()
            for projection in projections
            for reason in projection.degraded_reasons
        ]
    )


def _freshness(*, source_refs: list[str], degraded_reasons: list[str]) -> str:
    if not source_refs:
        return "degraded"
    if "no_projection_data" in set(degraded_reasons):
        return "degraded"
    if degraded_reasons:
        return "projection_partial"
    return "projection_current"


def _confidence(*, source_refs: list[str], degraded_reasons: list[str]) -> float:
    if not source_refs:
        return 0.4
    if degraded_reasons:
        return 0.68
    return 0.9


def _counter_records(counter: Any) -> list[dict[str, Any]]:
    if not isinstance(counter, dict):
        return []
    return [
        {"key": key, "count": value}
        for key, value in sorted(counter.items(), key=lambda item: str(item[0]))
    ]


def _breakdown_records(breakdown: Any) -> list[dict[str, Any]]:
    if not isinstance(breakdown, dict):
        return []
    return [
        {"key": key, **value}
        for key, value in sorted(breakdown.items(), key=lambda item: str(item[0]))
        if isinstance(value, dict)
    ]


def _bump_breakdown(
    breakdown: dict[str, dict[str, int]],
    key: str,
    metric: str,
) -> None:
    bucket = breakdown.setdefault(key, {"orders": 0, "opportunities": 0})
    bucket[metric] = bucket.get(metric, 0) + 1


def _dict_child(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _as_number(value: Any) -> int | float | None:
    if isinstance(value, int | float):
        return value
    return None


def _customer_id(projection: BusinessBrainProjection) -> int | None:
    customer = _dict_child(projection.state, "customer")
    value = _as_int(customer.get("customer_id")) or _as_int(
        projection.state.get("customer_id")
    )
    if value is not None:
        return value
    parts = projection.entity_ref.split(":", 1)
    if len(parts) == 2 and parts[0] == "customer":
        return _as_int(parts[1])
    return None


def _context_projection_count(
    context: dict[str, tuple[BusinessBrainProjection, ...]],
) -> int:
    return sum(len(projections) for projections in context.values())


def _insight_id(request: BIInsightRequest) -> str:
    raw = f"{request.workspace_id}:{request.question_kind}:{request.correlation_id}"
    return uuid.uuid5(uuid.NAMESPACE_URL, raw).hex


def _policy_ref(workspace_id: int) -> str:
    return f"promoter:policy:{workspace_id}"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
