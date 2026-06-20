from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.modules.action_runtime.contracts import (
    ActionRuntimeApprovalInput,
    ActionRuntimeDecision,
    ActionRuntimeDraftEditInput,
    ActionRuntimeExecution,
    ActionRuntimeInbox,
    ActionRuntimePolicy,
    ActionRuntimePolicyInput,
    ActionRuntimeRejectInput,
    ActionRuntimeRequeueInput,
    IntegrationCapability,
    IntegrationCapabilityInput,
    OwnerTaskItem,
    OwnerTaskProjection,
)
from app.modules.agent_runtime_events.contracts import AgentRunEventInput
from app.modules.agent_runtime_events.service import AgentRuntimeEventService
from app.modules.agent_talking.contracts import TalkBundle, TalkBundleExecutionResult
from app.modules.agent_talking.service import TalkBundleService
from app.modules.brain.agent_document import AgentDocumentBuilderService
from app.modules.commercial_spine.contracts import (
    BusinessBrainProjection,
    CommercialActionProposal,
    CommercialDecisionTrace,
    CommercialEvent,
)
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.telegram_tools.contracts import (
    TELEGRAM_EDIT_MESSAGE,
    TELEGRAM_SEND_MESSAGE,
    TELEGRAM_TOOL_SCOPES,
    TelegramToolResult,
)
from app.modules.telegram_tools.runtime import TelegramToolRuntime
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import (
    ToolGrantNotFoundError,
    ToolGrantService,
)
from app.modules.triggers.contracts import TriggerInput
from app.modules.triggers.service import TriggerNotFoundError, TriggerService
from app.modules.workspace_os.custom_agent import (
    CustomAgentPackageInput,
    CustomAgentPackageService,
)
from app.services.channel_adapter_contract import ChannelOutboundMedia


class ActionRuntimeDelivery(Protocol):
    async def deliver_message(
        self,
        conversation_id: int,
        text: str,
        *,
        db: AsyncSession,
        workspace_id: int | None = None,
        action_record_id: int | None = None,
        client_idempotency_key: str | None = None,
        message_id: int | None = None,
    ) -> Any: ...

    async def deliver_media(
        self,
        conversation_id: int,
        media: ChannelOutboundMedia,
        *,
        caption: str | None = None,
        db: AsyncSession,
        workspace_id: int | None = None,
        action_record_id: int | None = None,
        client_idempotency_key: str | None = None,
        message_id: int | None = None,
    ) -> Any: ...


INTEGRATION_ACTION_TYPES = {
    "create_calendar_event",
    "send_payment_link",
    "check_payment",
    "create_delivery_order",
    "export_customer",
    "call_integration",
}

LOCAL_ACTION_TYPES = {
    "send_reply",
    "send_status_message",
    "edit_reply",
    "edit_sent_reply",
    "send_catalog_media",
    "create_business_task",
    "schedule_sales_follow_up",
    "agent.create_custom_package",
    "agent.update_tool_grant",
    "agent.update_trigger",
    "agent.update_owner_config",
}

OWNER_TASK_ACTION_TYPES = {
    "create_business_task",
    "schedule_sales_follow_up",
    "check_payment",
    "create_delivery_order",
}

EDITABLE_DRAFT_ACTION_TYPES = {
    "send_reply",
    "send_status_message",
    "edit_reply",
    "edit_sent_reply",
    "promoter_outreach",
}

TELEGRAM_STATUS_MESSAGE_SIDE_EFFECT = "telegram.send_status_message"

EXECUTOR_EVENT_ACTION_TYPES = {
    "send_reply",
    "send_status_message",
    "edit_reply",
    "edit_sent_reply",
    "send_catalog_media",
}


