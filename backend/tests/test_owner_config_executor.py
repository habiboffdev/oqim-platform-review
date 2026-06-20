"""Action Runtime executor for agent.update_owner_config (spike #439).

The owner-config approval seam: a proposed edit goes proposed → waiting_approval
→ approved → executed, and only at execute() does the AGENT.md section actually
change. Approve alone never mutates business state.
"""

import pytest

from app.modules.action_runtime.service import ActionRuntimeService
from app.modules.agent_documents.service import AgentDocumentService
from app.modules.commercial_spine.contracts import CommercialActionProposal
from app.modules.commercial_spine.repository import CommercialSpineRepository

pytestmark = pytest.mark.asyncio


def _owner_config_proposal(*, workspace_id: int, agent_id: int) -> CommercialActionProposal:
    return CommercialActionProposal(
        proposal_id="owner_config:exec-1",
        workspace_id=workspace_id,
        conversation_id=0,
        customer_id=0,
        action_type="agent.update_owner_config",
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level="medium",
        requires_approval=True,
        priority="medium",
        confidence=1.0,
        reason_code="spike",
        source_refs=["owner:test"],
        idempotency_key="idem:owner-cfg-1",
        correlation_id="corr:1",
        trace_id="trace:1",
        payload={
            "op": "edit_doc",
            "agent_id": agent_id,
            "section_key": "role_mission",
            "body": "Yangilangan rol matni",
        },
    )


async def test_owner_config_executor_applies_agent_md_edit(db_session, workspace, agent):
    prop = _owner_config_proposal(workspace_id=workspace.id, agent_id=agent.id)
    await CommercialSpineRepository(db_session).persist_action_proposal(prop)

    svc = ActionRuntimeService(CommercialSpineRepository(db_session))
    await svc.process_proposal(workspace_id=workspace.id, proposal_id=prop.proposal_id)
    await svc.approve(
        workspace_id=workspace.id,
        proposal_id=prop.proposal_id,
        actor_ref="owner:test",
        correlation_id="c",
    )

    # The load-bearing invariant of the approval seam: approve alone must NOT
    # mutate AGENT.md — only execute() writes the section.
    pre = await AgentDocumentService(db_session).list_sections(
        workspace_id=workspace.id,
        document_kind="agent",
        subject_type="agent",
        subject_id=agent.id,
    )
    assert not any(s.section_key == "role_mission" for s in pre)

    execu = await svc.execute(
        workspace_id=workspace.id, proposal_id=prop.proposal_id, correlation_id="c"
    )

    assert execu.status == "executed"
    assert execu.reason_code == "owner_config_applied"

    sections = await AgentDocumentService(db_session).list_sections(
        workspace_id=workspace.id,
        document_kind="agent",
        subject_type="agent",
        subject_id=agent.id,
    )
    role = next(s for s in sections if s.section_key == "role_mission")
    assert "Yangilangan rol matni" in role.body


async def test_owner_config_executor_blocks_cross_workspace_agent(
    db_session, workspace, workspace_b, agent
):
    # payload references an agent from workspace A, but proposal is in workspace B
    prop = _owner_config_proposal(workspace_id=workspace_b.id, agent_id=agent.id)
    await CommercialSpineRepository(db_session).persist_action_proposal(prop)

    svc = ActionRuntimeService(CommercialSpineRepository(db_session))
    await svc.process_proposal(workspace_id=workspace_b.id, proposal_id=prop.proposal_id)
    await svc.approve(
        workspace_id=workspace_b.id,
        proposal_id=prop.proposal_id,
        actor_ref="owner:test",
        correlation_id="c",
    )
    execu = await svc.execute(
        workspace_id=workspace_b.id, proposal_id=prop.proposal_id, correlation_id="c"
    )

    assert execu.status == "blocked"
    # pin the SPECIFIC guard so a future payload-validation change can't silently
    # block for the wrong reason and let the cross-workspace guard rot.
    assert execu.reason_code == "agent_not_found"


@pytest.mark.parametrize(
    "payload_override, expected_reason",
    [
        ({"op": "delete_doc"}, "owner_config_op_invalid"),
        ({"section_key": "nonexistent_section"}, "owner_config_section_invalid"),
        ({"body": "   "}, "owner_config_fields_missing"),
    ],
)
async def test_owner_config_executor_blocks_bad_payloads(
    db_session, workspace, agent, payload_override, expected_reason
):
    payload = {
        "op": "edit_doc",
        "agent_id": agent.id,
        "section_key": "role_mission",
        "body": "Yangi matn",
    }
    payload.update(payload_override)
    prop = CommercialActionProposal(
        proposal_id=f"owner_config:bad:{expected_reason}",
        workspace_id=workspace.id,
        conversation_id=0,
        customer_id=0,
        action_type="agent.update_owner_config",
        lifecycle_state="proposed",
        execution_mode="suggest_only",
        risk_level="medium",
        requires_approval=True,
        priority="medium",
        confidence=1.0,
        reason_code="spike",
        source_refs=["owner:test"],
        idempotency_key=f"idem:bad:{expected_reason}",
        payload=payload,
    )
    await CommercialSpineRepository(db_session).persist_action_proposal(prop)
    svc = ActionRuntimeService(CommercialSpineRepository(db_session))
    await svc.process_proposal(workspace_id=workspace.id, proposal_id=prop.proposal_id)
    await svc.approve(
        workspace_id=workspace.id,
        proposal_id=prop.proposal_id,
        actor_ref="owner:test",
        correlation_id="c",
    )
    execu = await svc.execute(
        workspace_id=workspace.id, proposal_id=prop.proposal_id, correlation_id="c"
    )
    assert execu.status == "blocked"
    assert execu.reason_code == expected_reason

    # nothing was written to AGENT.md on any blocked path
    sections = await AgentDocumentService(db_session).list_sections(
        workspace_id=workspace.id,
        document_kind="agent",
        subject_type="agent",
        subject_id=agent.id,
    )
    assert not any(s.section_key == "role_mission" for s in sections)
