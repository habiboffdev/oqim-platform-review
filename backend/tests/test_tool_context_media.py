from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext


def test_current_turn_media_defaults_empty_and_is_settable():
    ctx = ToolContext(
        workspace_id=1, agent_id=2, conversation_id=3, grounding=[], history=[]
    )
    assert ctx.current_turn_media == []
    ctx.current_turn_media = ["x"]
    assert ctx.current_turn_media == ["x"]


def test_tool_context_has_live_media_text_default_none():
    import asyncio

    async def _mk():
        return ToolContext(
            workspace_id=1, agent_id=2, conversation_id=None, grounding=[], history=[],
            loop=asyncio.get_running_loop(), chain_name="FLASH_CHAIN",
            allowed_tool_names=frozenset(),
        )

    ctx = asyncio.run(_mk())
    assert ctx.live_media_text is None