class ActionRuntimeTelegramTools(Protocol):
    async def send_message(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        conversation_id: int,
        text: str,
        correlation_id: str,
        action_record_id: int | None = None,
        idempotency_key: str | None = None,
        reply_to_message_ref: str | None = None,
        delivery_delay_seconds: float | None = None,
        typing_indicator: bool = True,
        online_tail_seconds: float = 0.0,
    ) -> TelegramToolResult: ...

    async def edit_message(
        self,
        *,
        workspace_id: int,
        agent_id: int,
        local_message_id: int,
        text: str,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> TelegramToolResult: ...


class ActionRuntimeService:
    def __init__(
        self,
        repository: CommercialSpineRepository,
        *,
        delivery: ActionRuntimeDelivery | None = None,
        telegram_tools: ActionRuntimeTelegramTools | None = None,
    ) -> None:
        self._repository = repository
        self._delivery = delivery
        self._telegram_tools = telegram_tools

    async def set_policy(self, payload: ActionRuntimePolicyInput) -> ActionRuntimePolicy:
        policy = ActionRuntimePolicy(
            workspace_id=payload.workspace_id,
            enabled=payload.enabled,
            confidence_threshold=payload.confidence_threshold,
            low_risk_allowlist=list(dict.fromkeys(payload.low_risk_allowlist)),
            quiet_hours=dict(payload.quiet_hours),
            escalation_destination=payload.escalation_destination,
            source_refs=_source_refs(payload.source_refs, "action_runtime:policy"),
            correlation_id=payload.correlation_id,
        )
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=_policy_ref(payload.workspace_id),
                workspace_id=payload.workspace_id,
                projection_type="action_runtime_policy",
                entity_ref=f"workspace:{payload.workspace_id}",
                state=policy.model_dump(mode="json"),
                source_refs=policy.source_refs,
            )
        )
        return policy

    async def get_policy(self, *, workspace_id: int) -> ActionRuntimePolicy:
        projection = await self._repository.get_projection(
            workspace_id=workspace_id,
            projection_ref=_policy_ref(workspace_id),
        )
        if projection is None:
            return ActionRuntimePolicy(
                workspace_id=workspace_id,
                enabled=False,
                confidence_threshold=0.95,
                low_risk_allowlist=[],
                quiet_hours={},
                escalation_destination="in_app",
                source_refs=["action_runtime:default_policy"],
                correlation_id="action_runtime:default_policy",
            )
        return ActionRuntimePolicy.model_validate(projection.state)

    async def register_capability(
        self,
        payload: IntegrationCapabilityInput,
    ) -> IntegrationCapability:
        capability = IntegrationCapability(
            workspace_id=payload.workspace_id,
            capability_ref=payload.capability_ref,
            integration_kind=payload.integration_kind,
            enabled=payload.enabled,
            allowed_action_types=list(dict.fromkeys(payload.allowed_action_types)),
            source_refs=_source_refs(payload.source_refs, payload.capability_ref),
            correlation_id=payload.correlation_id,
        )
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=_capability_ref(capability.capability_ref),
                workspace_id=payload.workspace_id,
                projection_type="action_runtime_capability",
                entity_ref=f"integration:{payload.integration_kind}",
                state=capability.model_dump(mode="json"),
                source_refs=capability.source_refs,
            )
        )
        return capability

    async def inbox(
        self,
        *,
        workspace_id: int,
        states: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> ActionRuntimeInbox:
        requested_states = states or (
            "proposed",
            "waiting_approval",
            "approved",
            "executing",
            "blocked",
            "failed",
        )
        items = await self._repository.list_action_proposals(
            workspace_id=workspace_id,
            lifecycle_states=requested_states,
            limit=limit,
        )
        return ActionRuntimeInbox(workspace_id=workspace_id, items=list(items))

    async def owner_tasks(
        self,
        *,
        workspace_id: int,
        limit: int = 100,
    ) -> OwnerTaskProjection:
        items = await self._repository.list_action_proposals(
            workspace_id=workspace_id,
            lifecycle_states=(
                "proposed",
                "waiting_approval",
                "approved",
                "executing",
                "executed",
                "blocked",
                "failed",
                "rejected",
                "cancelled",
                "expired",
            ),
            limit=limit,
        )
        now = datetime.now(UTC)
        task_items = [
            _owner_task_from_proposal(proposal, now=now)
            for proposal in items
            if _is_owner_task_proposal(proposal)
        ]
        proposed = [item for item in task_items if item.state == "proposed"]
        counts = {
            "today": sum(1 for item in task_items if item.due_bucket == "today"),
            "overdue": sum(1 for item in task_items if item.due_bucket == "overdue"),
            "upcoming": sum(1 for item in task_items if item.due_bucket == "upcoming"),
            "completed": sum(1 for item in task_items if item.state == "completed"),
            "proposed": len(proposed),
        }
        return OwnerTaskProjection(
            workspace_id=workspace_id,
            items=task_items,
            proposed=proposed,
            counts=counts,
        )

    async def snooze_task(
        self,
        *,
        workspace_id: int,
        proposal_id: str,
        actor_ref: str,
        correlation_id: str,
        due_at: str | None = None,
    ) -> OwnerTaskItem:
        proposal = await self._require_proposal(
            workspace_id=workspace_id,
            proposal_id=proposal_id,
        )
        if not _is_owner_task_proposal(proposal):
            raise ValueError("owner_task_not_found")
        next_due = _parse_due_at(due_at) or (datetime.now(UTC) + timedelta(days=1))
        updated = await self._update_proposal(
            proposal,
            lifecycle_state="approved",
            reason_code="owner_snoozed_task",
            payload_patch={
                "owner_task": {
                    **_owner_task_payload(proposal.payload),
                    "due_at": next_due.isoformat(),
                    "snoozed_by": actor_ref,
                    "snooze_correlation_id": correlation_id,
                }
            },
            requires_approval=False,
        )
        await self._trace(
            proposal=updated,
            correlation_id=correlation_id,
            reason_code="owner_snoozed_task",
        )
        return _owner_task_from_proposal(updated, now=datetime.now(UTC))

    async def process_proposal(
        self,
        *,
        workspace_id: int,
        proposal_id: str,
        correlation_id: str | None = None,
    ) -> ActionRuntimeDecision:
        proposal = await self._require_proposal(
            workspace_id=workspace_id,
            proposal_id=proposal_id,
        )
        existing_execution = await self._existing_execution(proposal)
        if proposal.lifecycle_state == "executed" and existing_execution is not None:
            return ActionRuntimeDecision(
                workspace_id=workspace_id,
                proposal_id=proposal_id,
                state="executed",
                reason_code=existing_execution.reason_code,
                allowed_to_execute=True,
                execution=existing_execution,
            )

        policy = await self.get_policy(workspace_id=workspace_id)
        run_correlation_id = correlation_id or proposal.correlation_id or proposal.proposal_id

        if not policy.enabled:
            return await self._escalate(
                proposal,
                policy=policy,
                state="waiting_approval",
                reason_code="action_policy_disabled",
                correlation_id=run_correlation_id,
            )
        if proposal.requires_approval or proposal.risk_level in {"high", "critical"}:
            return await self._escalate(
                proposal,
                policy=policy,
                state="waiting_approval",
                reason_code="risk_requires_approval",
                correlation_id=run_correlation_id,
            )
        if proposal.confidence < policy.confidence_threshold:
            return await self._escalate(
                proposal,
                policy=policy,
                state="waiting_approval",
                reason_code="confidence_below_threshold",
                correlation_id=run_correlation_id,
            )
        if proposal.action_type not in set(policy.low_risk_allowlist):
            return await self._escalate(
                proposal,
                policy=policy,
                state="waiting_approval",
                reason_code="action_not_allowlisted",
                correlation_id=run_correlation_id,
            )
        if bool(policy.quiet_hours.get("active")):
            return await self._escalate(
                proposal,
                policy=policy,
                state="waiting_approval",
                reason_code="quiet_hours_active",
                correlation_id=run_correlation_id,
            )

        integration_reason = await self._integration_block_reason(proposal)
        if integration_reason is not None:
            blocked = await self._update_proposal(
                proposal,
                lifecycle_state="blocked",
                reason_code=integration_reason,
                payload_patch={
                    "policy_decision": {
                        "state": "blocked",
                        "reason_code": integration_reason,
                    }
                },
            )
            await self._trace(
                proposal=blocked,
                correlation_id=run_correlation_id,
                reason_code=integration_reason,
            )
            return ActionRuntimeDecision(
                workspace_id=workspace_id,
                proposal_id=proposal_id,
                state="blocked",
                reason_code=integration_reason,
                allowed_to_execute=False,
            )

        approved = await self._update_proposal(
            proposal,
            lifecycle_state="approved",
            reason_code="autopilot_policy_allowed",
            payload_patch={
                "policy_decision": {
                    "state": "approved",
                    "reason_code": "autopilot_policy_allowed",
                }
            },
        )
        execution = await self.execute(
            workspace_id=workspace_id,
            proposal_id=approved.proposal_id,
            correlation_id=run_correlation_id,
        )
        return ActionRuntimeDecision(
            workspace_id=workspace_id,
            proposal_id=proposal_id,
            state=execution.status if execution.status in {"executed", "failed"} else "blocked",
            reason_code=execution.reason_code,
            allowed_to_execute=True,
            execution=execution,
        )

    async def approve(
        self,
        *,
        workspace_id: int,
        proposal_id: str,
        actor_ref: str,
        correlation_id: str,
    ) -> CommercialActionProposal:
        return await self.approve_input(
            ActionRuntimeApprovalInput(
                workspace_id=workspace_id,
                proposal_id=proposal_id,
                actor_ref=actor_ref,
                correlation_id=correlation_id,
            )
        )

    async def approve_input(
        self,
        payload: ActionRuntimeApprovalInput,
    ) -> CommercialActionProposal:
        proposal = await self._require_proposal(
            workspace_id=payload.workspace_id,
            proposal_id=payload.proposal_id,
        )
        approval_reason = "owner_approved"
        approved = await self._update_proposal(
            proposal,
            lifecycle_state="approved",
            reason_code=approval_reason,
            payload_patch={
                "approval": {
                    "actor_ref": payload.actor_ref,
                    "correlation_id": payload.correlation_id,
                }
            },
        )
        await self._trace(
            proposal=approved,
            correlation_id=payload.correlation_id,
            reason_code=approval_reason,
        )
        return approved

    async def reject(
        self,
        *,
        workspace_id: int,
        proposal_id: str,
        actor_ref: str,
        reason_code: str,
        correlation_id: str,
    ) -> CommercialActionProposal:
        return await self.reject_input(
            ActionRuntimeRejectInput(
                workspace_id=workspace_id,
                proposal_id=proposal_id,
                actor_ref=actor_ref,
                reason_code=reason_code,
                correlation_id=correlation_id,
            )
        )

    async def reject_input(
        self,
        payload: ActionRuntimeRejectInput,
    ) -> CommercialActionProposal:
        proposal = await self._require_proposal(
            workspace_id=payload.workspace_id,
            proposal_id=payload.proposal_id,
        )
        rejected = await self._update_proposal(
            proposal,
            lifecycle_state="rejected",
            reason_code=payload.reason_code,
            payload_patch={
                "rejection": {
                    "actor_ref": payload.actor_ref,
                    "correlation_id": payload.correlation_id,
                    "reason_code": payload.reason_code,
                }
            },
        )
        await self._trace(
            proposal=rejected,
            correlation_id=payload.correlation_id,
            reason_code=payload.reason_code,
        )
        return rejected

    async def edit_draft_input(
        self,
        payload: ActionRuntimeDraftEditInput,
    ) -> CommercialActionProposal:
        proposal = await self._require_proposal(
            workspace_id=payload.workspace_id,
            proposal_id=payload.proposal_id,
        )
        if proposal.lifecycle_state not in {"proposed", "waiting_approval"}:
            raise ValueError("draft_edit_not_allowed_after_approval")
        if proposal.action_type not in EDITABLE_DRAFT_ACTION_TYPES and _proposal_text(proposal) is None:
            raise ValueError("draft_edit_not_supported")

        draft_text = payload.draft_text.strip()
        if not draft_text:
            raise ValueError("draft_text_missing")
        revision = _int_value(proposal.payload.get("draft_revision")) + 1
        updated = await self._update_proposal(
            proposal,
            lifecycle_state="waiting_approval" if proposal.requires_approval else proposal.lifecycle_state,
            reason_code="owner_edited_draft",
            payload_patch={
                "draft_text": draft_text,
                "reply_text": draft_text,
                "draft_revision": revision,
                "draft_edited": True,
                "draft_edit": {
                    "actor_ref": payload.actor_ref,
                    "correlation_id": payload.correlation_id,
                    "revision": revision,
                },
            },
        )
        await self._trace(
            proposal=updated,
            correlation_id=payload.correlation_id,
            reason_code="owner_edited_draft",
        )
        return updated

    async def execute(
        self,
        *,
        workspace_id: int,
        proposal_id: str,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        proposal = await self._require_proposal(
            workspace_id=workspace_id,
            proposal_id=proposal_id,
        )
        existing = await self._existing_execution(proposal)
        if existing is not None:
            return existing

        attempt = _attempt(proposal)
        if proposal.requires_approval and proposal.lifecycle_state not in {"approved", "executing"}:
            execution = _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="approval_required_before_execution",
                idempotency_key=_execution_idempotency_key(proposal, attempt),
            )
            await self._trace(
                proposal=proposal,
                correlation_id=correlation_id,
                reason_code=execution.reason_code,
            )
            return execution

        run_id = _agent_run_id_from_proposal(proposal)
        if run_id is not None and proposal.action_type in EXECUTOR_EVENT_ACTION_TYPES:
            await self._record_agent_run_executor_started(
                proposal=proposal,
                run_id=run_id,
                attempt=attempt,
                correlation_id=correlation_id,
            )

        if proposal.action_type == "send_reply":
            execution = await self._build_send_reply_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        elif proposal.action_type == "send_status_message":
            execution = await self._build_send_status_message_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        elif proposal.action_type in {"edit_reply", "edit_sent_reply"}:
            execution = await self._build_edit_reply_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        elif proposal.action_type == "send_catalog_media":
            execution = await self._build_catalog_media_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        elif proposal.action_type == "agent.create_custom_package":
            execution = await self._build_custom_agent_package_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        elif proposal.action_type == "agent.update_tool_grant":
            execution = await self._build_agent_tool_grant_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        elif proposal.action_type == "agent.update_trigger":
            execution = await self._build_agent_trigger_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        elif proposal.action_type == "agent.update_owner_config":
            execution = await self._build_owner_config_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        else:
            execution = self._build_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        await self._repository.persist_action_execution(
            execution.model_dump(mode="json")
        )
        target_state = "blocked" if execution.status == "unsupported" else execution.status
        updated = await self._update_proposal(
            proposal,
            lifecycle_state=target_state,
            reason_code=execution.reason_code,
            payload_patch={
                "last_execution_id": execution.execution_id,
                "last_execution_status": execution.status,
            },
        )
        await self._repository.append_event(
            CommercialEvent(
                event_id=f"event:{execution.execution_id}",
                workspace_id=proposal.workspace_id,
                source_type="action_runtime",
                source_ref=f"action_execution:{execution.execution_id}",
                actor_type="system",
                correlation_id=correlation_id,
                idempotency_key=f"event:{execution.idempotency_key}",
                payload={
                    "proposal_id": proposal.proposal_id,
                    "action_type": proposal.action_type,
                    "status": execution.status,
                    "reason_code": execution.reason_code,
                },
            )
        )
        await self._trace(
            proposal=updated,
            correlation_id=correlation_id,
            reason_code=execution.reason_code,
        )
        if run_id is not None and proposal.action_type in EXECUTOR_EVENT_ACTION_TYPES:
            await self._record_agent_run_executor_finished(
                proposal=updated,
                execution=execution,
                run_id=run_id,
                attempt=attempt,
                correlation_id=correlation_id,
            )
        return execution

    async def executions(
        self,
        *,
        workspace_id: int,
        proposal_id: str,
        limit: int = 50,
    ) -> tuple[ActionRuntimeExecution, ...]:
        rows = await self._repository.list_action_executions(
            workspace_id=workspace_id,
            proposal_id=proposal_id,
            limit=limit,
        )
        return tuple(ActionRuntimeExecution.model_validate(row) for row in rows)

    async def requeue_failed(
        self,
        payload: ActionRuntimeRequeueInput,
    ) -> CommercialActionProposal:
        proposal = await self._require_proposal(
            workspace_id=payload.workspace_id,
            proposal_id=payload.proposal_id,
        )
        if proposal.lifecycle_state != "failed":
            return proposal
        next_attempt = _attempt(proposal) + 1
        merged_payload = {
            **proposal.payload,
            **payload.patch_payload,
            "action_runtime_attempt": next_attempt,
            "requeue": {
                "actor_ref": payload.actor_ref,
                "correlation_id": payload.correlation_id,
            },
        }
        requeued = proposal.model_copy(
            update={
                "lifecycle_state": "approved",
                "reason_code": "requeued_after_failure",
                "payload": merged_payload,
            }
        )
        await self._repository.update_action_proposal(requeued)
        await self._trace(
            proposal=requeued,
            correlation_id=payload.correlation_id,
            reason_code="requeued_after_failure",
        )
        return requeued

    async def _require_proposal(
        self,
        *,
        workspace_id: int,
        proposal_id: str,
    ) -> CommercialActionProposal:
        proposal = await self._repository.get_action_proposal(
            workspace_id=workspace_id,
            proposal_id=proposal_id,
        )
        if proposal is None:
            raise ValueError("action_proposal_not_found")
        return proposal

    async def _existing_execution(
        self,
        proposal: CommercialActionProposal,
    ) -> ActionRuntimeExecution | None:
        row = await self._repository.get_action_execution(
            workspace_id=proposal.workspace_id,
            idempotency_key=_execution_idempotency_key(proposal, _attempt(proposal)),
        )
        if row is None:
            return None
        return ActionRuntimeExecution.model_validate(row)

    async def _escalate(
        self,
        proposal: CommercialActionProposal,
        *,
        policy: ActionRuntimePolicy,
        state: str,
        reason_code: str,
        correlation_id: str,
    ) -> ActionRuntimeDecision:
        updated = await self._update_proposal(
            proposal,
            lifecycle_state=state,
            reason_code=reason_code,
            requires_approval=True,
            payload_patch={
                "policy_decision": {
                    "state": state,
                    "reason_code": reason_code,
                }
            },
        )
        notification_ref = await self._notify(
            updated,
            channel=policy.escalation_destination,
            reason_code=reason_code,
            correlation_id=correlation_id,
        )
        await self._trace(
            proposal=updated,
            correlation_id=correlation_id,
            reason_code=reason_code,
            changed_projection_refs=[notification_ref],
        )
        return ActionRuntimeDecision(
            workspace_id=proposal.workspace_id,
            proposal_id=proposal.proposal_id,
            state=state,  # type: ignore[arg-type]
            reason_code=reason_code,
            allowed_to_execute=False,
            notification_refs=[notification_ref],
        )

    async def _notify(
        self,
        proposal: CommercialActionProposal,
        *,
        channel: str,
        reason_code: str,
        correlation_id: str,
    ) -> str:
        projection_ref = f"action_runtime:notification:{proposal.proposal_id}:{channel}"
        await self._repository.upsert_projection(
            BusinessBrainProjection(
                projection_ref=projection_ref,
                workspace_id=proposal.workspace_id,
                projection_type="action_runtime_notification",
                entity_ref=f"proposal:{proposal.proposal_id}",
                state={
                    "channel": channel,
                    "status": "queued",
                    "proposal_id": proposal.proposal_id,
                    "reason_code": reason_code,
                    "correlation_id": correlation_id,
                },
                source_refs=[f"proposal:{proposal.proposal_id}"],
            )
        )
        return projection_ref

    async def _integration_block_reason(
        self,
        proposal: CommercialActionProposal,
    ) -> str | None:
        if proposal.action_type not in INTEGRATION_ACTION_TYPES:
            return None
        projections = await self._repository.list_projections(
            workspace_id=proposal.workspace_id,
            projection_type="action_runtime_capability",
            limit=250,
        )
        capabilities = [
            IntegrationCapability.model_validate(item.state)
            for item in projections
            if proposal.action_type
            in set(item.state.get("allowed_action_types") or [])
        ]
        if not capabilities:
            return "missing_integration_capability"
        if not any(capability.enabled for capability in capabilities):
            return "integration_capability_disabled"
        return None

    def _build_execution(
        self,
        proposal: CommercialActionProposal,
        *,
        attempt: int,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        idempotency_key = _execution_idempotency_key(proposal, attempt)
        if bool(proposal.payload.get("force_failure")):
            return ActionRuntimeExecution(
                execution_id=_execution_id(proposal, attempt),
                workspace_id=proposal.workspace_id,
                conversation_id=proposal.conversation_id,
                customer_id=proposal.customer_id,
                proposal_id=proposal.proposal_id,
                action_type=proposal.action_type,
                status="failed",
                reason_code="executor_failed",
                idempotency_key=idempotency_key,
                attempt=attempt,
                payload={
                    "side_effect": proposal.action_type,
                    "correlation_id": correlation_id,
                },
                error="forced_failure",
            )
        supported = LOCAL_ACTION_TYPES | INTEGRATION_ACTION_TYPES
        if proposal.action_type not in supported:
            return ActionRuntimeExecution(
                execution_id=_execution_id(proposal, attempt),
                workspace_id=proposal.workspace_id,
                conversation_id=proposal.conversation_id,
                customer_id=proposal.customer_id,
                proposal_id=proposal.proposal_id,
                action_type=proposal.action_type,
                status="unsupported",
                reason_code="unsupported_action_type",
                idempotency_key=idempotency_key,
                attempt=attempt,
                payload={
                    "side_effect": proposal.action_type,
                    "correlation_id": correlation_id,
                },
                error="unsupported_action_type",
            )
        return ActionRuntimeExecution(
            execution_id=_execution_id(proposal, attempt),
            workspace_id=proposal.workspace_id,
            conversation_id=proposal.conversation_id,
            customer_id=proposal.customer_id,
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            status="executed",
            reason_code="executor_completed",
            idempotency_key=idempotency_key,
            attempt=attempt,
            delivery_state="confirmed",
            payload={
                "side_effect": proposal.action_type,
                "correlation_id": correlation_id,
                "proposal_payload": dict(proposal.payload),
            },
        )

    async def _build_send_reply_execution(
        self,
        proposal: CommercialActionProposal,
        *,
        attempt: int,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        idempotency_key = _execution_idempotency_key(proposal, attempt)
        if bool(proposal.payload.get("force_failure")):
            return _forced_failure_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
        draft_text = _proposal_text(proposal)
        if draft_text is None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="draft_text_missing",
                idempotency_key=idempotency_key,
            )

        agent_id = await self._resolve_agent_id(proposal, fallback_agent_type="seller")
        if agent_id is None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="agent_tool_owner_missing",
                idempotency_key=idempotency_key,
            )

        talk_bundle = _proposal_talk_bundle(proposal)
        if talk_bundle is not None:
            if talk_bundle.workspace_id != proposal.workspace_id:
                return _blocked_execution(
                    proposal,
                    attempt=attempt,
                    correlation_id=correlation_id,
                    reason_code="talk_bundle_workspace_mismatch",
                    idempotency_key=idempotency_key,
                )
            if talk_bundle.conversation_id != proposal.conversation_id:
                return _blocked_execution(
                    proposal,
                    attempt=attempt,
                    correlation_id=correlation_id,
                    reason_code="talk_bundle_conversation_mismatch",
                    idempotency_key=idempotency_key,
                )
            if talk_bundle.agent_id != agent_id:
                talk_bundle = talk_bundle.model_copy(update={"agent_id": agent_id})
            result = await TalkBundleService(
                self._repository.session,
                delivery=self._delivery,
            ).execute_bundle(
                bundle=talk_bundle,
                correlation_id=correlation_id,
                action_record_id=_proposal_int(proposal.payload, "action_record_id"),
            )
            return _talk_bundle_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                result=result,
            )

        tool_result = await self._telegram().send_message(
            workspace_id=proposal.workspace_id,
            agent_id=agent_id,
            conversation_id=proposal.conversation_id,
            text=draft_text,
            correlation_id=correlation_id,
            action_record_id=_proposal_int(proposal.payload, "action_record_id"),
            idempotency_key=idempotency_key,
            reply_to_message_ref=_optional_string(proposal.payload.get("reply_to_message_ref")),
            delivery_delay_seconds=_float_value_or_none(proposal.payload.get("delivery_delay_seconds")),
            typing_indicator=proposal.payload.get("typing_indicator") is not False,
            online_tail_seconds=_float_value(proposal.payload.get("online_tail_seconds")),
        )
        return _telegram_tool_execution(
            proposal,
            attempt=attempt,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            tool_result=tool_result,
            side_effect=TELEGRAM_SEND_MESSAGE,
        )

    async def _build_send_status_message_execution(
        self,
        proposal: CommercialActionProposal,
        *,
        attempt: int,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        idempotency_key = _execution_idempotency_key(proposal, attempt)
        if bool(proposal.payload.get("force_failure")):
            return _forced_failure_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
        status_text = _proposal_text(proposal)
        if status_text is None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="status_text_missing",
                idempotency_key=idempotency_key,
            )
        block_reason = _status_message_block_reason(proposal, status_text)
        if block_reason is not None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code=block_reason,
                idempotency_key=idempotency_key,
            )
        duplicate_reason = await self._status_message_duplicate_block_reason(proposal)
        if duplicate_reason is not None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code=duplicate_reason,
                idempotency_key=idempotency_key,
            )

        agent_id = await self._resolve_agent_id(proposal, fallback_agent_type="seller")
        if agent_id is None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="agent_tool_owner_missing",
                idempotency_key=idempotency_key,
            )

        tool_result = await self._telegram().send_message(
            workspace_id=proposal.workspace_id,
            agent_id=agent_id,
            conversation_id=proposal.conversation_id,
            text=status_text,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        return _telegram_tool_execution(
            proposal,
            attempt=attempt,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            tool_result=tool_result,
            side_effect=TELEGRAM_STATUS_MESSAGE_SIDE_EFFECT,
        )

    async def _status_message_duplicate_block_reason(
        self,
        proposal: CommercialActionProposal,
    ) -> str | None:
        run_ref = _status_message_run_ref(proposal)
        if run_ref is None:
            return None
        lane = _status_message_lane(proposal)
        text_key = _status_message_text_key(proposal)
        candidates = await self._repository.list_action_proposals(
            workspace_id=proposal.workspace_id,
            conversation_id=proposal.conversation_id,
            action_type="send_status_message",
            lifecycle_states=("executed",),
            limit=100,
        )
        for candidate in candidates:
            if candidate.proposal_id == proposal.proposal_id:
                continue
            if _status_message_run_ref(candidate) != run_ref:
                continue
            same_lane = _status_message_lane(candidate) == lane
            same_text = bool(text_key) and _status_message_text_key(candidate) == text_key
            if not same_lane and not same_text:
                continue
            executions = await self._repository.list_action_executions(
                workspace_id=proposal.workspace_id,
                proposal_id=candidate.proposal_id,
                limit=10,
            )
            if any(_is_executed_status_message(row) for row in executions):
                return "status_message_duplicate_in_run"
        return None

    async def _build_edit_reply_execution(
        self,
        proposal: CommercialActionProposal,
        *,
        attempt: int,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        idempotency_key = _execution_idempotency_key(proposal, attempt)
        if bool(proposal.payload.get("force_failure")):
            return _forced_failure_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
        draft_text = _proposal_text(proposal)
        if draft_text is None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="draft_text_missing",
                idempotency_key=idempotency_key,
            )
        local_message_id = _proposal_int(
            proposal.payload,
            "local_message_id",
            "message_id",
            "target_message_id",
        )
        if local_message_id is None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="local_message_id_missing",
                idempotency_key=idempotency_key,
            )

        agent_id = await self._resolve_agent_id(proposal, fallback_agent_type="seller")
        if agent_id is None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="agent_tool_owner_missing",
                idempotency_key=idempotency_key,
            )

        tool_result = await self._telegram().edit_message(
            workspace_id=proposal.workspace_id,
            agent_id=agent_id,
            local_message_id=local_message_id,
            text=draft_text,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        return _telegram_tool_execution(
            proposal,
            attempt=attempt,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            tool_result=tool_result,
            side_effect=TELEGRAM_EDIT_MESSAGE,
        )

    async def _build_catalog_media_execution(
        self,
        proposal: CommercialActionProposal,
        *,
        attempt: int,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        idempotency_key = _execution_idempotency_key(proposal, attempt)
        if self._delivery is None:
            return ActionRuntimeExecution(
                execution_id=_execution_id(proposal, attempt),
                workspace_id=proposal.workspace_id,
                conversation_id=proposal.conversation_id,
                customer_id=proposal.customer_id,
                proposal_id=proposal.proposal_id,
                action_type=proposal.action_type,
                status="failed",
                reason_code="delivery_runtime_unavailable",
                idempotency_key=idempotency_key,
                attempt=attempt,
                payload={
                    "side_effect": proposal.action_type,
                    "correlation_id": correlation_id,
                    "proposal_payload": dict(proposal.payload),
                },
                error="delivery_runtime_unavailable",
            )

        block_reason = _catalog_media_block_reason(proposal)
        if block_reason is not None:
            return ActionRuntimeExecution(
                execution_id=_execution_id(proposal, attempt),
                workspace_id=proposal.workspace_id,
                conversation_id=proposal.conversation_id,
                customer_id=proposal.customer_id,
                proposal_id=proposal.proposal_id,
                action_type=proposal.action_type,
                status="blocked",
                reason_code=block_reason,
                idempotency_key=idempotency_key,
                attempt=attempt,
                payload={
                    "side_effect": proposal.action_type,
                    "correlation_id": correlation_id,
                    "proposal_payload": dict(proposal.payload),
                },
            )

        media = ChannelOutboundMedia(
            url=str(proposal.payload["catalog_media_url"]),
            media_type=str(proposal.payload.get("media_type") or "photo"),
            mime_type=_optional_string(proposal.payload.get("mime_type")),
            file_name=_optional_string(proposal.payload.get("file_name")),
            asset_id=str(proposal.payload["catalog_media_asset_id"]),
        )
        delivery_result = await self._delivery.deliver_media(
            proposal.conversation_id,
            media,
            caption=_optional_string(proposal.payload.get("caption")),
            db=self._repository.session,
            workspace_id=proposal.workspace_id,
            client_idempotency_key=idempotency_key,
        )
        if bool(getattr(delivery_result, "success", False)):
            return ActionRuntimeExecution(
                execution_id=_execution_id(proposal, attempt),
                workspace_id=proposal.workspace_id,
                conversation_id=proposal.conversation_id,
                customer_id=proposal.customer_id,
                proposal_id=proposal.proposal_id,
                action_type=proposal.action_type,
                status="executed",
                reason_code="delivery_confirmed",
                idempotency_key=idempotency_key,
                attempt=attempt,
                delivery_state=getattr(delivery_result, "state", None),
                external_message_id=getattr(delivery_result, "external_message_id", None),
                payload={
                    "side_effect": proposal.action_type,
                    "correlation_id": correlation_id,
                    "proposal_payload": dict(proposal.payload),
                    "media": media.to_sidecar_payload(),
                },
            )
        return ActionRuntimeExecution(
            execution_id=_execution_id(proposal, attempt),
            workspace_id=proposal.workspace_id,
            conversation_id=proposal.conversation_id,
            customer_id=proposal.customer_id,
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            status="failed",
            reason_code="delivery_not_confirmed",
            idempotency_key=idempotency_key,
            attempt=attempt,
            delivery_state=getattr(delivery_result, "state", None),
            payload={
                "side_effect": proposal.action_type,
                "correlation_id": correlation_id,
                "proposal_payload": dict(proposal.payload),
                "media": media.to_sidecar_payload(),
            },
            error=_optional_string(getattr(delivery_result, "error", None)),
        )

    async def _build_custom_agent_package_execution(
        self,
        proposal: CommercialActionProposal,
        *,
        attempt: int,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        idempotency_key = _execution_idempotency_key(proposal, attempt)
        if bool(proposal.payload.get("force_failure")):
            return _forced_failure_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
        package_payload = proposal.payload.get("custom_agent_package")
        if not isinstance(package_payload, dict):
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="custom_agent_package_missing",
                idempotency_key=idempotency_key,
            )
        try:
            custom_input = CustomAgentPackageInput.model_validate(package_payload)
        except ValueError:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="custom_agent_package_invalid",
                idempotency_key=idempotency_key,
            )

        result = await CustomAgentPackageService(self._repository.session).create(
            workspace_id=proposal.workspace_id,
            payload=custom_input,
        )
        agent = result.agent
        return ActionRuntimeExecution(
            execution_id=_execution_id(proposal, attempt),
            workspace_id=proposal.workspace_id,
            conversation_id=proposal.conversation_id,
            customer_id=proposal.customer_id,
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            status="executed",
            reason_code="custom_agent_package_created" if result.created else "custom_agent_package_reused",
            idempotency_key=idempotency_key,
            attempt=attempt,
            payload={
                "side_effect": proposal.action_type,
                "correlation_id": correlation_id,
                "agent": {
                    "id": agent.id,
                    "name": agent.name,
                    "agent_type": agent.agent_type,
                    "package_key": result.package_key,
                    "permission_mode": result.permission_mode,
                    "document_section_count": result.document_section_count,
                    "skill_count": result.skill_count,
                    "tool_grant_count": result.tool_grant_count,
                    "trigger_count": result.trigger_count,
                },
            },
        )

    async def _build_agent_trigger_execution(
        self,
        proposal: CommercialActionProposal,
        *,
        attempt: int,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        idempotency_key = _execution_idempotency_key(proposal, attempt)
        if bool(proposal.payload.get("force_failure")):
            return _forced_failure_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )

        agent_id = _proposal_int(proposal.payload, "agent_id", "owner_agent_id")
        operation = _optional_string(proposal.payload.get("operation"))
        if agent_id is None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="agent_id_missing",
                idempotency_key=idempotency_key,
            )
        if operation not in {"create", "deactivate"}:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="agent_trigger_operation_invalid",
                idempotency_key=idempotency_key,
            )

        agent = await self._repository.session.get(Agent, agent_id)
        if agent is None or agent.workspace_id != proposal.workspace_id:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="agent_not_found",
                idempotency_key=idempotency_key,
            )

        triggers = TriggerService(self._repository.session)
        if operation == "create":
            trigger_payload = proposal.payload.get("trigger")
            if not isinstance(trigger_payload, dict):
                return _blocked_execution(
                    proposal,
                    attempt=attempt,
                    correlation_id=correlation_id,
                    reason_code="agent_trigger_payload_missing",
                    idempotency_key=idempotency_key,
                )
            try:
                trigger_input = TriggerInput.model_validate(
                    {**trigger_payload, "owner_agent_id": agent_id}
                )
            except ValueError:
                return _blocked_execution(
                    proposal,
                    attempt=attempt,
                    correlation_id=correlation_id,
                    reason_code="agent_trigger_payload_invalid",
                    idempotency_key=idempotency_key,
                )
            trigger = await triggers.create(
                workspace_id=proposal.workspace_id,
                payload=trigger_input,
            )
            reason_code = "agent_trigger_upserted"
        else:
            trigger_id = _proposal_int(proposal.payload, "trigger_id")
            if trigger_id is None:
                return _blocked_execution(
                    proposal,
                    attempt=attempt,
                    correlation_id=correlation_id,
                    reason_code="agent_trigger_id_missing",
                    idempotency_key=idempotency_key,
                )
            existing = await triggers.list_for_workspace(
                workspace_id=proposal.workspace_id,
                agent_id=agent_id,
            )
            if not any(item.id == trigger_id for item in existing):
                return _blocked_execution(
                    proposal,
                    attempt=attempt,
                    correlation_id=correlation_id,
                    reason_code="agent_trigger_not_found",
                    idempotency_key=idempotency_key,
                )
            try:
                trigger = await triggers.deactivate(
                    workspace_id=proposal.workspace_id,
                    trigger_id=trigger_id,
                    reason="owner",
                )
            except TriggerNotFoundError:
                return _blocked_execution(
                    proposal,
                    attempt=attempt,
                    correlation_id=correlation_id,
                    reason_code="agent_trigger_not_found",
                    idempotency_key=idempotency_key,
                )
            reason_code = "agent_trigger_deactivated"

        return ActionRuntimeExecution(
            execution_id=_execution_id(proposal, attempt),
            workspace_id=proposal.workspace_id,
            conversation_id=proposal.conversation_id,
            customer_id=proposal.customer_id,
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            status="executed",
            reason_code=reason_code,
            idempotency_key=idempotency_key,
            attempt=attempt,
            payload={
                "side_effect": proposal.action_type,
                "correlation_id": correlation_id,
                "agent_id": agent_id,
                "agent_name": agent.name,
                "operation": operation,
                "trigger": trigger.model_dump(mode="json"),
            },
        )

    async def _build_agent_tool_grant_execution(
        self,
        proposal: CommercialActionProposal,
        *,
        attempt: int,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        idempotency_key = _execution_idempotency_key(proposal, attempt)
        if bool(proposal.payload.get("force_failure")):
            return _forced_failure_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )

        agent_id = _proposal_int(proposal.payload, "agent_id")
        scope = _optional_string(proposal.payload.get("tool_scope"))
        operation = _optional_string(proposal.payload.get("operation"))
        if agent_id is None:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="agent_id_missing",
                idempotency_key=idempotency_key,
            )
        if scope not in TELEGRAM_TOOL_SCOPES:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="unsupported_tool_scope",
                idempotency_key=idempotency_key,
            )
        if operation not in {"grant", "revoke"}:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="tool_grant_operation_invalid",
                idempotency_key=idempotency_key,
            )

        agent = await self._repository.session.get(Agent, agent_id)
        if agent is None or agent.workspace_id != proposal.workspace_id:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="agent_not_found",
                idempotency_key=idempotency_key,
            )

        grants = ToolGrantService(self._repository.session)
        if operation == "grant":
            grant = await grants.grant(
                workspace_id=proposal.workspace_id,
                payload=ToolGrantInput(
                    agent_id=agent_id,
                    scope=scope,
                    granted_by="owner",
                    grant_reason=_optional_string(proposal.payload.get("grant_reason"))
                    or "Owner approved agent tool permission.",
                    audit_metadata={
                        "proposal_id": proposal.proposal_id,
                        "correlation_id": correlation_id,
                    },
                ),
            )
            _sync_agent_tool_scope(agent, scope=scope, operation="grant")
            reason_code = "agent_tool_grant_granted"
        else:
            try:
                grant = await grants.revoke(
                    workspace_id=proposal.workspace_id,
                    agent_id=agent_id,
                    scope=scope,
                    revoked_by="owner",
                )
            except ToolGrantNotFoundError:
                return _blocked_execution(
                    proposal,
                    attempt=attempt,
                    correlation_id=correlation_id,
                    reason_code="tool_grant_not_found",
                    idempotency_key=idempotency_key,
                )
            _sync_agent_tool_scope(agent, scope=scope, operation="revoke")
            reason_code = "agent_tool_grant_revoked"

        return ActionRuntimeExecution(
            execution_id=_execution_id(proposal, attempt),
            workspace_id=proposal.workspace_id,
            conversation_id=proposal.conversation_id,
            customer_id=proposal.customer_id,
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            status="executed",
            reason_code=reason_code,
            idempotency_key=idempotency_key,
            attempt=attempt,
            payload={
                "side_effect": proposal.action_type,
                "correlation_id": correlation_id,
                "agent_id": agent_id,
                "agent_name": agent.name,
                "operation": operation,
                "tool_scope": scope,
                "tool_grant_id": grant.id,
                "active": grant.active,
            },
        )

    async def _build_owner_config_execution(
        self,
        proposal: CommercialActionProposal,
        *,
        attempt: int,
        correlation_id: str,
    ) -> ActionRuntimeExecution:
        """Apply an owner-approved AGENT.md section edit (spike #439).

        Mirrors `_build_agent_tool_grant_execution`: validate the payload, confirm
        the target agent belongs to the proposal's workspace, then write the
        section via AgentDocumentBuilderService.edit_section.
        """
        idempotency_key = _execution_idempotency_key(proposal, attempt)
        if bool(proposal.payload.get("force_failure")):
            return _forced_failure_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )

        op = _optional_string(proposal.payload.get("op"))
        if op != "edit_doc":
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="owner_config_op_invalid",
                idempotency_key=idempotency_key,
            )
        agent_id = _proposal_int(proposal.payload, "agent_id")
        section_key = _optional_string(proposal.payload.get("section_key"))
        body = proposal.payload.get("body")
        if (
            agent_id is None
            or not section_key
            or not isinstance(body, str)
            or not body.strip()
        ):
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="owner_config_fields_missing",
                idempotency_key=idempotency_key,
            )

        agent = await self._repository.session.get(Agent, agent_id)
        if agent is None or agent.workspace_id != proposal.workspace_id:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="agent_not_found",
                idempotency_key=idempotency_key,
            )

        try:
            await AgentDocumentBuilderService(self._repository.session).edit_section(
                workspace_id=proposal.workspace_id,
                agent_id=agent_id,
                section_key=section_key,
                body=body,
            )
        except KeyError:
            return _blocked_execution(
                proposal,
                attempt=attempt,
                correlation_id=correlation_id,
                reason_code="owner_config_section_invalid",
                idempotency_key=idempotency_key,
            )

        return ActionRuntimeExecution(
            execution_id=_execution_id(proposal, attempt),
            workspace_id=proposal.workspace_id,
            conversation_id=proposal.conversation_id,
            customer_id=proposal.customer_id,
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            status="executed",
            reason_code="owner_config_applied",
            idempotency_key=idempotency_key,
            attempt=attempt,
            payload={
                "side_effect": proposal.action_type,
                "correlation_id": correlation_id,
                "agent_id": agent_id,
                "agent_name": agent.name,
                "op": op,
                "section_key": section_key,
            },
        )

    def _telegram(self) -> ActionRuntimeTelegramTools:
        if self._telegram_tools is not None:
            return self._telegram_tools
        return TelegramToolRuntime(
            self._repository.session,
            delivery=self._delivery,
        )

    async def _resolve_agent_id(
        self,
        proposal: CommercialActionProposal,
        *,
        fallback_agent_type: str,
    ) -> int | None:
        explicit_agent_id = _proposal_agent_id(proposal.payload, proposal.source_refs)
        if explicit_agent_id is not None:
            agent = await self._repository.session.get(Agent, explicit_agent_id)
            if (
                agent is not None
                and agent.workspace_id == proposal.workspace_id
                and agent.is_active
            ):
                return agent.id
            return None

        preferred_type = _optional_string(proposal.payload.get("agent_type"))
        preferred_type = preferred_type or fallback_agent_type
        agent = await self._repository.session.scalar(
            select(Agent)
            .where(
                Agent.workspace_id == proposal.workspace_id,
                Agent.is_active.is_(True),
                Agent.agent_type == preferred_type,
            )
            .order_by(Agent.is_default.desc(), Agent.id.asc())
        )
        if agent is not None:
            return agent.id
        agent = await self._repository.session.scalar(
            select(Agent)
            .where(
                Agent.workspace_id == proposal.workspace_id,
                Agent.is_active.is_(True),
                Agent.is_default.is_(True),
            )
            .order_by(Agent.id.asc())
        )
        return agent.id if agent is not None else None

    async def _record_agent_run_executor_started(
        self,
        *,
        proposal: CommercialActionProposal,
        run_id: str,
        attempt: int,
        correlation_id: str,
    ) -> None:
        events = AgentRuntimeEventService(self._repository)
        tool_name = _executor_tool_name(proposal.action_type)
        event_base = _executor_event_base(run_id, proposal.proposal_id, attempt)
        await events.record_event(
            AgentRunEventInput(
                event_id=f"{event_base}:tool-started",
                run_id=run_id,
                workspace_id=proposal.workspace_id,
                event_type="tool.call.started",
                visibility="internal",
                tool_name=tool_name,
                tool_state="called",
                action_proposal_id=proposal.proposal_id,
                source_refs=[f"proposal:{proposal.proposal_id}"],
                payload={
                    "action_type": proposal.action_type,
                    "execution_attempt": attempt,
                },
                correlation_id=correlation_id,
                idempotency_key=f"{proposal.idempotency_key}:agent_run_tool_started:{attempt}",
            )
        )
        await events.record_event(
            AgentRunEventInput(
                event_id=f"{event_base}:owner-started",
                run_id=run_id,
                workspace_id=proposal.workspace_id,
                event_type="owner_progress.created",
                visibility="owner",
                owner_label=_executor_owner_started_label(proposal.action_type),
                owner_detail=_executor_owner_started_detail(proposal.action_type),
                action_proposal_id=proposal.proposal_id,
                source_refs=[f"proposal:{proposal.proposal_id}"],
                correlation_id=correlation_id,
                idempotency_key=f"{proposal.idempotency_key}:agent_run_owner_started:{attempt}",
            )
        )

    async def _record_agent_run_executor_finished(
        self,
        *,
        proposal: CommercialActionProposal,
        execution: ActionRuntimeExecution,
        run_id: str,
        attempt: int,
        correlation_id: str,
    ) -> None:
        events = AgentRuntimeEventService(self._repository)
        tool_state = _executor_tool_state(execution.status)
        event_base = _executor_event_base(run_id, proposal.proposal_id, attempt)
        await events.record_event(
            AgentRunEventInput(
                event_id=f"{event_base}:tool-finished",
                run_id=run_id,
                workspace_id=proposal.workspace_id,
                event_type=f"tool.call.{tool_state}",
                visibility="internal",
                tool_name=_executor_tool_name(proposal.action_type),
                tool_state=tool_state,
                action_proposal_id=proposal.proposal_id,
                source_refs=[
                    f"proposal:{proposal.proposal_id}",
                    f"action_execution:{execution.execution_id}",
                ],
                payload={
                    "action_type": proposal.action_type,
                    "execution_attempt": attempt,
                    "execution_status": execution.status,
                    "delivery_state": execution.delivery_state,
                },
                correlation_id=correlation_id,
                idempotency_key=f"{proposal.idempotency_key}:agent_run_tool_finished:{attempt}",
            )
        )
        await events.record_event(
            AgentRunEventInput(
                event_id=f"{event_base}:owner-finished",
                run_id=run_id,
                workspace_id=proposal.workspace_id,
                event_type="owner_progress.created",
                visibility="owner",
                owner_label=_executor_owner_finished_label(
                    proposal.action_type,
                    execution.status,
                ),
                owner_detail=_executor_owner_finished_detail(execution),
                action_proposal_id=proposal.proposal_id,
                source_refs=[
                    f"proposal:{proposal.proposal_id}",
                    f"action_execution:{execution.execution_id}",
                ],
                correlation_id=correlation_id,
                idempotency_key=f"{proposal.idempotency_key}:agent_run_owner_finished:{attempt}",
            )
        )

    async def _update_proposal(
        self,
        proposal: CommercialActionProposal,
        *,
        lifecycle_state: str,
        reason_code: str,
        payload_patch: dict[str, Any] | None = None,
        requires_approval: bool | None = None,
    ) -> CommercialActionProposal:
        merged_payload = dict(proposal.payload)
        if payload_patch:
            merged_payload.update(payload_patch)
        approval_required = (
            requires_approval
            if requires_approval is not None
            else proposal.requires_approval
        )
        if lifecycle_state == "waiting_approval":
            approval_required = True
        updated = proposal.model_copy(
            update={
                "lifecycle_state": lifecycle_state,
                "reason_code": reason_code,
                "requires_approval": approval_required,
                "payload": merged_payload,
            }
        )
        await self._repository.update_action_proposal(updated)
        return updated

    async def _trace(
        self,
        *,
        proposal: CommercialActionProposal,
        correlation_id: str,
        reason_code: str,
        changed_projection_refs: list[str] | None = None,
    ) -> None:
        await self._repository.persist_decision_trace(
            CommercialDecisionTrace(
                trace_id=f"trace:action_runtime:{proposal.proposal_id}:{reason_code}",
                workspace_id=proposal.workspace_id,
                conversation_id=proposal.conversation_id,
                customer_id=proposal.customer_id,
                correlation_id=correlation_id,
                changed_projection_refs=changed_projection_refs or [],
                emitted_proposal_refs=[f"proposal:{proposal.proposal_id}"],
                degraded_reasons=[],
            )
        )


