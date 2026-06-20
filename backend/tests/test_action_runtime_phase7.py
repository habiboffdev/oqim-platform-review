from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.workspace import Workspace
from app.modules.action_runtime.contracts import (
    ActionRuntimePolicyInput,
    ActionRuntimeRequeueInput,
    IntegrationCapabilityInput,
)
from app.modules.action_runtime.service import (
    TELEGRAM_STATUS_MESSAGE_SIDE_EFFECT,
    ActionRuntimeService,
    _execution_idempotency_key,
)
from app.modules.agent_runtime_events.contracts import (
    AgentRunEventInput,
    AgentRunInput,
)
from app.modules.agent_runtime_events.service import (
    AgentRuntimeEventService,
)
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository
from app.modules.telegram_tools.contracts import (
    TELEGRAM_EDIT_MESSAGE,
    TELEGRAM_SEND_MESSAGE,
    TelegramToolResult,
)
from app.modules.tool_grants.contracts import ToolGrantInput
from app.modules.tool_grants.service import ToolGrantService
from app.services.channel_adapter_contract import ChannelOutboundMedia
from app.services.delivery import DeliveryResult


def _repository(db_session: AsyncSession) -> CommercialSpineRepository:
    return CommercialSpineRepository(db_session)


def _service(db_session: AsyncSession) -> ActionRuntimeService:
    return ActionRuntimeService(repository=_repository(db_session))


class _FakeDelivery:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.message_calls: list[dict] = []

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
        reply_to_message_id: int | None = None,
        delay_override_seconds: float | None = None,
        typing_indicator: bool | None = None,
        online_tail_seconds: float | None = None,
    ) -> DeliveryResult:
        _ = db, action_record_id
        self.message_calls.append(
            {
                "conversation_id": conversation_id,
                "workspace_id": workspace_id,
                "text": text,
                "idempotency_key": client_idempotency_key,
                "message_id": message_id,
                "reply_to_message_id": reply_to_message_id,
                "delay_override_seconds": delay_override_seconds,
                "typing_indicator": typing_indicator,
                "online_tail_seconds": online_tail_seconds,
            }
        )
        return DeliveryResult(
            success=True,
            external_message_id="telegram-text-42",
            state="confirmed",
        )

    async def deliver_media(
        self,
        conversation_id: int,
        media: ChannelOutboundMedia,
        *,
        caption: str | None,
        db: AsyncSession,
        workspace_id: int | None = None,
        action_record_id: int | None = None,
        client_idempotency_key: str | None = None,
        message_id: int | None = None,
    ) -> DeliveryResult:
        _ = db, action_record_id, message_id
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "workspace_id": workspace_id,
                "media": media,
                "caption": caption,
                "idempotency_key": client_idempotency_key,
            }
        )
        return DeliveryResult(
            success=True,
            external_message_id="telegram-media-42",
            state="confirmed",
        )


class _FakeTelegramTools:
    def __init__(self) -> None:
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []

    async def send_message(self, **kwargs) -> TelegramToolResult:
        self.send_calls.append(kwargs)
        return TelegramToolResult(
            workspace_id=kwargs["workspace_id"],
            agent_id=kwargs["agent_id"],
            scope=TELEGRAM_SEND_MESSAGE,
            status="executed",
            reason_code="delivery_confirmed",
            correlation_id=kwargs["correlation_id"],
            idempotency_key=kwargs["idempotency_key"],
            conversation_id=kwargs["conversation_id"],
            message_id=919,
            external_message_id="telegram-text-fake",
            delivery_state="confirmed",
        )

    async def edit_message(self, **kwargs) -> TelegramToolResult:
        self.edit_calls.append(kwargs)
        return TelegramToolResult(
            workspace_id=kwargs["workspace_id"],
            agent_id=kwargs["agent_id"],
            scope=TELEGRAM_EDIT_MESSAGE,
            status="executed",
            reason_code="message_edited",
            correlation_id=kwargs["correlation_id"],
            idempotency_key=kwargs["idempotency_key"],
            conversation_id=73,
            message_id=kwargs["local_message_id"],
            external_message_id="telegram-edited-fake",
            delivery_state="confirmed",
        )


