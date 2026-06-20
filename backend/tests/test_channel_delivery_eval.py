from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.workspace import Workspace
from app.modules.evals.channel_delivery_eval import run_channel_delivery_eval_suite


async def test_channel_delivery_eval_proves_partial_delivery_and_replay_safety(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
    conversation: Conversation,
) -> None:
    report = await run_channel_delivery_eval_suite(
        session=db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
        conversation_id=conversation.id,
    )

    assert report.suite == "channel-delivery"
    assert report.pass_rate == 1.0
    assert report.passed_runs == report.total_runs
    assert report.intent_count == 5
    assert report.sent_count == 8
    assert report.unknown_count == 3
    assert report.replayed_count == 2
    assert report.duplicate_delivery_count == 0
    assert report.delivery_call_count == 7
    assert {
        "multi_bubble_partial_delivery",
        "idempotent_replay_after_partial_delivery",
        "restart_replays_unconfirmed_bubbles_without_delivery_call",
        "burst_delivery_preserves_ordered_idempotency",
    } == {result.case_id for result in report.results}
