import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.modules.agent_runtime_v2.owner_agent import ensure_owner_agent
from app.modules.agent_runtime_v2.runtime_profile import (
    _SETUP_AGENT_TOOLS,
    _default_execution_mode,
)

pytestmark = pytest.mark.asyncio


def test_owner_kind_maps_to_setup_execution_mode():
    assert _default_execution_mode("owner") == "setup"
    # Existing mappings unchanged:
    assert _default_execution_mode("setup_agent") == "setup"
    assert _default_execution_mode("seller") == "interactive"


def test_owner_profile_grants_edit_and_read():
    assert "owner.edit_doc" in _SETUP_AGENT_TOOLS  # write (approval-gated)
    assert "ask" in _SETUP_AGENT_TOOLS  # read


async def test_ensure_owner_agent_creates_one_then_is_idempotent(db_session, workspace):
    first = await ensure_owner_agent(db_session, workspace.id)
    await db_session.flush()
    assert first.agent_type == "owner"
    assert first.workspace_id == workspace.id
    assert first.is_default is False

    second = await ensure_owner_agent(db_session, workspace.id)
    await db_session.flush()
    assert second.id == first.id  # idempotent: no duplicate

    rows = (
        await db_session.execute(
            select(Agent).where(
                Agent.workspace_id == workspace.id, Agent.agent_type == "owner"
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_ensure_owner_agent_is_workspace_scoped(db_session, workspace, workspace_b):
    a = await ensure_owner_agent(db_session, workspace.id)
    b = await ensure_owner_agent(db_session, workspace_b.id)
    await db_session.flush()
    assert a.id != b.id
    assert a.workspace_id == workspace.id
    assert b.workspace_id == workspace_b.id


def test_owner_operator_prompt_is_operator_not_seller():
    from app.modules.agent_runtime_v2.reply_runtime import (
        OWNER_OPERATOR_PROMPT,
        compose_owner_operator_system_prompt,
    )

    out = compose_owner_operator_system_prompt("# SELLER DOC: sell the course")
    # operator identity present (its own brain, not the seller hermes_reply)
    assert OWNER_OPERATOR_PROMPT.split("\n", 1)[0] in out
    assert "operator" in out.lower()
    # NOT the seller composition's persona framing
    assert "Rendered AGENT.md:" not in out
    # the business doc rides as a managed artifact it edits, NOT as its persona
    assert "NOT instructions for you" in out
    assert "sell the course" in out  # still available as context


def test_setup_profile_uses_owner_chain_and_gemini_35_flash():
    from app.modules.agent_runtime_v2.config_loader import AgentConfig
    from app.modules.agent_runtime_v2.context_config import CONTEXT_WINDOW_DEFAULT
    from app.modules.agent_runtime_v2.runtime_profile import RuntimeProfileCompiler

    config = AgentConfig(
        agent_id=4,
        workspace_id=1,
        name="Owner",
        trust_mode="disabled",
        auto_send_threshold=0.0,
        agent_md="# AGENT",
        context_window=CONTEXT_WINDOW_DEFAULT,
    )
    profile = RuntimeProfileCompiler().compile_agent(config=config, agent_kind="owner")
    assert profile.execution_mode == "setup"
    assert profile.hermes_settings.chain == "OWNER_CHAIN"
    assert profile.hermes_settings.model == "gemini-3.5-flash"


def test_owner_agent_type_resolves_to_setup_execution_mode_end_to_end():
    """The FULL chain: agent_type='owner' -> _agent_kind -> _default_execution_mode.
    Regression for the live bug where 'owner' fell through _agent_kind to
    'custom_agent' -> interactive, so the owner agent ran the seller profile."""
    from app.models.agent import Agent
    from app.modules.agent_runtime_context.service import _agent_kind
    from app.modules.agent_runtime_v2.runtime_profile import _default_execution_mode

    kind = _agent_kind(Agent(agent_type="owner"))
    assert _default_execution_mode(kind) == "setup"
