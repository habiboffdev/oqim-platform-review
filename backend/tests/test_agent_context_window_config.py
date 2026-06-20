"""Per-agent context-window override flows from channel_config through the loader."""
from __future__ import annotations

import pytest

from app.modules.agent_runtime_v2.config_loader import AgentConfigLoader

pytestmark = pytest.mark.asyncio


class TestAgentConfigLoaderContextWindow:
    async def test_context_window_override_from_channel_config(
        self, db_session, workspace, agent
    ):
        agent.channel_config = {"context": {"window_tokens": 64_000}}
        await db_session.flush()
        config = await AgentConfigLoader(db_session).load(
            workspace_id=workspace.id, agent_id=agent.id
        )
        assert config.context_window == 64_000

    async def test_no_context_config_defaults_to_gemini_true_window(
        self, db_session, workspace, agent
    ):
        # The default `agent` fixture has no "context" key in channel_config.
        config = await AgentConfigLoader(db_session).load(
            workspace_id=workspace.id, agent_id=agent.id
        )
        assert config.context_window == 1_048_576

    async def test_out_of_range_window_is_clamped(self, db_session, workspace, agent):
        agent.channel_config = {"context": {"window_tokens": 9_000_000}}
        await db_session.flush()
        config = await AgentConfigLoader(db_session).load(
            workspace_id=workspace.id, agent_id=agent.id
        )
        assert config.context_window == 1_048_576
