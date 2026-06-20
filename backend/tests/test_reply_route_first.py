"""Tests for Route-first behavior in the reply hot path (Slice 1)."""

from __future__ import annotations

import pytest

from app.modules.agent_runtime_v2.runtime_service import AgentRuntimeService

pytestmark = pytest.mark.asyncio


async def test_interactive_turn_compiles_profile_and_keeps_tool_grants(
    db_session, workspace, conversation, customer, agent
):
    ctx = await AgentRuntimeService(db_session).gather_turn_context(
        workspace_id=workspace.id,
        agent_id=agent.id,
        customer_message="salom",
        conversation_id=conversation.id,
    )
    profile = ctx.gathered.runtime_profile
    assert profile is not None
    assert profile.execution_mode == "interactive"
    # pilot hardening (2026-06-18): a real profile keeps its talk grants but no
    # retrieval (the seller answers from AGENT.md, not RAG).
    assert "talk.send_msgs" in profile.allowed_tool_names
    assert not any(t.startswith("knowledge_") for t in profile.allowed_tool_names)
    assert ctx.runtime_context_packet is not None


async def test_empty_message_still_compiles_real_profile(
    db_session, workspace, conversation, customer, agent
):
    ctx = await AgentRuntimeService(db_session).gather_turn_context(
        workspace_id=workspace.id,
        agent_id=agent.id,
        customer_message="",
        conversation_id=conversation.id,
    )
    profile = ctx.gathered.runtime_profile
    assert profile is not None
    assert profile.execution_mode == "interactive"
    assert "talk.send_msgs" in profile.allowed_tool_names
    assert not any(t.startswith("knowledge_") for t in profile.allowed_tool_names)
    assert ctx.gathered.agent_kind == "seller_agent"  # real kind, not the degraded custom_agent default


async def test_interactive_turn_skips_eager_grounding(
    db_session, workspace, conversation, customer, agent, monkeypatch
):
    from app.modules.agent_runtime_context.service import AgentRuntimeContextService

    captured = {}
    orig_build = AgentRuntimeContextService.build

    async def recording_build(self, request):
        captured["include_grounding"] = request.include_grounding
        return await orig_build(self, request)

    monkeypatch.setattr(AgentRuntimeContextService, "build", recording_build)

    await AgentRuntimeService(db_session).gather_turn_context(
        workspace_id=workspace.id,
        agent_id=agent.id,
        customer_message="kurs haqida ko'proq ma'lumot bering",
        conversation_id=conversation.id,
    )
    assert "include_grounding" in captured, "build() was never called"
    assert captured["include_grounding"] is False


async def test_interactive_turn_skips_catalog_authority_resolve(
    db_session, workspace, conversation, customer, agent, monkeypatch
):
    from app.modules.catalog_authority.service import CatalogAuthorityService

    calls = {"n": 0}
    orig_resolve = CatalogAuthorityService.resolve

    async def recording_resolve(self, *, workspace_id, query):
        calls["n"] += 1
        return await orig_resolve(self, workspace_id=workspace_id, query=query)

    monkeypatch.setattr(CatalogAuthorityService, "resolve", recording_resolve)

    ctx = await AgentRuntimeService(db_session).gather_turn_context(
        workspace_id=workspace.id,
        agent_id=agent.id,
        customer_message="iPhone bormi",
        conversation_id=conversation.id,
    )
    assert calls["n"] == 0
    assert ctx.gathered.authority_bundle is None


async def test_interactive_turn_keeps_session_load_without_grounding(
    db_session, workspace, conversation, customer, agent
):
    ctx = await AgentRuntimeService(db_session).gather_turn_context(
        workspace_id=workspace.id,
        agent_id=agent.id,
        customer_message="salom",
        conversation_id=conversation.id,
    )
    # Lazy interactive turn: no eager authority/style lines...
    assert ctx.gathered.grounding == []
    assert ctx.gathered.voice_examples == []
    # ...but the cheap session/packet load is intact and talk tools are granted.
    assert ctx.runtime_context_packet is not None
    assert "talk.send_msgs" in ctx.gathered.runtime_profile.allowed_tool_names
    assert not any(
        t.startswith("knowledge_")
        for t in ctx.gathered.runtime_profile.allowed_tool_names
    )


async def test_gather_context_forwards_eager_flag_when_true(
    db_session, workspace, conversation, customer, agent, monkeypatch
):
    from app.modules.agent_runtime_context.service import AgentRuntimeContextService

    captured = {}
    orig_build = AgentRuntimeContextService.build

    async def recording_build(self, request):
        captured["include_grounding"] = request.include_grounding
        return await orig_build(self, request)

    monkeypatch.setattr(AgentRuntimeContextService, "build", recording_build)

    await AgentRuntimeService(db_session)._gather_context(
        workspace_id=workspace.id,
        agent_id=agent.id,
        customer_message="iPhone bormi",
        conversation_id=conversation.id,
        enable_eager_grounding=True,
    )
    assert captured.get("include_grounding") is True