def _source_refs(values: list[str], fallback: str) -> list[str]:
    refs = list(dict.fromkeys([*values, fallback]))
    return refs or [fallback]


def _sync_agent_tool_scope(agent: Agent, *, scope: str, operation: str) -> None:
    tools_config = dict(agent.tools_config or {})
    tool_scopes = [
        str(item)
        for item in tools_config.get("tool_scopes", [])
        if isinstance(item, str) and item.strip()
    ]
    if operation == "grant":
        tool_scopes = list(dict.fromkeys([*tool_scopes, scope]))
    elif operation == "revoke":
        tool_scopes = [item for item in tool_scopes if item != scope]
    agent.tools_config = {
        **tools_config,
        "tool_scopes": tool_scopes,
    }


def _is_owner_task_proposal(proposal: CommercialActionProposal) -> bool:
    if proposal.action_type in OWNER_TASK_ACTION_TYPES:
        return True
    owner_task = _owner_task_payload(proposal.payload)
    return bool(owner_task.get("task_kind") or owner_task.get("title"))


def _owner_task_from_proposal(
    proposal: CommercialActionProposal,
    *,
    now: datetime,
) -> OwnerTaskItem:
    payload = dict(proposal.payload)
    candidate = _dict_value(payload.get("candidate_value"))
    owner_task = _owner_task_payload(payload)
    due_at = _first_text(
        owner_task.get("due_at"),
        candidate.get("due_at"),
        payload.get("due_at"),
        candidate.get("deadline"),
        payload.get("deadline"),
    )
    due_dt = _parse_due_at(due_at)
    state = _owner_task_state(proposal)
    kind = _owner_task_kind(proposal, candidate=candidate, owner_task=owner_task)
    title = _first_text(
        owner_task.get("title"),
        candidate.get("task_title"),
        candidate.get("title"),
        payload.get("title"),
        _owner_task_kind_label(kind),
    )
    detail = _first_text(
        owner_task.get("detail"),
        candidate.get("description"),
        payload.get("description"),
        candidate.get("summary"),
        _owner_task_detail(kind),
    )
    customer_label = _first_text(
        payload.get("customer_name"),
        payload.get("customer_display_name"),
        candidate.get("customer_name"),
        "Mijoz",
    )
    can_accept = state == "proposed"
    can_complete = state in {"accepted", "blocked"}
    return OwnerTaskItem(
        task_id=f"task:{proposal.proposal_id}",
        workspace_id=proposal.workspace_id,
        proposal_id=proposal.proposal_id,
        action_type=proposal.action_type,
        kind=kind,
        state=state,
        due_bucket=_owner_task_due_bucket(state=state, due_at=due_dt, now=now),
        title=title,
        detail=detail,
        customer_label=customer_label,
        conversation_id=proposal.conversation_id,
        customer_id=proposal.customer_id,
        due_at=due_dt.isoformat() if due_dt is not None else None,
        status_label=_owner_task_status_label(state, proposal.lifecycle_state),
        source_label=_owner_task_source_label(proposal.source_refs),
        evidence_labels=_owner_task_evidence_labels(proposal.source_refs),
        priority=proposal.priority,
        risk_level=proposal.risk_level,
        confidence=proposal.confidence,
        can_accept=can_accept,
        can_complete=can_complete,
        can_snooze=state in {"accepted", "blocked", "proposed"},
        can_message=state in {"accepted", "blocked"}
        and proposal.conversation_id > 0
        and proposal.customer_id > 0,
        proposal=proposal,
    )