async def _grant(
    db_session: AsyncSession,
    *,
    workspace: Workspace,
    agent: Agent,
    scope: str,
) -> None:
    await ToolGrantService(db_session).grant(
        workspace_id=workspace.id,
        payload=ToolGrantInput(agent_id=agent.id, scope=scope),
    )


async def _proposal(
    db_session: AsyncSession,
    *,
    workspace: Workspace,
    conversation: Conversation,
    customer: Customer,
    proposal_id: str = "proposal-phase7",
    action_type: str = "send_reply",
    confidence: float = 0.9,
    risk_level: str = "low",
    requires_approval: bool = False,
    payload: dict | None = None,
    source_refs: list[str] | None = None,
) -> CommercialActionProposal:
    proposal = CommercialActionProposal(
        proposal_id=proposal_id,
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        action_type=action_type,
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level=risk_level,  # type: ignore[arg-type]
        requires_approval=requires_approval,
        priority="medium",
        confidence=confidence,
        reason_code="phase7_test",
        source_refs=source_refs or [f"message:{proposal_id}"],
        payload=payload or {"draft_text": "Ha, yuboraman."},
        idempotency_key=f"idem:{proposal_id}",
        correlation_id=f"corr:{proposal_id}",
        trace_id=f"trace:{proposal_id}",
    )
    await _repository(db_session).persist_action_proposal(proposal)
    return proposal


async def _policy(
    db_session: AsyncSession,
    workspace: Workspace,
    *,
    enabled: bool = True,
    threshold: float = 0.8,
    allowed: list[str] | None = None,
    quiet_hours_active: bool = False,
    escalation_destination: str = "in_app",
) -> None:
    await _service(db_session).set_policy(
        ActionRuntimePolicyInput(
            workspace_id=workspace.id,
            enabled=enabled,
            confidence_threshold=threshold,
            low_risk_allowlist=allowed or ["send_reply", "send_catalog_media"],
            quiet_hours={"active": quiet_hours_active},
            escalation_destination=escalation_destination,
            source_refs=["policy:phase7"],
            correlation_id="corr:policy:phase7",
        )
    )


async def test_action_runtime_inbox_lifecycle_and_manual_approval(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        confidence=0.62,
    )
    service = _service(db_session)

    decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    inbox = await service.inbox(workspace_id=workspace.id)
    approved = await service.approve(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
        actor_ref="seller:test",
        correlation_id="corr:approve:phase7",
    )
    approved_inbox = await service.inbox(workspace_id=workspace.id)
    rejected = await service.reject(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
        actor_ref="seller:test",
        reason_code="seller_changed_mind",
        correlation_id="corr:reject:phase7",
    )

    assert decision.state == "waiting_approval"
    assert decision.reason_code == "action_policy_disabled"
    assert inbox.items[0].proposal_id == proposal.proposal_id
    assert inbox.items[0].lifecycle_state == "waiting_approval"
    assert approved.lifecycle_state == "approved"
    assert approved_inbox.items[0].proposal_id == proposal.proposal_id
    assert approved_inbox.items[0].lifecycle_state == "approved"
    assert rejected.lifecycle_state == "rejected"


