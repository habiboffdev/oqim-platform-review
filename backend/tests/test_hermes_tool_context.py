from app.modules.agent_runtime_v2.hermes.tool_context import (
    ToolContext,
    current_tool_context,
    use_tool_context,
)


def test_tool_context_has_search_state_defaults():
    from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext

    ctx = ToolContext(
        workspace_id=1, agent_id=2, conversation_id=None,
        grounding=[], history=[],
    )
    assert ctx.search_count == 0
    assert ctx.searched_queries == set()
    # mutable + per-instance (not shared across instances)
    ctx.searched_queries.add("x")
    other = ToolContext(
        workspace_id=1, agent_id=2, conversation_id=None,
        grounding=[], history=[],
    )
    assert other.searched_queries == set()

def test_context_absent_by_default():
    assert current_tool_context.get() is None

def test_use_tool_context_sets_and_resets():
    ctx = ToolContext(workspace_id=1, agent_id=2, conversation_id=3,
                      grounding=["Narx: 250000"], history=[])
    assert current_tool_context.get() is None
    with use_tool_context(ctx):
        got = current_tool_context.get()
        assert got is ctx
        got.tool_errors.append("boom")
        assert got.tool_errors == ["boom"]
    assert current_tool_context.get() is None


def test_tool_context_defaults_context_window_to_gemini_true_window():
    from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext

    ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[]
    )
    assert ctx.context_window == 1_048_576


def test_tool_context_accepts_context_window_override():
    from app.modules.agent_runtime_v2.hermes.tool_context import ToolContext

    ctx = ToolContext(
        workspace_id=1, agent_id=1, conversation_id=None, grounding=[], history=[],
        context_window=64_000,
    )
    assert ctx.context_window == 64_000