def _owner_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _dict_value(payload.get("owner_task"))


def _owner_task_state(proposal: CommercialActionProposal) -> str:
    lifecycle = proposal.lifecycle_state
    if lifecycle in {"proposed", "waiting_approval"}:
        return "proposed"
    if lifecycle in {"approved", "executing"}:
        return "accepted"
    if lifecycle in {"blocked", "failed"}:
        return "blocked"
    if lifecycle == "executed":
        return "completed"
    return "dismissed"


def _owner_task_due_bucket(
    *,
    state: str,
    due_at: datetime | None,
    now: datetime,
) -> str:
    if state == "proposed":
        return "proposed"
    if state in {"completed", "dismissed"}:
        return "completed"
    if due_at is None:
        return "today"
    if due_at.date() < now.date():
        return "overdue"
    if due_at.date() == now.date():
        return "today"
    return "upcoming"


def _owner_task_kind(
    proposal: CommercialActionProposal,
    *,
    candidate: dict[str, Any],
    owner_task: dict[str, Any],
) -> str:
    value = _first_text(
        owner_task.get("task_kind"),
        owner_task.get("kind"),
        candidate.get("task_type"),
        candidate.get("kind"),
        "",
    ).lower()
    kind_aliases = {
        "meeting": "meeting",
        "uchrashuv": "meeting",
        "delivery": "delivery",
        "yetkazish": "delivery",
        "stock": "stock",
        "stok": "stock",
        "call": "call",
        "phone": "call",
        "payment": "payment",
        "tolov": "payment",
        "to'lov": "payment",
        "follow_up": "follow_up",
        "followup": "follow_up",
    }
    if value in kind_aliases:
        return kind_aliases[value]
    action_map = {
        "schedule_sales_follow_up": "follow_up",
        "check_payment": "payment",
        "create_delivery_order": "delivery",
    }
    return action_map.get(proposal.action_type, "business")


