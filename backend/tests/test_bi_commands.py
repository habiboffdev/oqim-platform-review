from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.customer import Customer
from app.models.conversation import Conversation
from app.models.workspace import Workspace


pytestmark = pytest.mark.asyncio


async def test_bi_command_creates_custom_agent_proposal_before_runtime_mutation(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    workspace: Workspace,
) -> None:
    response = await client.post(
        "/api/bi-promoter/commands",
        headers=auth_headers,
        json={
            "command_kind": "create_agent",
            "agent_name": "Uchrashuv BI agenti",
            "command_text": "Mijoz uchrashuv so'rasa, vaqt tanlash va keyingi qadamni taklif qiladigan agent yarat.",
            "permission_mode": "ask_always",
            "brain_scopes": ["knowledge", "rules", "voice"],
            "tool_scopes": ["telegram.read_messages"],
            "trigger_sources": [],
            "correlation_id": "test:bi-command:create-agent",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["schema_version"] == "bi_command_result.v1"
    assert body["status"] == "proposal_created"
    assert body["action_route"] == "/actions"
    assert body["message_uz"]
    proposal = body["proposal"]
    assert proposal["action_type"] == "agent.create_custom_package"
    assert proposal["lifecycle_state"] == "waiting_approval"
    assert proposal["requires_approval"] is True
    assert proposal["payload"]["custom_agent_package"]["name"] == "Uchrashuv BI agenti"
    assert proposal["payload"]["actor_ref"] == "agent"

    before_approval = await db_session.scalar(
        select(Agent).where(
            Agent.workspace_id == workspace.id,
            Agent.name == "Uchrashuv BI agenti",
        )
    )
    assert before_approval is None

    blocked = await client.post(
        f"/api/action-runtime/proposals/{proposal['proposal_id']}/execute",
        headers=auth_headers,
        json={
            "actor_ref": "owner:test",
            "correlation_id": "test:bi-command:blocked",
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["reason_code"] == "approval_required_before_execution"

    approved = await client.post(
        f"/api/action-runtime/proposals/{proposal['proposal_id']}/approve",
        headers=auth_headers,
        json={
            "actor_ref": "owner:test",
            "correlation_id": "test:bi-command:approve",
        },
    )
    assert approved.status_code == 200

    executed = await client.post(
        f"/api/action-runtime/proposals/{proposal['proposal_id']}/execute",
        headers=auth_headers,
        json={
            "actor_ref": "owner:test",
            "correlation_id": "test:bi-command:execute",
        },
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed"

    agent = await db_session.scalar(
        select(Agent).where(
            Agent.workspace_id == workspace.id,
            Agent.name == "Uchrashuv BI agenti",
        )
    )
    assert agent is not None
    assert agent.agent_type == "custom"


async def test_bi_command_create_agent_is_idempotent(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "command_kind": "create_agent",
        "agent_name": "Kanal nazoratchisi",
        "command_text": "Telegram kanaldan foydali yangiliklarni kuzatadigan agent taklif qil.",
        "correlation_id": "test:bi-command:idempotent",
    }
    first = await client.post(
        "/api/bi-promoter/commands",
        headers=auth_headers,
        json=payload,
    )
    second = await client.post(
        "/api/bi-promoter/commands",
        headers=auth_headers,
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["status"] == "proposal_created"
    assert second.json()["status"] == "proposal_reused"
    assert first.json()["proposal"]["proposal_id"] == second.json()["proposal"]["proposal_id"]


async def test_bi_command_creates_owner_task_proposal_without_parallel_queue(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/bi-promoter/commands",
        headers=auth_headers,
        json={
            "command_kind": "create_owner_task",
            "command_text": "Ertaga Hamid bilan uchrashuv vaqtini egasi tasdiqlasin.",
            "task_title": "Hamid bilan uchrashuv vaqtini tasdiqlash",
            "task_detail": "Ertaga 11:00 vaqt to'g'ri kelishini egasi tekshiradi.",
            "task_kind": "meeting",
            "customer_label": "Hamid",
            "due_at": "2026-05-18T06:00:00+00:00",
            "correlation_id": "test:bi-command:create-owner-task",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["schema_version"] == "bi_command_result.v1"
    assert body["command_kind"] == "create_owner_task"
    assert body["status"] == "proposal_created"
    assert body["action_route"] == "/actions"
    proposal = body["proposal"]
    assert proposal["action_type"] == "create_business_task"
    assert proposal["lifecycle_state"] == "proposed"
    assert proposal["requires_approval"] is False
    assert proposal["payload"]["owner_task"]["task_kind"] == "meeting"
    assert proposal["payload"]["owner_task"]["title"] == "Hamid bilan uchrashuv vaqtini tasdiqlash"
    assert proposal["source_refs"] == ["bi_command:create_owner_task"]

    projection = await client.get("/api/action-runtime/tasks", headers=auth_headers)

    assert projection.status_code == 200
    proposed = projection.json()["proposed"]
    assert proposed[0]["proposal_id"] == proposal["proposal_id"]
    assert proposed[0]["title"] == "Hamid bilan uchrashuv vaqtini tasdiqlash"
    assert proposed[0]["kind"] == "meeting"
    assert proposed[0]["customer_label"] == "Hamid"
    assert proposed[0]["source_label"] == "BI buyrug'i: create owner task"
    assert proposed[0]["evidence_labels"] == ["BI: create owner task"]


async def test_bi_command_owner_task_is_idempotent(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    payload = {
        "command_kind": "create_owner_task",
        "command_text": "Bugun yetkazishni tekshirish uchun egaga vazifa och.",
        "task_title": "Yetkazish holatini tekshirish",
        "task_detail": "Mijoz kutayotgan buyurtma holatini tekshirish kerak.",
        "task_kind": "delivery",
        "customer_label": "Mijoz",
        "correlation_id": "test:bi-command:owner-task-idempotent",
    }
    first = await client.post(
        "/api/bi-promoter/commands",
        headers=auth_headers,
        json=payload,
    )
    second = await client.post(
        "/api/bi-promoter/commands",
        headers=auth_headers,
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["status"] == "proposal_created"
    assert second.json()["status"] == "proposal_reused"
    assert first.json()["proposal"]["proposal_id"] == second.json()["proposal"]["proposal_id"]


async def test_bi_command_creates_reply_action_proposal_from_owner_task(
    client: AsyncClient,
    auth_headers: dict[str, str],
    customer: Customer,
    conversation: Conversation,
) -> None:
    response = await client.post(
        "/api/bi-promoter/commands",
        headers=auth_headers,
        json={
            "command_kind": "create_reply_action",
            "command_text": "Mijozga uchrashuv vaqti bo'yicha javob taklif qil.",
            "conversation_id": conversation.id,
            "customer_id": customer.id,
            "customer_label": "Madina",
            "reply_text": "Ha, ertaga 11:00 mos. Uchrashuvni tasdiqlab qo'yaman.",
            "source_proposal_id": "proposal-task-meeting",
            "correlation_id": "test:bi-command:create-reply-action",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["schema_version"] == "bi_command_result.v1"
    assert body["command_kind"] == "create_reply_action"
    assert body["status"] == "proposal_created"
    assert body["message_uz"] == "Javob taklifi Amallarga qo'shildi."
    proposal = body["proposal"]
    assert proposal["action_type"] == "send_reply"
    assert proposal["lifecycle_state"] == "waiting_approval"
    assert proposal["execution_mode"] == "draft_for_review"
    assert proposal["requires_approval"] is True
    assert proposal["executor_runtime"] == "telegram_tool_runtime"
    assert proposal["payload"]["draft_text"] == "Ha, ertaga 11:00 mos. Uchrashuvni tasdiqlab qo'yaman."
    assert proposal["payload"]["customer_name"] == "Madina"
    assert proposal["payload"]["source_task_proposal_id"] == "proposal-task-meeting"
    assert proposal["source_refs"] == [
        "bi_command:create_reply_action",
        f"conversation:{conversation.id}",
        "owner_task:proposal-task-meeting",
    ]

    inbox = await client.get("/api/action-runtime/inbox", headers=auth_headers)

    assert inbox.status_code == 200
    assert proposal["proposal_id"] in {
        item["proposal_id"] for item in inbox.json()["items"]
    }


async def test_bi_command_reply_action_is_idempotent(
    client: AsyncClient,
    auth_headers: dict[str, str],
    customer: Customer,
    conversation: Conversation,
) -> None:
    payload = {
        "command_kind": "create_reply_action",
        "command_text": "Mijozga qayta aloqa javobini taklif qil.",
        "conversation_id": conversation.id,
        "customer_id": customer.id,
        "customer_label": "Jasur",
        "reply_text": "Salom, buyurtmangiz bo'yicha tekshirib, tez orada yozaman.",
        "source_proposal_id": "proposal-task-follow-up",
        "correlation_id": "test:bi-command:reply-action-idempotent",
    }
    first = await client.post(
        "/api/bi-promoter/commands",
        headers=auth_headers,
        json=payload,
    )
    second = await client.post(
        "/api/bi-promoter/commands",
        headers=auth_headers,
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["status"] == "proposal_created"
    assert second.json()["status"] == "proposal_reused"
    assert first.json()["proposal"]["proposal_id"] == second.json()["proposal"]["proposal_id"]
