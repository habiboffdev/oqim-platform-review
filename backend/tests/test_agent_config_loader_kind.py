"""Tests for AgentConfig.agent_kind — normalized runtime kind exposed before retrieval."""

import pytest

from app.modules.agent_runtime_v2.config_loader import AgentConfigLoader

pytestmark = pytest.mark.asyncio


async def test_config_loader_sets_agent_kind(db_session, workspace, agent):
    config = await AgentConfigLoader(db_session).load(
        workspace_id=workspace.id, agent_id=agent.id
    )
    # agent fixture leaves agent_type at its "customer" default → normalized to "seller_agent".
    assert config.agent_kind == "seller_agent"
    assert config.agent_kind != agent.agent_type  # proves normalization, not pass-through


async def test_config_loader_reads_talking_overrides_from_channel_config(
    db_session, workspace, agent
):
    agent.channel_config = {"talking": {"max_chars": 300, "allow_reaction": False}}
    await db_session.flush()

    config = await AgentConfigLoader(db_session).load(
        workspace_id=workspace.id, agent_id=agent.id
    )
    assert config.talking_overrides == {"max_chars": 300, "allow_reaction": False}


async def test_config_loader_talking_overrides_default_none(db_session, workspace, agent):
    config = await AgentConfigLoader(db_session).load(
        workspace_id=workspace.id, agent_id=agent.id
    )
    assert config.talking_overrides is None