def _owner_task_kind_label(kind: str) -> str:
    labels = {
        "business": "Biznes vazifa",
        "meeting": "Uchrashuv",
        "delivery": "Yetkazishni tekshirish",
        "stock": "Stokni tekshirish",
        "call": "Qo'ng'iroq qilish",
        "payment": "To'lovni tekshirish",
        "follow_up": "Mijozga qayta yozish",
    }
    return labels.get(kind, "Biznes vazifa")


def _owner_task_detail(kind: str) -> str:
    details = {
        "meeting": "Mijoz uchrashuv so'ragan. Vaqtni tasdiqlang.",
        "delivery": "Yetkazish bo'yicha holatni tekshiring.",
        "stock": "Mahsulot borligini tekshiring.",
        "call": "Mijoz bilan bog'lanish kerak.",
        "payment": "To'lov tushganini tekshiring.",
        "follow_up": "Mijozga qayta yozish va suhbatni davom ettirish kerak.",
    }
    return details.get(kind, "Bu ishni egasi tekshirishi kerak.")


def _owner_task_status_label(state: str, lifecycle_state: str) -> str:
    if state == "proposed":
        return "Qabul qilish kerak"
    if state == "accepted":
        return "Bajarish kerak"
    if state == "blocked":
        return "Yordam kerak"
    if state == "completed":
        return "Tugatilgan"
    if lifecycle_state == "rejected":
        return "Rad etilgan"
    return "Yopilgan"