async def test_autopilot_executes_allowed_low_risk_once(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace)
    await _grant(
        db_session,
        workspace=workspace,
        agent=agent,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-auto-send",
        action_type="send_reply",
        confidence=0.94,
        payload={"draft_text": "Ha, yuboraman.", "agent_id": agent.id},
    )
    delivery = _FakeDelivery()
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=delivery,
    )

    first = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    second = await service.execute(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
        correlation_id="corr:execute:again",
    )
    executions = await service.executions(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    assert first.state == "executed"
    assert first.execution is not None
    assert first.execution.status == "executed"
    assert second.execution_id == first.execution.execution_id
    assert len(executions) == 1
    assert executions[0].payload["side_effect"] == TELEGRAM_SEND_MESSAGE
    assert executions[0].external_message_id == "telegram-text-42"
    assert executions[0].payload["telegram_tool"]["message_id"] is not None
    assert len(delivery.message_calls) == 1
    assert delivery.message_calls[0]["text"] == "Ha, yuboraman."


async def test_send_reply_blocks_without_telegram_tool_grant(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace)
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-send-no-grant",
        action_type="send_reply",
        confidence=0.96,
        payload={"draft_text": "Grant kerak.", "agent_id": agent.id},
    )
    delivery = _FakeDelivery()
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=delivery,
    )

    decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    executions = await service.executions(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    assert decision.state == "blocked"
    assert decision.reason_code == "missing_tool_grant"
    assert len(delivery.message_calls) == 0
    assert executions[0].payload["telegram_tool"]["scope"] == TELEGRAM_SEND_MESSAGE
    assert executions[0].payload["telegram_tool"]["status"] == "blocked"


async def test_status_message_executes_as_distinct_progress_action(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace, allowed=["send_status_message"])
    await _grant(
        db_session,
        workspace=workspace,
        agent=agent,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-status-message",
        action_type="send_status_message",
        confidence=0.98,
        payload={
            "agent_id": agent.id,
            "kind": "progress",
            "not_final_answer": True,
            "draft_text": "Katalogdan aynan shu modelni tekshiryapman.",
            "reason": "catalog_lookup_expected_to_take_longer",
        },
    )
    delivery = _FakeDelivery()
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=delivery,
    )

    decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    executions = await service.executions(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    assert decision.state == "executed"
    assert decision.execution is not None
    assert decision.execution.action_type == "send_status_message"
    assert executions[0].payload["side_effect"] == TELEGRAM_STATUS_MESSAGE_SIDE_EFFECT
    assert executions[0].payload["telegram_tool"]["scope"] == TELEGRAM_SEND_MESSAGE
    assert executions[0].payload["proposal_payload"]["not_final_answer"] is True
    assert len(delivery.message_calls) == 1
    assert delivery.message_calls[0]["text"] == "Katalogdan aynan shu modelni tekshiryapman."


async def test_status_message_blocks_without_telegram_tool_grant(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace, allowed=["send_status_message"])
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-status-no-grant",
        action_type="send_status_message",
        confidence=0.98,
        payload={
            "agent_id": agent.id,
            "kind": "progress",
            "not_final_answer": True,
            "draft_text": "Ombor bo‘yicha aniqlashtirib beraman.",
        },
    )
    delivery = _FakeDelivery()
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=delivery,
    )

    decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    executions = await service.executions(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    assert decision.state == "blocked"
    assert decision.reason_code == "missing_tool_grant"
    assert len(delivery.message_calls) == 0
    assert executions[0].payload["side_effect"] == TELEGRAM_STATUS_MESSAGE_SIDE_EFFECT
    assert executions[0].payload["telegram_tool"]["scope"] == TELEGRAM_SEND_MESSAGE
    assert executions[0].payload["telegram_tool"]["status"] == "blocked"


async def test_status_message_requires_progress_payload_contract(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace, allowed=["send_status_message"])
    await _grant(
        db_session,
        workspace=workspace,
        agent=agent,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-status-bad-contract",
        action_type="send_status_message",
        confidence=0.98,
        payload={
            "agent_id": agent.id,
            "draft_text": "Katalogdan tekshiryapman.",
        },
    )
    delivery = _FakeDelivery()
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=delivery,
    )

    decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    executions = await service.executions(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    assert decision.state == "blocked"
    assert decision.reason_code == "status_message_kind_required"
    assert len(delivery.message_calls) == 0
    assert executions[0].payload["proposal_payload"]["draft_text"] == "Katalogdan tekshiryapman."


async def test_status_message_waits_for_owner_when_approval_required(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace, allowed=["send_status_message"])
    await _grant(
        db_session,
        workspace=workspace,
        agent=agent,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-status-approval",
        action_type="send_status_message",
        confidence=0.98,
        requires_approval=True,
        payload={
            "agent_id": agent.id,
            "kind": "progress",
            "not_final_answer": True,
            "draft_text": "To‘lov rasmini tekshirib olay.",
        },
    )
    delivery = _FakeDelivery()
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=delivery,
    )

    decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    executions = await service.executions(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    assert decision.state == "waiting_approval"
    assert decision.reason_code == "risk_requires_approval"
    assert executions == ()
    assert len(delivery.message_calls) == 0


async def test_status_message_blocks_duplicate_auto_send_for_same_agent_run_lane(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace, allowed=["send_status_message"])
    await _grant(
        db_session,
        workspace=workspace,
        agent=agent,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    run_ref = "agent_run:seller-agent-run:duplicate-status-proof"
    first = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-status-duplicate-first",
        action_type="send_status_message",
        confidence=0.98,
        source_refs=[run_ref, "message:status-duplicate:first"],
        payload={
            "agent_id": agent.id,
            "kind": "progress",
            "not_final_answer": True,
            "draft_text": "Katalogdan aynan shu modelni tekshiryapman.",
            "reason": "catalog_lookup_expected_to_take_longer",
        },
    )
    duplicate = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-status-duplicate-second",
        action_type="send_status_message",
        confidence=0.98,
        source_refs=[run_ref, "message:status-duplicate:second"],
        payload={
            "agent_id": agent.id,
            "kind": "progress",
            "not_final_answer": True,
            "draft_text": "Hozir katalogni qayta tekshiryapman.",
            "reason": "catalog_lookup_expected_to_take_longer",
        },
    )
    delivery = _FakeDelivery()
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=delivery,
    )

    first_decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=first.proposal_id,
    )
    duplicate_decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=duplicate.proposal_id,
    )
    duplicate_executions = await service.executions(
        workspace_id=workspace.id,
        proposal_id=duplicate.proposal_id,
    )

    assert first_decision.state == "executed"
    assert duplicate_decision.state == "blocked"
    assert duplicate_decision.reason_code == "status_message_duplicate_in_run"
    assert len(delivery.message_calls) == 1
    assert duplicate_executions[0].status == "blocked"


