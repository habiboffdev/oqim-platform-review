from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.hermes_session import HermesSessionRecord
from app.modules.agent_runtime_v2.hermes.session_store import OqimHermesSessionDB
from app.modules.agent_runtime_v2.session_compaction import (
    CompactionResult,
    SessionCompactionService,
)
from app.modules.agent_sessions.service import AgentSessionService

pytestmark = pytest.mark.asyncio


async def _seed_messages(db_session, workspace, agent, conversation, *, n_messages: int):
    """Create the agent_session and populate its hermes_session with `n_messages`
    via the store API (the only correct way to write the linked
    hermes_sessions + hermes_session_messages rows)."""
    agent_session = await AgentSessionService(db_session).get_or_create(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        customer_id=conversation.customer_id,
        agent_id=agent.id,
        channel="telegram_dm",
    )
    store = await OqimHermesSessionDB.load(
        db_session, workspace_id=workspace.id, agent_session_id=agent_session.id
    )
    sid = agent_session.hermes_session_id
    if sid not in store.sessions:
        store.create_session(sid, source="oqim")
    for i in range(n_messages):
        store.append_message(
            session_id=sid,
            role="user" if i % 2 == 0 else "assistant",
            content=f"message {i} " * 40,
        )
    await store.flush()
    await db_session.flush()
    return agent_session


def _patch_summary(monkeypatch):
    """Make Hermes's middle-turn summarizer deterministic (no real LLM).

    Patches `agent.context_compressor.call_llm` (the single LLM call inside both
    the Hermes default summarizer and OqimContextCompressor._generate_kind_summary)
    so the kind-aware prompt assembly still runs and can be inspected, while no
    network call happens. Returns the captured prompts to the caller."""
    import types

    import agent.context_compressor as cc

    captured: list[str] = []

    def _fake_call_llm(**kwargs):
        captured.append(kwargs["messages"][0]["content"])
        msg = types.SimpleNamespace(content="[summary of earlier turns]")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setattr(cc, "call_llm", _fake_call_llm)
    return captured


async def _make_agent(db_session, workspace, *, agent_type: str) -> Agent:
    ag = Agent(
        workspace_id=workspace.id,
        name=f"{agent_type} agent",
        is_default=False,
        agent_type=agent_type,
        trust_mode="disabled",
        auto_send_threshold=0.85,
        tools_config={"enabled_tools": ["knowledge_search_catalog"]},
        knowledge_config={"use_catalog": True, "use_knowledge": True},
        channel_config={"mode": "dm", "chat_ids": []},
    )
    db_session.add(ag)
    await db_session.flush()
    return ag


class TestSessionCompaction:
    async def test_missing_agent_session_raises(self, db_session, workspace):
        with pytest.raises(LookupError):
            await SessionCompactionService(db_session).compact(
                workspace_id=workspace.id, agent_id=999999, conversation_id=999999,
                apply=True,
            )

    async def test_apply_compacts_creates_new_session_and_repoints(
        self, db_session, workspace, agent, conversation, monkeypatch
    ):
        _patch_summary(monkeypatch)
        agent_session = await _seed_messages(
            db_session, workspace, agent, conversation, n_messages=40
        )
        old_id = agent_session.hermes_session_id

        result = await SessionCompactionService(db_session).compact(
            workspace_id=workspace.id, agent_id=agent.id, conversation_id=conversation.id,
            apply=True,
        )

        assert isinstance(result, CompactionResult)
        assert result.applied is True and result.noop is False
        assert result.after_messages < result.before_messages
        await db_session.refresh(agent_session)
        assert agent_session.hermes_session_id == result.new_session_id
        assert result.new_session_id != old_id
        old_row = (await db_session.execute(
            select(HermesSessionRecord).where(
                HermesSessionRecord.agent_session_id == agent_session.id,
                HermesSessionRecord.hermes_session_id == old_id,
            )
        )).scalar_one()
        assert old_row.ended_reason == "compression"

    async def test_dry_run_persists_nothing(
        self, db_session, workspace, agent, conversation, monkeypatch
    ):
        _patch_summary(monkeypatch)
        agent_session = await _seed_messages(
            db_session, workspace, agent, conversation, n_messages=40
        )
        old_id = agent_session.hermes_session_id

        result = await SessionCompactionService(db_session).compact(
            workspace_id=workspace.id, agent_id=agent.id, conversation_id=conversation.id,
            apply=False,
        )

        assert result.applied is False
        assert result.before_messages == 40
        await db_session.refresh(agent_session)
        assert agent_session.hermes_session_id == old_id

    async def test_short_session_is_noop(
        self, db_session, workspace, agent, conversation, monkeypatch
    ):
        _patch_summary(monkeypatch)
        agent_session = await _seed_messages(
            db_session, workspace, agent, conversation, n_messages=4
        )
        old_id = agent_session.hermes_session_id

        result = await SessionCompactionService(db_session).compact(
            workspace_id=workspace.id, agent_id=agent.id, conversation_id=conversation.id,
            apply=True,
        )

        assert result.noop is True and result.applied is False
        await db_session.refresh(agent_session)
        assert agent_session.hermes_session_id == old_id

    async def test_drives_hermes_native_compress_context(
        self, db_session, workspace, agent, conversation, monkeypatch
    ):
        _patch_summary(monkeypatch)
        await _seed_messages(db_session, workspace, agent, conversation, n_messages=40)

        import run_agent
        calls = {"n": 0}
        real = run_agent.AIAgent._compress_context

        def _spy(self, *a, **k):
            calls["n"] += 1
            return real(self, *a, **k)

        monkeypatch.setattr(run_agent.AIAgent, "_compress_context", _spy)

        await SessionCompactionService(db_session).compact(
            workspace_id=workspace.id, agent_id=agent.id, conversation_id=conversation.id,
            apply=True,
        )
        assert calls["n"] >= 1

    async def test_seller_agent_compacts_with_seller_template(
        self, db_session, workspace, conversation, monkeypatch
    ):
        captured = _patch_summary(monkeypatch)
        seller = await _make_agent(db_session, workspace, agent_type="seller")
        await _seed_messages(
            db_session, workspace, seller, conversation, n_messages=40
        )

        await SessionCompactionService(db_session).compact(
            workspace_id=workspace.id, agent_id=seller.id,
            conversation_id=conversation.id, apply=True,
        )

        assert captured, "summarizer was not invoked"
        prompt = captured[0]
        assert "## Sales Objective" in prompt
        assert "## Handoff and Next Step" in prompt
        assert "## Relevant Files" not in prompt

    async def test_custom_agent_compacts_with_personal_template(
        self, db_session, workspace, conversation, monkeypatch
    ):
        captured = _patch_summary(monkeypatch)
        custom = await _make_agent(db_session, workspace, agent_type="custom")
        await _seed_messages(
            db_session, workspace, custom, conversation, n_messages=40
        )

        await SessionCompactionService(db_session).compact(
            workspace_id=workspace.id, agent_id=custom.id,
            conversation_id=conversation.id, apply=True,
        )

        assert captured, "summarizer was not invoked"
        prompt = captured[0]
        assert "## Current Focus" in prompt
        assert "## Relevant Files" not in prompt