def _owner_task_source_label(source_refs: list[str]) -> str:
    refs = [str(ref) for ref in source_refs if str(ref).strip()]
    for prefix in (
        "bi_command:",
        "message:",
        "conversation:",
        "fact:",
        "source_unit:",
        "source:",
        "onboarding:",
        "telegram:",
    ):
        match = next((ref for ref in refs if ref.startswith(prefix) or (prefix == "message:" and ":message:" in ref)), None)
        if match:
            return _owner_task_source_label_for_ref(match)
    return "Agent taklifi"


def _owner_task_evidence_labels(source_refs: list[str]) -> list[str]:
    labels: list[str] = []
    for ref in source_refs:
        labels.append(_owner_task_evidence_label_for_ref(str(ref)))
    return list(dict.fromkeys(labels))[:4]


def _owner_task_source_label_for_ref(ref: str) -> str:
    if ref.startswith("bi_command:"):
        return f"BI buyrug'i: {_human_ref_name(ref.removeprefix('bi_command:'))}"
    if ref.startswith("message:") or ":message:" in ref:
        return _owner_task_message_label(ref)
    if ref.startswith("conversation:"):
        return f"Suhbat {_id_ref_label(ref.removeprefix('conversation:'))}"
    if ref.startswith("fact:"):
        return f"Brain: {_human_ref_name(ref.removeprefix('fact:'))}"
    if ref.startswith("source_unit:"):
        return f"Manba bo'lagi: {_source_ref_name(ref)}"
    if ref.startswith("source:") or ref.startswith("onboarding:"):
        return f"Manba: {_source_ref_name(ref)}"
    if ref.startswith("telegram:"):
        return f"Telegram: {_human_ref_name(ref.removeprefix('telegram:'))}"
    return f"Agent taklifi: {_human_ref_name(ref)}"


