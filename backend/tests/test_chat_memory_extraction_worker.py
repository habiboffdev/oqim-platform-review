from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_runtime import ActionRuntime
from app.models.commercial_spine import BusinessBrainProjectionRecord
from app.models.conversation import Conversation
from app.models.message import Message, SenderType
from app.models.workspace import Workspace

pytestmark = pytest.mark.asyncio


async def test_chat_memory_extraction_worker_projects_summary_without_autocrm_extraction(
    db_session: AsyncSession,
    workspace: Workspace,
    conversation: Conversation,
) -> None:
    from app.services.chat_memory_extraction_worker import ChatMemoryExtractionWorker

    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                sender_type=SenderType.CUSTOMER.value,
                content="Salom, kurs haqida ma'lumot kerak",
                telegram_message_id=2101,
            ),
            Message(
                conversation_id=conversation.id,
                sender_type=SenderType.SELLER.value,
                content="Albatta, qaysi yo'nalish qiziq?",
                telegram_message_id=2102,
            ),
            Message(
                conversation_id=conversation.id,
                sender_type=SenderType.CUSTOMER.value,
                content="Ertaga qo'ng'iroq qiling",
                telegram_message_id=2103,
            ),
        ]
    )
    await db_session.flush()

    latest_message = await db_session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.id.desc())
        .limit(1)
    )
    assert latest_message is not None

    @asynccontextmanager
    async def db_factory():
        yield db_session

    worker = ChatMemoryExtractionWorker(
        db_factory=db_factory,
        workspace_ids_provider=lambda: [workspace.id],
        batch_size=1,
    )

    assert await worker.run_once() == 1
    assert await worker.run_once() == 0

    await db_session.refresh(conversation)
    assert "Ertaga qo'ng'iroq qiling" in (conversation.summary or "")

    projection = await db_session.scalar(
        select(BusinessBrainProjectionRecord).where(
            BusinessBrainProjectionRecord.workspace_id == workspace.id,
            BusinessBrainProjectionRecord.projection_ref
            == f"chat_memory:conversation:{conversation.id}",
        )
    )
    assert projection is not None
    assert projection.projection_type == "chat_memory_conversation"
    assert projection.state["latest_message_ref"] == f"message:{latest_message.id}"
    assert projection.state["message_count"] == 3
    assert "customer_waiting" in projection.state["tags"]
    assert "action_candidate" not in projection.state["tags"]
    assert "autocrm_projection_refs" not in projection.state
    assert "action_proposal_refs" not in projection.state
    assert "semantic_state" not in projection.state
    assert "semantic_lists" not in projection.state
    assert "projection_refs_by_type" not in projection.state

    action_state = await db_session.scalar(
        select(ActionRuntime).where(
            ActionRuntime.workspace_id == workspace.id,
            ActionRuntime.conversation_id == conversation.id,
            ActionRuntime.message_id == latest_message.id,
            ActionRuntime.action == "chat_memory_extract",
        )
    )
    assert action_state is not None
    assert action_state.state == "success"