async def test_action_runtime_returns_proposal_agent_run_timeline(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    run_id = "seller-agent-run:timeline-proof"
    event_service = AgentRuntimeEventService(_repository(db_session))
    await event_service.start_run(
        AgentRunInput(
            run_id=run_id,
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            trigger_ref="message:timeline-proof",
            conversation_id=conversation.id,
            customer_id=customer.id,
            state="waiting_approval",
            permission_mode="ask_always",
            correlation_id="corr:timeline-proof",
            idempotency_key="idem:timeline-proof",
        )
    )
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-timeline-proof",
        action_type="send_status_message",
        payload={
            "agent_id": agent.id,
            "kind": "progress",
            "not_final_answer": True,
            "draft_text": "Katalogdan tekshiryapman.",
        },
    )
    proposal = proposal.model_copy(
        update={
            "source_refs": [*proposal.source_refs, f"agent_run:{run_id}"],
        }
    )
    await _repository(db_session).update_action_proposal(proposal)
    await event_service.record_event(
        AgentRunEventInput(
            event_id="timeline-proof-owner",
            run_id=run_id,
            workspace_id=workspace.id,
            event_type="owner_progress.created",
            visibility="owner",
            owner_label="Katalog tekshirilyapti",
            owner_detail="Mos mahsulot topilmoqda.",
            correlation_id="corr:timeline-proof",
            idempotency_key="idem:timeline-proof:owner",
        )
    )
    await event_service.record_event(
        AgentRunEventInput(
            event_id="timeline-proof-internal",
            run_id=run_id,
            workspace_id=workspace.id,
            event_type="tool.call.started",
            visibility="internal",
            tool_name="catalog.search",
            tool_state="called",
            correlation_id="corr:timeline-proof",
            idempotency_key="idem:timeline-proof:internal",
        )
    )
    await event_service.record_event(
        AgentRunEventInput(
            event_id="timeline-proof-customer-action",
            run_id=run_id,
            workspace_id=workspace.id,
            event_type="customer_status.proposed",
            visibility="customer_action",
            owner_label="Mijozga holat xabari taklif qilindi",
            action_proposal_id=proposal.proposal_id,
            correlation_id="corr:timeline-proof",
            idempotency_key="idem:timeline-proof:customer-action",
        )
    )
    await db_session.commit()

    response = await client.get(
        f"/api/action-runtime/proposals/{proposal.proposal_id}/timeline",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "agent_run_timeline.v1"
    assert body["run_id"] == run_id
    assert body["run"]["state"] == "waiting_approval"
    assert [event["event_type"] for event in body["events"]] == [
        "owner_progress.created",
        "tool.call.started",
        "customer_status.proposed",
    ]
    assert body["events"][2]["action_proposal_id"] == proposal.proposal_id


async def test_action_runtime_records_executor_tool_events_for_agent_run(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace, allowed=["send_status_message"])
    await _grant(
        db_session,
        workspace=workspace,
        agent=agent,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    run_id = "seller-agent-run:executor-tool-proof"
    event_service = AgentRuntimeEventService(_repository(db_session))
    await event_service.start_run(
        AgentRunInput(
            run_id=run_id,
            workspace_id=workspace.id,
            agent_id=agent.id,
            agent_kind="seller",
            trigger_ref="message:executor-tool-proof",
            conversation_id=conversation.id,
            customer_id=customer.id,
            state="running",
            permission_mode="auto_approve",
            correlation_id="corr:executor-tool-proof",
            idempotency_key="idem:executor-tool-proof",
        )
    )
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-executor-tool-proof",
        action_type="send_status_message",
        confidence=0.98,
        source_refs=[f"agent_run:{run_id}", "message:executor-tool-proof"],
        payload={
            "agent_id": agent.id,
            "kind": "progress",
            "not_final_answer": True,
            "draft_text": "Katalogdan aynan shu modelni tekshiryapman.",
            "reason": "catalog_lookup_expected_to_take_longer",
        },
    )
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=_FakeDelivery(),
    )

    decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
        correlation_id="corr:executor-tool-proof:process",
    )
    timeline = await event_service.timeline(workspace_id=workspace.id, run_id=run_id)

    assert decision.state == "executed"
    assert [event.event_type for event in timeline.events] == [
        "tool.call.started",
        "owner_progress.created",
        "tool.call.succeeded",
        "owner_progress.created",
    ]
    assert timeline.events[0].visibility == "internal"
    assert timeline.events[0].tool_name == TELEGRAM_STATUS_MESSAGE_SIDE_EFFECT
    assert timeline.events[0].tool_state == "called"
    assert timeline.events[1].visibility == "owner"
    assert timeline.events[1].owner_label == "Holat xabari yuborilmoqda"
    assert timeline.events[2].tool_state == "succeeded"
    assert timeline.events[3].owner_label == "Holat xabari yuborildi"
    assert timeline.events[3].owner_detail == "Telegram holati: tasdiqlandi."