def _owner_task_evidence_label_for_ref(ref: str) -> str:
    if ref.startswith("bi_command:"):
        return f"BI: {_human_ref_name(ref.removeprefix('bi_command:'))}"
    if ref.startswith("message:") or ":message:" in ref:
        return _owner_task_message_label(ref)
    if ref.startswith("conversation:"):
        return f"Suhbat {_id_ref_label(ref.removeprefix('conversation:'))}"
    if ref.startswith("fact:"):
        return f"Brain: {_human_ref_name(ref.removeprefix('fact:'))}"
    if ref.startswith("source_unit:"):
        return f"Manba bo'lagi: {_source_ref_name(ref)}"
    if ref.startswith("source:") or ref.startswith("onboarding:"):
        return f"Manba: {_source_ref_name(ref)}"
    if ref.startswith("telegram:"):
        return f"Telegram: {_human_ref_name(ref.removeprefix('telegram:'))}"
    if ref.startswith("owner_task:"):
        return f"Vazifa: {_human_ref_name(ref.removeprefix('owner_task:'))}"
    if ref.startswith("candidate:"):
        return f"Topilgan signal: {_human_ref_name(ref.removeprefix('candidate:'))}"
    return f"Dalil: {_human_ref_name(ref)}"


def _message_ref_label(ref: str) -> str:
    parts = [part for part in ref.split(":") if part]
    if "message" in parts:
        index = parts.index("message")
        if index + 1 < len(parts):
            raw = parts[index + 1]
            suffix = " ".join(_human_ref_name(part) for part in parts[index + 2 :])
            label = _message_id_label(raw)
            return f"{label} {suffix}".strip()
    if len(parts) >= 2 and parts[0] == "message":
        suffix = " ".join(_human_ref_name(part) for part in parts[2:])
        return f"{_message_id_label(parts[1])} {suffix}".strip()
    return _message_id_label(ref)


def _owner_task_message_label(ref: str) -> str:
    label = _message_ref_label(ref).lstrip(": ").strip()
    if not label:
        return "Telegram xabari"
    separator = " " if label.startswith("#") else ": "
    return f"Telegram xabari{separator}{label}"


def _message_id_label(value: str) -> str:
    label = _id_ref_label(value)
    if label.startswith("#"):
        return label
    return f": {label}" if label else ""


def _id_ref_label(value: str) -> str:
    cleaned = value.strip()
    if cleaned.isdigit():
        return f"#{cleaned}"
    return _human_ref_name(cleaned) if cleaned else ""


