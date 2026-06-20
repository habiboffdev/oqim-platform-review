"""Host-resume: the engine passes stored Hermes session messages back as conversation_history.

Packaged Hermes builds its model input exclusively from the conversation_history
parameter of run_conversation (run_agent.py: messages = list(conversation_history)).
OQIM loads prior turns into the session store but historically never passed them
back, so every turn ran cold (history=0). These tests prove prior session messages
reach the model on the next turn — asserting on the contents the model receives,
mirroring test_adapter_feeds_telegram_chat_history_into_the_turn.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.brain.llm import LLMToolResponse
from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.hermes.engine import HermesEngineAdapter
from app.modules.agent_runtime_v2.hermes.session_store import InMemoryHermesSessionDB
from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler

pytestmark = pytest.mark.asyncio

_SESSION_ID = "oqim:agent-session:7"


def _cfg() -> AgentConfig:
    return AgentConfig(
        agent_id=2,
        workspace_id=1,
        name="Sotuvchi",
        trust_mode="disabled",
        auto_send_threshold=0.85,
        agent_md="# Sotuvchi\nSen sotuvchisan.",
    )


def _profile(agent_kind: str = "seller"):
    return RuntimeProfileCompiler().compile_agent(config=_cfg(), agent_kind=agent_kind)


def _session_db_with(*messages: dict) -> InMemoryHermesSessionDB:
    session_db = InMemoryHermesSessionDB()
    session_db.create_session(_SESSION_ID, source="oqim")
    for message in messages:
        session_db.append_message(session_id=_SESSION_ID, **message)
    return session_db


async def test_engine_passes_prior_session_messages_as_conversation_history():
    session_db = _session_db_with(
        {"role": "user", "content": "Qizil mahsulot bormi?"},
        {"role": "assistant", "content": "Ha, qizil mahsulot bor."},
    )
    seen: dict = {}

    async def _fake_gwt(**kw):
        seen["contents"] = kw.get("contents")
        return LLMToolResponse(
            text="Ha, hali ham bor.", tool_calls=[], model_used="m", provider="gemini"
        )

    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt
    ):
        await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(),
            customer_message="Yana bormi?",
            grounding=[],
            history=[],
            agent_kind="seller",
            hermes_session_id=_SESSION_ID,
            session_db=session_db,
        )

    blob = json.dumps(seen.get("contents"), ensure_ascii=False)
    # Prior session turns reached the model via Hermes-native resume...
    assert "Qizil mahsulot bormi?" in blob
    assert "Ha, qizil mahsulot bor." in blob
    # ...alongside the current turn.
    assert "Yana bormi?" in blob


async def test_engine_filters_system_messages_from_resume():
    session_db = _session_db_with(
        {"role": "system", "content": "INTERNAL-MARKER-SHOULD-NOT-REPLAY"},
        {"role": "user", "content": "salom"},
    )
    seen: dict = {}

    async def _fake_gwt(**kw):
        seen["contents"] = kw.get("contents")
        return LLMToolResponse(
            text="Assalomu alaykum!", tool_calls=[], model_used="m", provider="gemini"
        )

    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt
    ):
        await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(),
            customer_message="yana salom",
            grounding=[],
            history=[],
            agent_kind="seller",
            hermes_session_id=_SESSION_ID,
            session_db=session_db,
        )

    blob = json.dumps(seen.get("contents"), ensure_ascii=False)
    # System material comes from the composed system prompt, never replayed history.
    assert "INTERNAL-MARKER-SHOULD-NOT-REPLAY" not in blob
    assert "salom" in blob


async def test_engine_runs_clean_when_session_has_no_prior_messages():
    session_db = InMemoryHermesSessionDB()
    session_db.create_session(_SESSION_ID, source="oqim")

    async def _fake_gwt(**kw):
        return LLMToolResponse(
            text="Assalomu alaykum!", tool_calls=[], model_used="m", provider="gemini"
        )

    with patch(
        "app.modules.agent_runtime_v2.hermes.openai_shim.generate_with_tools", _fake_gwt
    ):
        out = await HermesEngineAdapter().run(
            config=_cfg(),
            profile=_profile(),
            customer_message="salom",
            grounding=[],
            history=[],
            agent_kind="seller",
            hermes_session_id=_SESSION_ID,
            session_db=session_db,
        )

    assert out.reply_text == "Assalomu alaykum!"