async def test_edit_reply_executes_through_telegram_edit_tool(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace, allowed=["edit_reply"])
    telegram_tools = _FakeTelegramTools()
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-edit-reply",
        action_type="edit_reply",
        confidence=0.98,
        payload={
            "agent_id": agent.id,
            "local_message_id": 7007,
            "draft_text": "Tahrirlangan javob.",
        },
    )
    service = ActionRuntimeService(
        repository=_repository(db_session),
        telegram_tools=telegram_tools,
    )

    decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    assert decision.state == "executed"
    assert decision.execution is not None
    assert decision.execution.reason_code == "message_edited"
    assert decision.execution.payload["side_effect"] == TELEGRAM_EDIT_MESSAGE
    assert decision.execution.external_message_id == "telegram-edited-fake"
    assert telegram_tools.edit_calls == [
        {
            "workspace_id": workspace.id,
            "agent_id": agent.id,
            "local_message_id": 7007,
            "text": "Tahrirlangan javob.",
            "correlation_id": proposal.correlation_id,
            "idempotency_key": decision.execution.idempotency_key,
        }
    ]


async def test_action_runtime_executes_catalog_media_through_delivery_runtime(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    await _policy(db_session, workspace)
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-auto-catalog-media",
        action_type="send_catalog_media",
        confidence=0.97,
        payload={
            "product_ref": "catalog_product:ring-001",
            "catalog_media_asset_id": "catalog_media:ring-001-photo",
            "catalog_media_url": "https://cdn.example/ring.jpg",
            "asset_approved": True,
            "caption": "Mana rasmi",
            "media_type": "photo",
            "mime_type": "image/jpeg",
        },
    )
    delivery = _FakeDelivery()
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=delivery,
    )

    decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    repeated = await service.execute(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
        correlation_id="corr:catalog-media-repeat",
    )

    assert decision.state == "executed"
    assert decision.execution is not None
    assert decision.execution.reason_code == "delivery_confirmed"
    assert decision.execution.external_message_id == "telegram-media-42"
    assert repeated.execution_id == decision.execution.execution_id
    assert len(delivery.calls) == 1
    call = delivery.calls[0]
    assert call["conversation_id"] == conversation.id
    assert call["workspace_id"] == workspace.id
    assert call["caption"] == "Mana rasmi"
    assert call["media"].url == "https://cdn.example/ring.jpg"
    assert call["media"].asset_id == "catalog_media:ring-001-photo"


