from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.hermes_session import HermesSessionMessageRecord, HermesSessionRecord
from app.modules.agent_runtime_v2.hermes.session_store import (
    InMemoryHermesSessionDB,
    OqimHermesSessionDB,
)
from app.modules.agent_sessions.service import AgentSessionService

pytestmark = pytest.mark.asyncio


async def test_in_memory_hermes_session_db_accepts_hermes_write_shape():
    db = InMemoryHermesSessionDB()
    db.create_session(
        session_id="oqim:agent-session:7",
        source="oqim",
        model="gemini",
        model_config={"profile": "agent", "execution_mode": "interactive"},
        system_prompt="system",
        parent_session_id=None,
    )
    db.append_message(
        session_id="oqim:agent-session:7",
        role="user",
        content="Assalomu alaykum",
        tool_name=None,
        tool_calls=None,
        tool_call_id=None,
        finish_reason=None,
    )

    assert db.get_session("oqim:agent-session:7")["id"] == "oqim:agent-session:7"
    assert db.messages["oqim:agent-session:7"][0]["content"] == "Assalomu alaykum"


async def test_oqim_hermes_session_db_persists_messages_across_instances(
    db_session,
    workspace,
    conversation,
    customer,
    agent,
):
    agent_session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=customer.id,
        agent_id=agent.id,
        channel="telegram_dm",
    )
    store = await OqimHermesSessionDB.load(
        db_session,
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
    )
    store.create_session(
        session_id=agent_session.hermes_session_id,
        source="oqim",
        model="gemini",
        model_config={"profile": "agent", "execution_mode": "interactive"},
        system_prompt="system",
    )
    store.append_message(
        session_id=agent_session.hermes_session_id,
        role="user",
        content="Assalomu alaykum",
    )
    await store.flush()
    await db_session.commit()

    reloaded = await OqimHermesSessionDB.load(
        db_session,
        workspace_id=workspace.id,
        agent_session_id=agent_session.id,
    )

    assert reloaded.get_session(agent_session.hermes_session_id)["message_count"] == 1
    assert reloaded.messages[agent_session.hermes_session_id][0]["content"] == "Assalomu alaykum"

    row = await db_session.scalar(
        select(HermesSessionRecord).where(
            HermesSessionRecord.hermes_session_id == agent_session.hermes_session_id
        )
    )
    assert row is not None
    assert row.agent_session_id == agent_session.id
    assert row.message_count == 1

    message = await db_session.scalar(
        select(HermesSessionMessageRecord).where(
            HermesSessionMessageRecord.hermes_session_id == row.id
        )
    )
    assert message is not None
    assert message.role == "user"
    assert message.content == "Assalomu alaykum"
