from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.workspace import Workspace
from app.modules.evals.runtime_profile_eval import (
    run_runtime_profile_background_eval_suite,
)


async def test_runtime_profile_eval_records_generic_agent_mode_runs_and_dedupe(
    db_session: AsyncSession,
    workspace: Workspace,
    agent: Agent,
) -> None:
    report = await run_runtime_profile_background_eval_suite(
        session=db_session,
        workspace_id=workspace.id,
        agent_id=agent.id,
    )

    assert report.suite == "runtime-profiles"
    assert report.pass_rate == 1.0
    assert report.profile_count == 3
    assert report.execution_mode_count == 3
    assert report.completed_run_count == 3
    assert report.deduped_replay_count == 3
    assert report.tool_schema_count > 0
    assert {result.profile_kind for result in report.results} == {"agent"}
    assert {result.execution_mode for result in report.results} == {
        "interactive",
        "action",
        "setup",
    }

    by_mode = {result.execution_mode: result for result in report.results}
    assert by_mode["interactive"].lane == "fast_interactive"
    assert by_mode["interactive"].run_mode == "reply"
    assert by_mode["action"].lane == "background"
    assert by_mode["action"].run_mode == "learning"
    assert by_mode["setup"].lane == "background"
    assert by_mode["setup"].run_mode == "learning"
    assert by_mode["interactive"].talk_tool_count > 0
    assert by_mode["action"].talk_tool_count == 0
    assert by_mode["setup"].talk_tool_count == 0

    for result in report.results:
        assert result.hermes_run_id.startswith("hermes_run:")
        assert result.runtime_profile_hash
        assert result.runtime_profile_cache_key.endswith(result.runtime_profile_hash)
        assert result.completed is True
        assert result.deduped_replay is True