async def test_autopilot_low_confidence_risky_and_quiet_hours_escalate(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    await _policy(db_session, workspace, threshold=0.9)
    service = _service(db_session)
    low_conf = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-low-confidence",
        confidence=0.72,
    )
    risky = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-risky",
        risk_level="high",
        requires_approval=True,
    )
    await _policy(db_session, workspace, quiet_hours_active=True)
    quiet = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-quiet",
        confidence=0.99,
    )

    low = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=low_conf.proposal_id,
    )
    risk = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=risky.proposal_id,
    )
    quiet_decision = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=quiet.proposal_id,
    )

    assert low.state == "waiting_approval"
    assert low.reason_code == "confidence_below_threshold"
    assert risk.state == "waiting_approval"
    assert risk.reason_code == "risk_requires_approval"
    assert quiet_decision.state == "waiting_approval"
    assert quiet_decision.reason_code == "quiet_hours_active"


async def test_missing_integration_capability_blocks_with_reason(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    await _policy(db_session, workspace, allowed=["create_calendar_event"])
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-calendar",
        action_type="create_calendar_event",
        confidence=0.97,
        payload={"title": "Customer meeting"},
    )
    service = _service(db_session)

    blocked = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    await service.register_capability(
        IntegrationCapabilityInput(
            workspace_id=workspace.id,
            capability_ref="capability:calendar:primary",
            integration_kind="calendar",
            enabled=True,
            allowed_action_types=["create_calendar_event"],
            source_refs=["owner:calendar-connected"],
            correlation_id="corr:calendar-capability",
        )
    )
    retry = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    assert blocked.state == "blocked"
    assert blocked.reason_code == "missing_integration_capability"
    assert retry.state == "executed"
    assert retry.execution is not None
    assert retry.execution.payload["side_effect"] == "create_calendar_event"


async def test_failure_is_visible_and_requeue_retries_with_new_attempt(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
    agent: Agent,
) -> None:
    await _policy(db_session, workspace)
    await _grant(
        db_session,
        workspace=workspace,
        agent=agent,
        scope=TELEGRAM_SEND_MESSAGE,
    )
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-failure",
        action_type="send_reply",
        confidence=0.98,
        payload={"draft_text": "Retry me", "force_failure": True, "agent_id": agent.id},
    )
    delivery = _FakeDelivery()
    service = ActionRuntimeService(
        repository=_repository(db_session),
        delivery=delivery,
    )

    failed = await service.process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    requeued = await service.requeue_failed(
        ActionRuntimeRequeueInput(
            workspace_id=workspace.id,
            proposal_id=proposal.proposal_id,
            patch_payload={"force_failure": False},
            actor_ref="operator:test",
            correlation_id="corr:requeue:phase7",
        )
    )
    recovered = await service.execute(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
        correlation_id="corr:execute:recovered",
    )
    executions = await service.executions(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    assert failed.state == "failed"
    assert failed.execution is not None
    assert failed.execution.status == "failed"
    assert requeued.lifecycle_state == "approved"
    assert recovered.status == "executed"
    assert len(executions) == 2
    assert [item.status for item in executions] == ["failed", "executed"]
    assert len(delivery.message_calls) == 1
    assert delivery.message_calls[0]["text"] == "Retry me"


async def test_telegram_seller_bot_notification_adapter_records_escalation(
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    await _policy(
        db_session,
        workspace,
        threshold=0.99,
        escalation_destination="telegram_seller_bot",
    )
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-telegram-escalation",
        confidence=0.7,
    )

    decision = await _service(db_session).process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )
    notification = await _repository(db_session).get_projection(
        workspace_id=workspace.id,
        projection_ref=f"action_runtime:notification:{proposal.proposal_id}:telegram_seller_bot",
    )

    assert decision.state == "waiting_approval"
    assert notification is not None
    assert notification.state["channel"] == "telegram_seller_bot"
    assert notification.state["status"] == "queued"
    assert notification.state["proposal_id"] == proposal.proposal_id


