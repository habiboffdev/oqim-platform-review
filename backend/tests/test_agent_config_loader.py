"""Tests for AgentConfigLoader (P5a Slice 1) — loads agent policy + rendered docs."""

import pytest

from app.models.agent import Agent, TrustMode
from app.modules.agent_runtime_v2.config_loader import AgentConfig, AgentConfigLoader
from app.modules.brain.agent_document import AgentDocumentBuilderService
from app.modules.brain.contracts import AgentSectionDraft

pytestmark = pytest.mark.asyncio


async def _create_agent(
    db_session,
    workspace,
    *,
    name="Sotuvchi",
    trust_mode=TrustMode.AUTOPILOT.value,
    threshold=0.8,
) -> Agent:
    agent = Agent(
        workspace_id=workspace.id,
        name=name,
        trust_mode=trust_mode,
        auto_send_threshold=threshold,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


async def test_load_returns_policy_and_rendered_markdown(db_session, workspace):
    agent = await _create_agent(db_session, workspace)
    await AgentDocumentBuilderService(db_session).persist_section(
        workspace_id=workspace.id,
        agent_id=agent.id,
        draft=AgentSectionDraft(section_key="role_mission", body="Mijozlarga javob beradi."),
    )
    await db_session.flush()

    config = await AgentConfigLoader(db_session).load(
        workspace_id=workspace.id, agent_id=agent.id
    )

    assert isinstance(config, AgentConfig)
    assert config.agent_id == agent.id
    assert config.trust_mode == TrustMode.AUTOPILOT.value
    assert config.auto_send_threshold == 0.8
    assert "Mijozlarga javob beradi." in config.agent_md


async def test_load_raises_for_cross_workspace_agent(db_session, workspace, workspace_b):
    agent = await _create_agent(db_session, workspace)
    with pytest.raises(LookupError):
        await AgentConfigLoader(db_session).load(
            workspace_id=workspace_b.id, agent_id=agent.id
        )


async def test_load_raises_for_missing_agent(db_session, workspace):
    with pytest.raises(LookupError):
        await AgentConfigLoader(db_session).load(
            workspace_id=workspace.id, agent_id=999999
        )


def test_resolve_crm_routing_reads_block_or_none():
    from app.modules.agent_runtime_v2.config_loader import resolve_crm_routing

    cfg = {"crm": {"routing": {
        "pipelines": {"sales": 111, "consulting": 222},
        "default": "sales", "instructions": "consulting -> consulting"}}}
    r = resolve_crm_routing(cfg)
    assert r == {
        "pipelines": {"sales": "111", "consulting": "222"},
        "default": "sales", "instructions": "consulting -> consulting"}
    assert resolve_crm_routing({}) is None
    assert resolve_crm_routing({"crm": {}}) is None
    assert resolve_crm_routing({"crm": {"routing": {"pipelines": {}}}}) is None
    assert resolve_crm_routing(None) is None
