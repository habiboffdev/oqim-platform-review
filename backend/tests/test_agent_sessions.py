from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.agent_session import AgentSession, AgentSessionEvent
from app.models.message import Message, SenderType
from app.models.workspace import Workspace
from app.modules.agent_sessions.service import AgentSessionService

pytestmark = pytest.mark.asyncio


async def _cleanup_concurrent_agent_session_rows(engine) -> None:
    async with engine.begin() as conn:
        workspace_ids = list(
            (
                await conn.execute(
                    select(Workspace.id).where(Workspace.name == "Concurrent Workspace")
                )
            ).scalars().all()
        )
        if not workspace_ids:
            return
        conversation_ids = list(
            (
                await conn.execute(
                    select(Conversation.id).where(Conversation.workspace_id.in_(workspace_ids))
                )
            ).scalars().all()
        )
        await conn.execute(delete(AgentSessionEvent).where(AgentSessionEvent.workspace_id.in_(workspace_ids)))
        await conn.execute(delete(AgentSession).where(AgentSession.workspace_id.in_(workspace_ids)))
        if conversation_ids:
            await conn.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
        await conn.execute(delete(Conversation).where(Conversation.workspace_id.in_(workspace_ids)))
        await conn.execute(delete(Agent).where(Agent.workspace_id.in_(workspace_ids)))
        await conn.execute(delete(Customer).where(Customer.workspace_id.in_(workspace_ids)))
        await conn.execute(delete(Workspace).where(Workspace.id.in_(workspace_ids)))


async def _customer_message(db_session, conversation, content: str) -> Message:
    message = Message(
        conversation_id=conversation.id,
        channel="telegram_dm",
        sender_type=SenderType.CUSTOMER.value,
        content=content,
        telegram_message_id=9101,
        created_at=datetime.now(UTC),
    )
    db_session.add(message)
    await db_session.flush()
    return message


async def test_agent_session_service_creates_session_and_appends_customer_event(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    message = await _customer_message(db_session, conversation, "Assalomu alaykum")

    service = AgentSessionService(db_session)
    session = await service.get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="telegram_dm",
    )
    event = await service.append_event(
        agent_session_id=session.id,
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        event_type="customer_message",
        direction="inbound",
        message_id=message.id,
        text=message.content,
        payload={"telegram_message_id": message.telegram_message_id},
        idempotency_key=f"message:{message.id}:customer_message",
    )

    assert session.session_key == f"workspace:{workspace.id}:conversation:{conversation.id}:agent:{agent.id}"
    assert session.hermes_session_id == f"oqim:agent-session:{session.id}"
    assert event.sequence == 1
    assert event.event_type == "customer_message"

    rows = (
        await db_session.execute(
            select(AgentSessionEvent)
            .where(AgentSessionEvent.agent_session_id == session.id)
            .order_by(AgentSessionEvent.sequence)
        )
    ).scalars().all()
    assert [row.event_type for row in rows] == ["customer_message"]


async def test_agent_session_service_serializes_concurrent_event_sequences(engine):
    await _cleanup_concurrent_agent_session_rows(engine)
    suffix = uuid4().hex
    try:
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            workspace = Workspace(
                phone_number=f"+998{suffix[:9]}",
                name="Concurrent Workspace",
                type="ecommerce",
                password_hash=hash_password("testpass123"),
            )
            setup.add(workspace)
            await setup.flush()
            customer = Customer(
                workspace_id=workspace.id,
                display_name="Concurrent Customer",
                telegram_id=int(suffix[:8], 16),
            )
            setup.add(customer)
            await setup.flush()
            conversation = Conversation(
                workspace_id=workspace.id,
                customer_id=customer.id,
                telegram_chat_id=int(suffix[8:16], 16),
                pipeline_stage="new",
                last_message_at=datetime.now(UTC),
            )
            agent = Agent(
                workspace_id=workspace.id,
                name="Concurrent Agent",
                is_default=True,
                persona={"role": "Sales assistant"},
                instructions="You are a test assistant.",
            )
            setup.add_all([conversation, agent])
            await setup.flush()
            service = AgentSessionService(setup)
            agent_session = await service.get_or_create(
                workspace_id=workspace.id,
                conversation_id=conversation.id,
                customer_id=customer.id,
                agent_id=agent.id,
                channel="telegram_dm",
            )
            messages = [
                Message(
                    conversation_id=conversation.id,
                    channel="telegram_dm",
                    sender_type=SenderType.CUSTOMER.value,
                    content=f"burst {index}",
                    telegram_message_id=20_000 + index,
                    created_at=datetime.now(UTC),
                )
                for index in range(12)
            ]
            setup.add_all(messages)
            await setup.commit()

        async def append(index: int) -> int:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                event = await AgentSessionService(session).append_event(
                    agent_session_id=agent_session.id,
                    workspace_id=workspace.id,
                    conversation_id=conversation.id,
                    agent_id=agent.id,
                    event_type="customer_message",
                    direction="inbound",
                    message_id=messages[index].id,
                    text=messages[index].content,
                    payload={"telegram_message_id": messages[index].telegram_message_id},
                    idempotency_key=f"message:{messages[index].id}:customer_message",
                )
                await session.commit()
                return event.sequence

        sequences = await asyncio.gather(*(append(index) for index in range(len(messages))))

        async with AsyncSession(engine, expire_on_commit=False) as verify:
            rows = (
                await verify.execute(
                    select(AgentSessionEvent)
                    .where(AgentSessionEvent.agent_session_id == agent_session.id)
                    .order_by(AgentSessionEvent.sequence.asc())
                )
            ).scalars().all()
            stored_session = await verify.get(AgentSession, agent_session.id)

        assert sorted(sequences) == list(range(1, len(messages) + 1))
        assert [event.sequence for event in rows] == list(range(1, len(messages) + 1))
        assert sorted(event.text for event in rows) == sorted(f"burst {index}" for index in range(len(messages)))
        assert stored_session is not None
        assert stored_session.event_count == len(messages)
    finally:
        await _cleanup_concurrent_agent_session_rows(engine)