async def test_action_runtime_api_is_workspace_scoped(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_headers_b: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-api",
    )
    await _service(db_session).process_proposal(
        workspace_id=workspace.id,
        proposal_id=proposal.proposal_id,
    )

    own = await client.get("/api/action-runtime/inbox", headers=auth_headers)
    other = await client.get("/api/action-runtime/inbox", headers=auth_headers_b)

    assert own.status_code == 200
    assert own.json()["items"][0]["proposal_id"] == proposal.proposal_id
    assert other.status_code == 200
    assert other.json()["items"] == []


async def test_owner_task_projection_uses_business_labels_and_task_actions(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    due_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    proposed = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-task-meeting",
        action_type="create_business_task",
        payload={
            "customer_name": "Madina",
            "owner_task": {
                "task_kind": "meeting",
                "title": "Madina bilan uchrashuv vaqtini tasdiqlash",
                "detail": "Ertangi vaqtni egasi tasdiqlashi kerak.",
                "due_at": due_at,
            },
        },
    )
    accepted = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-task-payment",
        action_type="check_payment",
        payload={
            "customer_name": "Jasur",
            "candidate_value": {
                "task_title": "To'lov chekini tekshirish",
                "description": "Mijoz chek yuborganini aytdi.",
                "task_type": "payment",
            },
        },
    )
    source_task = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-task-source",
        action_type="create_business_task",
        source_refs=[
            "source_unit:business_source:onboarding:source:satstation:ingested:000",
            "source:catalog-pdf",
        ],
        payload={
            "customer_name": "Otabek",
            "owner_task": {
                "task_kind": "stock",
                "title": "Satstation katalogidagi mavjudlikni tekshirish",
                "detail": "Kanal va PDFdan kelgan mahsulot mavjudligini egasi tekshiradi.",
            },
        },
    )
    await _service(db_session).approve(
        workspace_id=workspace.id,
        proposal_id=accepted.proposal_id,
        actor_ref="owner:test",
        correlation_id="corr:task:accept",
    )

    projection = await _service(db_session).owner_tasks(workspace_id=workspace.id)
    response = await client.get("/api/action-runtime/tasks", headers=auth_headers)

    assert proposed.proposal_id in {item.proposal_id for item in projection.proposed}
    assert projection.counts["proposed"] == 2
    assert projection.counts["today"] == 1
    assert projection.counts["upcoming"] == 0
    payment = next(item for item in projection.items if item.proposal_id == accepted.proposal_id)
    assert payment.kind == "payment"
    assert payment.status_label == "Bajarish kerak"
    assert payment.evidence_labels == ["Telegram xabari: proposal task payment"]
    assert "message:" not in " ".join(payment.evidence_labels)
    source_item = next(item for item in projection.items if item.proposal_id == source_task.proposal_id)
    assert source_item.source_label == "Manba bo'lagi: satstation"
    assert source_item.evidence_labels == ["Manba bo'lagi: satstation", "Manba: catalog pdf"]
    assert "source_unit:" not in " ".join(source_item.evidence_labels)

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "owner_task_projection.v1"
    assert "Madina bilan uchrashuv vaqtini tasdiqlash" in {
        item["title"] for item in body["proposed"]
    }
    assert any(item["source_label"].startswith("Telegram xabari") for item in body["items"])
    assert any(item["source_label"] == "Manba bo'lagi: satstation" for item in body["items"])


