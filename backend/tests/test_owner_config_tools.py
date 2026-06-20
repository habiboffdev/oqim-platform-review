"""Owner config tools (spike #439) — owner.edit_doc emits an approval proposal.

The owner/setup agent never mutates business state directly: every config edit
becomes a CommercialActionProposal (requires_approval=True) that flows through the
existing Action Runtime → owner-bot card → approve → execute → audit seam.
"""

from contextlib import asynccontextmanager

from sqlalchemy import select

from app.models.commercial_action import CommercialActionProposalRecord


def test_owner_tools_registered_and_granted_to_setup_agent():
    """A tool coded but not registered/granted is unreachable by the agent."""
    from tools.registry import registry

    from app.modules.agent_runtime_v2.hermes.oqim_tools import register_oqim_tools
    from app.modules.agent_runtime_v2.runtime_profile import _SETUP_AGENT_TOOLS

    register_oqim_tools()
    oqim_names = set(registry.get_tool_names_for_toolset("oqim"))
    for tool in ("owner.edit_doc", "media.store", "media.list"):
        assert tool in oqim_names, f"{tool} not registered in oqim toolset"
        assert tool in _SETUP_AGENT_TOOLS, f"{tool} not granted to setup agent"


async def test_owner_edit_doc_emits_waiting_approval_proposal(
    db_session, workspace, agent, monkeypatch
):
    from app.modules.agent_runtime_v2.hermes.oqim_tools import _owner_edit_doc_async

    @asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.oqim_tools.async_session", fake_session
    )

    out = await _owner_edit_doc_async(
        workspace_id=workspace.id,
        agent_id=agent.id,
        section_key="role_mission",
        body="Yangi rol matni",
    )

    assert out["status"] == "ok"
    assert out["proposal_id"]

    prop = await db_session.scalar(
        select(CommercialActionProposalRecord).where(
            CommercialActionProposalRecord.workspace_id == workspace.id
        )
    )
    assert prop is not None
    assert prop.action_type == "agent.update_owner_config"
    assert prop.requires_approval is True
    assert prop.lifecycle_state == "proposed"
    assert prop.conversation_id == 0
    assert prop.customer_id == 0
    assert prop.payload["op"] == "edit_doc"
    assert prop.payload["agent_id"] == agent.id
    assert prop.payload["section_key"] == "role_mission"
    assert prop.payload["body"] == "Yangi rol matni"


async def test_owner_edit_doc_dedupes_identical_edit(db_session, workspace, agent, monkeypatch):
    """A re-emitted identical edit must dedupe to one proposal (no double card)."""
    from sqlalchemy import func

    from app.modules.agent_runtime_v2.hermes.oqim_tools import _owner_edit_doc_async

    @asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(
        "app.modules.agent_runtime_v2.hermes.oqim_tools.async_session", fake_session
    )

    kwargs = dict(
        workspace_id=workspace.id,
        agent_id=agent.id,
        section_key="role_mission",
        body="Aynan bir xil matn",
    )
    first = await _owner_edit_doc_async(**kwargs)
    second = await _owner_edit_doc_async(**kwargs)

    assert first["deduped"] is False
    assert second["deduped"] is True
    assert first["proposal_id"] == second["proposal_id"]

    count = await db_session.scalar(
        select(func.count())
        .select_from(CommercialActionProposalRecord)
        .where(CommercialActionProposalRecord.workspace_id == workspace.id)
    )
    assert count == 1
