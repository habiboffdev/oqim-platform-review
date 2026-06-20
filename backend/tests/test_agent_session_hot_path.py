from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.models.agent_session import AgentSession, AgentSessionEvent
from app.models.conversation import Conversation
from app.models.conversation_turn_session import ConversationTurnSession
from app.models.message import Message
from app.services.inbound_pipeline import (
    process_inbound_message,
    process_message_delete,
    process_message_edit,
)

pytestmark = pytest.mark.asyncio


def _payload(*, message_id: int, text: str, chat_id: str = "777001", **extra) -> dict:
    payload = {
        "chatId": chat_id,
        "senderId": "987654",
        "senderName": "Test Customer",
        "messageId": message_id,
        "text": text,
        "date": 1700000000 + message_id,
        "isOutgoing": False,
        "mediaType": None,
        "replyToMsgId": None,
    }
    payload.update(extra)
    return payload


async def test_inbound_customer_message_creates_agent_session_event_before_reply_worker(
    db_session,
    workspace,
    agent,
):
    dispatcher = AsyncMock()
    dispatcher.enqueue_message = AsyncMock()
    dispatcher.record_agent_message = AsyncMock()

    result = await process_inbound_message(
        raw_payload=_payload(message_id=1001, text="Assalomu alaykum"),
        workspace=workspace,
        session=db_session,
        conversation_turn_runner=dispatcher,
        channel="telegram_dm",
    )

    agent_session = await db_session.scalar(
        select(AgentSession).where(
            AgentSession.workspace_id == workspace.id,
            AgentSession.conversation_id == result.conversation_id,
            AgentSession.agent_id == agent.id,
        )
    )
    assert agent_session is not None
    assert agent_session.hermes_session_id == f"oqim:agent-session:{agent_session.id}"
    assert agent_session.event_count == 1

    event = await db_session.scalar(
        select(AgentSessionEvent).where(
            AgentSessionEvent.agent_session_id == agent_session.id,
            AgentSessionEvent.message_id == result.message_id,
        )
    )
    assert event is not None
    assert event.sequence == 1
    assert event.event_type == "customer_message"
    assert event.direction == "inbound"
    assert event.text == "Assalomu alaykum"
    assert event.payload["message_id"] == result.message_id
    assert event.payload["telegram_message_id"] == 1001

    dispatcher.enqueue_message.assert_awaited_once()


async def test_quick_customer_burst_appends_to_one_agent_session_and_one_turn(
    db_session,
    workspace,
    agent,
):
    dispatcher = AsyncMock()
    dispatcher.enqueue_message = AsyncMock()
    dispatcher.record_agent_message = AsyncMock()

    texts = ["Assalomu alaykum", "sat bormi", "narxi qancha"]
    results = []
    for index, text in enumerate(texts, start=1):
        results.append(
            await process_inbound_message(
                raw_payload=_payload(message_id=1100 + index, text=text),
                workspace=workspace,
                session=db_session,
                conversation_turn_runner=dispatcher,
                channel="telegram_dm",
            )
        )

    conversation_id = results[-1].conversation_id
    agent_sessions = list(
        (
            await db_session.execute(
                select(AgentSession).where(
                    AgentSession.workspace_id == workspace.id,
                    AgentSession.conversation_id == conversation_id,
                    AgentSession.agent_id == agent.id,
                )
            )
        ).scalars().all()
    )
    assert len(agent_sessions) == 1

    events = list(
        (
            await db_session.execute(
                select(AgentSessionEvent)
                .where(AgentSessionEvent.agent_session_id == agent_sessions[0].id)
                .order_by(AgentSessionEvent.sequence.asc())
            )
        ).scalars().all()
    )
    assert [event.text for event in events] == texts
    assert [event.sequence for event in events] == [1, 2, 3]
    assert agent_sessions[0].event_count == 3

    turn_count = await db_session.scalar(
        select(func.count(ConversationTurnSession.id)).where(
            ConversationTurnSession.workspace_id == workspace.id,
            ConversationTurnSession.conversation_id == conversation_id,
            ConversationTurnSession.agent_id == agent.id,
            ConversationTurnSession.state == "open",
        )
    )
    assert turn_count == 1

    turn = await db_session.scalar(
        select(ConversationTurnSession).where(
            ConversationTurnSession.workspace_id == workspace.id,
            ConversationTurnSession.conversation_id == conversation_id,
            ConversationTurnSession.agent_id == agent.id,
        )
    )
    assert turn is not None
    assert turn.turn_revision == 3
    assert turn.latest_customer_message_id == results[-1].message_id


async def test_customer_message_actions_are_recorded_in_agent_session_transcript(
    db_session,
    workspace,
    agent,
) -> None:
    dispatcher = AsyncMock()
    dispatcher.enqueue_message = AsyncMock()
    dispatcher.record_agent_message = AsyncMock()
    result = await process_inbound_message(
        raw_payload=_payload(
            message_id=1201,
            text="forwarded price",
            replyToMsgId=1199,
            forwardFromName="Old seller",
            forwardDate=1_700_000_111,
        ),
        workspace=workspace,
        session=db_session,
        conversation_turn_runner=dispatcher,
        channel="telegram_dm",
    )
    conversation = await db_session.get(Conversation, result.conversation_id)
    message = await db_session.get(Message, result.message_id)
    assert conversation is not None
    assert message is not None

    await process_message_edit(
        session=db_session,
        workspace=workspace,
        conversation=conversation,
        message=message,
        conversation_turn_runner=dispatcher,
        edited_text="edited price",
    )
    await process_message_delete(
        session=db_session,
        workspace=workspace,
        conversation=conversation,
        message=message,
    )

    events = (
        await db_session.execute(
            select(AgentSessionEvent)
            .where(
                AgentSessionEvent.conversation_id == conversation.id,
                AgentSessionEvent.agent_id == agent.id,
            )
            .order_by(AgentSessionEvent.sequence.asc())
        )
    ).scalars().all()

    assert [event.event_type for event in events] == [
        "customer_message",
        "customer_message_edited",
        "customer_message_deleted",
    ]
    assert events[0].payload["reply_to_msg_id"] == 1199
    assert events[0].payload["forward_from_name"] == "Old seller"
    assert events[1].text == "edited price"
    assert events[1].payload["action"] == "edited"
    assert events[2].text == "[deleted]"
    assert events[2].payload["action"] == "deleted"