async def test_action_runtime_edits_reply_draft_before_approval(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-draft-edit",
        action_type="send_reply",
        requires_approval=True,
        payload={"draft_text": "Eski javob."},
    )

    response = await client.post(
        f"/api/action-runtime/proposals/{proposal.proposal_id}/draft",
        headers=auth_headers,
        json={
            "actor_ref": "owner:test",
            "draft_text": "Yangi javob.\nIkkinchi qator saqlansin.",
            "correlation_id": "corr:draft:edit",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lifecycle_state"] == "waiting_approval"
    assert body["reason_code"] == "owner_edited_draft"
    assert body["payload"]["draft_text"] == "Yangi javob.\nIkkinchi qator saqlansin."
    assert body["payload"]["reply_text"] == "Yangi javob.\nIkkinchi qator saqlansin."
    assert body["payload"]["draft_revision"] == 1
    assert body["payload"]["draft_edit"]["actor_ref"] == "owner:test"


async def test_action_runtime_blocks_draft_edit_after_approval(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
    customer: Customer,
    conversation: Conversation,
) -> None:
    proposal = await _proposal(
        db_session,
        workspace=workspace,
        conversation=conversation,
        customer=customer,
        proposal_id="proposal-draft-edit-approved",
        action_type="send_reply",
        requires_approval=True,
        payload={"draft_text": "Tasdiqlanadigan javob."},
    )
    approved = await client.post(
        f"/api/action-runtime/proposals/{proposal.proposal_id}/approve",
        headers=auth_headers,
        json={
            "actor_ref": "owner:test",
            "correlation_id": "corr:draft:approve",
        },
    )

    response = await client.post(
        f"/api/action-runtime/proposals/{proposal.proposal_id}/draft",
        headers=auth_headers,
        json={
            "actor_ref": "owner:test",
            "draft_text": "Kech qolgan edit.",
            "correlation_id": "corr:draft:late",
        },
    )

    assert approved.status_code == 200
    assert response.status_code == 409
    assert response.json()["detail"] == "draft_edit_not_allowed_after_approval"


def test_action_runtime_phase7_guardrails_and_docs() -> None:
    root = Path(__file__).resolve().parents[1] / "app/modules/action_runtime"
    banned_tokens = (
        "genai.Client(",
        ".models.generate_content(",
        "client.aio.models.generate_content(",
        "re.compile(",
        "re.search(",
        "keyword",
        "heuristic",
        "filename",
        "conversation.crm_state",
    )
    # agent_runtime_v2/trace.py uses re.compile to parse structural
    # <tag>body</tag> blocks in LLM trace output, not for semantic intent
    # guessing. Exclude it from the banned-tokens scan.
    structural_parser_files = {"agent_runtime_v2/trace.py"}
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if str(relative) in structural_parser_files:
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in banned_tokens):
            offenders.append(str(relative))

    docs_root = Path(__file__).resolve().parents[2] / "docs"
    roadmap = (
        docs_root / "superpowers/plans/2026-05-04-business-brain-autocrm-roadmap.md"
    ).read_text(encoding="utf-8")
    inventory = (
        docs_root / "architecture/2026-05-04-legacy-deletion-inventory.md"
    ).read_text(encoding="utf-8")

    assert offenders == []
    assert "Phase 7 landed" in roadmap
    assert "old direct action side effects are demoted" in inventory


def test_action_runtime_delivery_idempotency_key_fits_delivery_runtime_limit() -> None:
    proposal = CommercialActionProposal(
        proposal_id="proposal-" + ("a" * 32),
        workspace_id=1,
        conversation_id=2,
        customer_id=3,
        action_type="send_catalog_media",
        lifecycle_state="approved",
        execution_mode="draft_for_review",
        risk_level="low",
        requires_approval=True,
        priority="medium",
        confidence=0.91,
        reason_code="catalog_media_evidence",
        source_refs=["message:1"],
        payload={},
        idempotency_key=(
            "send_catalog_media:draft:82:454:inbound_reply:2305:"
            "send_catalog_media:catalog_product:live-iphone-15-128-blue"
        ),
        correlation_id="corr:long-key",
        trace_id="trace:long-key",
    )

    key = _execution_idempotency_key(proposal, 1)

    assert len(key) <= 120
    assert key.startswith("runtime:proposal-")