def _source_ref_name(ref: str) -> str:
    parts = [part for part in ref.split(":") if part]
    for marker in ("source", "source_ref"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                candidate = parts[index + 1]
                if candidate not in {"ingested", "unit"}:
                    return _human_ref_name(candidate)
    if ref.startswith("onboarding:source:"):
        candidate = ref.removeprefix("onboarding:source:").split(":")[0]
        return f"#{candidate}" if candidate.isdigit() else _human_ref_name(candidate)
    meaningful = [
        part
        for part in parts
        if part
        not in {
            "source_unit",
            "business_source",
            "workspace",
            "onboarding",
            "source",
            "ingested",
            "unit",
        }
        and not part.isdigit()
    ]
    if meaningful:
        return _human_ref_name(meaningful[-1])
    numeric = next((part for part in reversed(parts) if part.isdigit()), "")
    return f"#{numeric}" if numeric else "o'qilgan manba"


def _human_ref_name(value: str) -> str:
    cleaned = value.strip().strip(":")
    if not cleaned:
        return "dalil"
    return " ".join(
        part
        for part in cleaned.replace(".", " ").replace("_", " ").replace("-", " ").split()
        if part
    )


def _parse_due_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _float_value(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _float_value_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _policy_ref(workspace_id: int) -> str:
    return f"action_runtime:policy:{workspace_id}"


def _capability_ref(capability_ref: str) -> str:
    return f"action_runtime:capability:{capability_ref}"


def _attempt(proposal: CommercialActionProposal) -> int:
    raw = proposal.payload.get("action_runtime_attempt")
    if isinstance(raw, int) and raw > 0:
        return raw
    return 1


def _execution_id(proposal: CommercialActionProposal, attempt: int) -> str:
    return f"execution:{proposal.proposal_id}:attempt:{attempt}"


def _execution_idempotency_key(
    proposal: CommercialActionProposal,
    attempt: int,
) -> str:
    raw = f"{proposal.idempotency_key}:action_runtime:attempt:{attempt}"
    if len(raw) <= 120:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"runtime:{proposal.proposal_id}:attempt:{attempt}:{digest}"[:120]


def _catalog_media_block_reason(proposal: CommercialActionProposal) -> str | None:
    if proposal.payload.get("asset_approved") is not True:
        return "approved_catalog_media_asset"
    if not _optional_string(proposal.payload.get("product_ref")):
        return "catalog_product_ref"
    if not _optional_string(proposal.payload.get("catalog_media_asset_id")):
        return "catalog_media_asset"
    if not _optional_string(proposal.payload.get("catalog_media_url")):
        return "catalog_media_url"
    return None


def _status_message_block_reason(
    proposal: CommercialActionProposal,
    status_text: str,
) -> str | None:
    if proposal.payload.get("kind") != "progress":
        return "status_message_kind_required"
    if proposal.payload.get("not_final_answer") is not True:
        return "status_message_not_final_answer_required"
    if len(status_text.strip()) > 220:
        return "status_message_too_long"
    return None


def _status_message_run_ref(proposal: CommercialActionProposal) -> str | None:
    payload_run_id = _optional_string(proposal.payload.get("agent_run_id"))
    if payload_run_id:
        return payload_run_id if payload_run_id.startswith("agent_run:") else f"agent_run:{payload_run_id}"
    for source_ref in proposal.source_refs:
        ref = str(source_ref)
        if ref.startswith("agent_run:"):
            return ref
    return None


def _agent_run_id_from_proposal(proposal: CommercialActionProposal) -> str | None:
    payload_run_id = _optional_string(proposal.payload.get("agent_run_id"))
    if payload_run_id:
        return payload_run_id.removeprefix("agent_run:").strip() or None
    for source_ref in proposal.source_refs:
        ref = str(source_ref).strip()
        if ref.startswith("agent_run:"):
            return ref.removeprefix("agent_run:").strip() or None
    return None


def _status_message_lane(proposal: CommercialActionProposal) -> str:
    lane = _optional_string(
        proposal.payload.get("tool_name")
        or proposal.payload.get("tool")
        or proposal.payload.get("reason")
    )
    if lane:
        return lane.strip().lower()
    text_key = _status_message_text_key(proposal)
    return f"text:{text_key}" if text_key else "progress"


def _status_message_text_key(proposal: CommercialActionProposal) -> str:
    text = _proposal_text(proposal)
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip().lower()


def _is_executed_status_message(row: dict[str, Any]) -> bool:
    payload = dict(row.get("payload") or {})
    return (
        row.get("action_type") == "send_status_message"
        and row.get("status") == "executed"
        and payload.get("side_effect") == TELEGRAM_STATUS_MESSAGE_SIDE_EFFECT
    )


def _blocked_execution(
    proposal: CommercialActionProposal,
    *,
    attempt: int,
    correlation_id: str,
    reason_code: str,
    idempotency_key: str,
) -> ActionRuntimeExecution:
    return ActionRuntimeExecution(
        execution_id=_execution_id(proposal, attempt),
        workspace_id=proposal.workspace_id,
        conversation_id=proposal.conversation_id,
        customer_id=proposal.customer_id,
        proposal_id=proposal.proposal_id,
        action_type=proposal.action_type,
        status="blocked",
        reason_code=reason_code,
        idempotency_key=idempotency_key,
        attempt=attempt,
        payload={
            "side_effect": proposal.action_type,
            "correlation_id": correlation_id,
            "proposal_payload": dict(proposal.payload),
        },
    )


def _forced_failure_execution(
    proposal: CommercialActionProposal,
    *,
    attempt: int,
    correlation_id: str,
    idempotency_key: str,
) -> ActionRuntimeExecution:
    return ActionRuntimeExecution(
        execution_id=_execution_id(proposal, attempt),
        workspace_id=proposal.workspace_id,
        conversation_id=proposal.conversation_id,
        customer_id=proposal.customer_id,
        proposal_id=proposal.proposal_id,
        action_type=proposal.action_type,
        status="failed",
        reason_code="executor_failed",
        idempotency_key=idempotency_key,
        attempt=attempt,
        payload={
            "side_effect": proposal.action_type,
            "correlation_id": correlation_id,
            "proposal_payload": dict(proposal.payload),
        },
        error="forced_failure",
    )


def _telegram_tool_execution(
    proposal: CommercialActionProposal,
    *,
    attempt: int,
    correlation_id: str,
    idempotency_key: str,
    tool_result: TelegramToolResult,
    side_effect: str,
) -> ActionRuntimeExecution:
    status = _tool_execution_status(tool_result.status)
    return ActionRuntimeExecution(
        execution_id=_execution_id(proposal, attempt),
        workspace_id=proposal.workspace_id,
        conversation_id=proposal.conversation_id,
        customer_id=proposal.customer_id,
        proposal_id=proposal.proposal_id,
        action_type=proposal.action_type,
        status=status,  # type: ignore[arg-type]
        reason_code=tool_result.reason_code,
        idempotency_key=idempotency_key,
        attempt=attempt,
        delivery_state=tool_result.delivery_state,
        external_message_id=tool_result.external_message_id,
        payload={
            "side_effect": side_effect,
            "correlation_id": correlation_id,
            "proposal_payload": dict(proposal.payload),
            "telegram_tool": tool_result.model_dump(mode="json"),
        },
        error=tool_result.reason_code if status == "failed" else None,
    )


def _talk_bundle_execution(
    proposal: CommercialActionProposal,
    *,
    attempt: int,
    correlation_id: str,
    idempotency_key: str,
    result: TalkBundleExecutionResult,
) -> ActionRuntimeExecution:
    status = _bundle_execution_status(result.status)
    last_bubble = _last_delivered_bubble(result)
    message_id = last_bubble.message_id if last_bubble is not None else None
    external_message_id = (
        last_bubble.external_message_id if last_bubble is not None else None
    )
    return ActionRuntimeExecution(
        execution_id=_execution_id(proposal, attempt),
        workspace_id=proposal.workspace_id,
        conversation_id=proposal.conversation_id,
        customer_id=proposal.customer_id,
        proposal_id=proposal.proposal_id,
        action_type=proposal.action_type,
        status=status,  # type: ignore[arg-type]
        reason_code=result.reason,
        idempotency_key=idempotency_key,
        attempt=attempt,
        delivery_state=result.delivery_state,
        external_message_id=external_message_id,
        payload={
            "side_effect": TELEGRAM_SEND_MESSAGE,
            "correlation_id": correlation_id,
            "proposal_payload": dict(proposal.payload),
            "talk_bundle_execution": result.model_dump(mode="json"),
            "telegram_tool": {
                "workspace_id": proposal.workspace_id,
                "agent_id": _proposal_int(proposal.payload, "agent_id", "owner_agent_id") or 0,
                "scope": TELEGRAM_SEND_MESSAGE,
                "status": status,
                "reason_code": result.reason,
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key,
                "conversation_id": proposal.conversation_id,
                "message_id": message_id,
                "external_message_id": external_message_id,
                "delivery_state": result.delivery_state,
                "payload": {"bundle": True, "sent_count": result.sent_count},
                "messages": [],
            },
        },
        error=result.reason if status == "failed" else None,
    )


def _bundle_execution_status(status: str) -> str:
    if status == "executed":
        return "executed"
    if status == "blocked":
        return "blocked"
    return "failed"


def _last_delivered_bubble(result: TalkBundleExecutionResult):
    for bubble in reversed(result.bubbles):
        if bubble.status in {"executed", "replayed"}:
            return bubble
    return None


def _tool_execution_status(status: str) -> str:
    if status in {"executed", "replayed"}:
        return "executed"
    if status == "blocked":
        return "blocked"
    if status == "unsupported":
        return "unsupported"
    return "failed"


def _executor_event_base(run_id: str, proposal_id: str, attempt: int) -> str:
    return f"agent-run:{run_id}:proposal:{proposal_id}:attempt:{attempt}"


def _executor_tool_name(action_type: str) -> str:
    if action_type == "send_status_message":
        return TELEGRAM_STATUS_MESSAGE_SIDE_EFFECT
    if action_type == "send_reply":
        return TELEGRAM_SEND_MESSAGE
    if action_type in {"edit_reply", "edit_sent_reply"}:
        return TELEGRAM_EDIT_MESSAGE
    if action_type == "send_catalog_media":
        return "telegram.send_catalog_media"
    return f"action_runtime.{action_type}"


def _executor_tool_state(status: str) -> str:
    if status == "executed":
        return "succeeded"
    if status == "blocked":
        return "blocked"
    return "failed"


def _executor_owner_started_label(action_type: str) -> str:
    labels = {
        "send_reply": "Javob yuborilmoqda",
        "send_status_message": "Holat xabari yuborilmoqda",
        "edit_reply": "Javob tahrirlanmoqda",
        "edit_sent_reply": "Javob tahrirlanmoqda",
        "send_catalog_media": "Katalog rasmi yuborilmoqda",
    }
    return labels.get(action_type, "Amal bajarilmoqda")


def _executor_owner_started_detail(action_type: str) -> str:
    if action_type == "send_status_message":
        return "Mijozga yakuniy javob emas, qisqa jarayon xabari yuboriladi."
    if action_type in {"send_reply", "edit_reply", "edit_sent_reply"}:
        return "Tasdiqlangan matn Telegram orqali bajarilmoqda."
    if action_type == "send_catalog_media":
        return "Tasdiqlangan media Telegram orqali yuborilmoqda."
    return "Tasdiqlangan amal runtime orqali bajarilmoqda."


def _executor_owner_finished_label(action_type: str, status: str) -> str:
    if status == "executed":
        labels = {
            "send_reply": "Javob yuborildi",
            "send_status_message": "Holat xabari yuborildi",
            "edit_reply": "Javob tahrirlandi",
            "edit_sent_reply": "Javob tahrirlandi",
            "send_catalog_media": "Katalog rasmi yuborildi",
        }
        return labels.get(action_type, "Amal bajarildi")
    if status == "blocked":
        return "Amal to‘xtadi"
    return "Amal bajarilmadi"


def _executor_owner_finished_detail(execution: ActionRuntimeExecution) -> str:
    if execution.status == "executed":
        if execution.delivery_state:
            return f"Telegram holati: {_delivery_state_label(execution.delivery_state)}."
        return "Runtime amalni yakunladi."
    if execution.status == "blocked":
        return "Ruxsat, ulanish yoki xavfsizlik sharti sababli yuborilmadi."
    return "OQIM buni qayta urinish yoki egadan ko‘rib chiqish uchun saqladi."


def _delivery_state_label(value: str) -> str:
    known = {
        "confirmed": "tasdiqlandi",
        "pending": "kutilmoqda",
        "unknown": "aniqlanmoqda",
        "failed": "xato",
        "retrying": "qayta urinilmoqda",
    }
    return known.get(value, "qayd qilindi")


def _proposal_text(proposal: CommercialActionProposal) -> str | None:
    for key in ("draft_text", "reply_text", "text", "message_text", "draft_content"):
        value = _optional_string(proposal.payload.get(key))
        if value:
            return value
    return None


def _proposal_talk_bundle(proposal: CommercialActionProposal) -> TalkBundle | None:
    raw = proposal.payload.get("talk_bundle")
    if not isinstance(raw, dict):
        return None
    try:
        return TalkBundle.model_validate(raw)
    except Exception:
        return None


def _proposal_agent_id(payload: dict[str, Any], source_refs: list[str]) -> int | None:
    direct = _proposal_int(payload, "agent_id", "owner_agent_id")
    if direct is not None:
        return direct
    ref = _optional_string(payload.get("agent_ref"))
    if ref:
        parsed = _agent_id_from_ref(ref)
        if parsed is not None:
            return parsed
    for source_ref in source_refs:
        parsed = _agent_id_from_ref(source_ref)
        if parsed is not None:
            return parsed
    return None


def _agent_id_from_ref(value: str) -> int | None:
    match = re.fullmatch(r"agent:(\d+)", str(value).strip())
    if match is None:
        return None
    parsed = int(match.group(1))
    return parsed if parsed > 0 else None


def _proposal_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            if parsed > 0:
                return parsed
    return None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
