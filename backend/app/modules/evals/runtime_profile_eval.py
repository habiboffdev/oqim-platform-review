from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.modules.agent_runtime_v2.config_loader import AgentConfig
from app.modules.agent_runtime_v2.runtime_profile import (
    ExecutionMode,
    RuntimeProfile,
    RuntimeProfileCompiler,
)
from app.modules.hermes_runtime.contracts import (
    HermesRunInput,
    HermesRunLane,
    HermesRunMode,
    HermesRunPatch,
)
from app.modules.hermes_runtime.service import HermesRunService

_EXECUTION_MODE_RUN_POLICIES: dict[ExecutionMode, tuple[str, HermesRunLane, HermesRunMode]] = {
    "interactive": ("seller_agent", HermesRunLane.FAST_INTERACTIVE, HermesRunMode.REPLY),
    "action": ("seller_agent", HermesRunLane.BACKGROUND, HermesRunMode.LEARNING),
    "setup": ("setup_agent", HermesRunLane.BACKGROUND, HermesRunMode.LEARNING),
}


class RuntimeProfileEvalCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class RuntimeProfileEvalResult(BaseModel):
    case_id: str
    profile_kind: str
    execution_mode: str
    passed: bool
    lane: str
    run_mode: str
    agent_kind: str
    hermes_run_id: str
    runtime_profile_hash: str
    runtime_profile_cache_key: str
    tool_schema_count: int = Field(ge=0)
    talk_tool_count: int = Field(ge=0)
    completed: bool
    deduped_replay: bool
    duration_ms: int = Field(ge=0)
    checks: list[RuntimeProfileEvalCheck] = Field(default_factory=list)


class RuntimeProfileEvalSuiteReport(BaseModel):
    suite: Literal["runtime-profiles"] = "runtime-profiles"
    workspace_id: int = Field(gt=0)
    agent_id: int = Field(gt=0)
    total_runs: int = Field(ge=0)
    passed_runs: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    profile_count: int = Field(ge=0)
    execution_mode_count: int = Field(ge=0)
    completed_run_count: int = Field(ge=0)
    deduped_replay_count: int = Field(ge=0)
    tool_schema_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    p95_case_duration_ms: int = Field(ge=0)
    results: list[RuntimeProfileEvalResult] = Field(default_factory=list)


async def run_runtime_profile_background_eval_suite(
    *,
    session: AsyncSession,
    workspace_id: int,
    agent_id: int,
) -> RuntimeProfileEvalSuiteReport:
    started = time.monotonic()
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace_id:
        raise ValueError(f"agent {agent_id} not found in workspace {workspace_id}")
    config = AgentConfig(
        agent_id=agent.id,
        workspace_id=workspace_id,
        name=agent.name,
        trust_mode=agent.trust_mode,
        auto_send_threshold=float(agent.auto_send_threshold),
        agent_md=_agent_md(agent),
    )
    suite_key = uuid.uuid4().hex[:12]
    compiler = RuntimeProfileCompiler()
    runs = HermesRunService(session)
    results: list[RuntimeProfileEvalResult] = []
    for execution_mode, (agent_kind, lane, run_mode) in _EXECUTION_MODE_RUN_POLICIES.items():
        profile = compiler.compile_agent(
            config=config,
            agent_kind=agent_kind,
            execution_mode=execution_mode,
        )
        results.append(
            await _record_profile_case(
                runs=runs,
                profile=profile,
                lane=lane,
                run_mode=run_mode,
                suite_key=suite_key,
            )
        )

    passed = sum(1 for result in results if result.passed)
    durations = [result.duration_ms for result in results]
    return RuntimeProfileEvalSuiteReport(
        workspace_id=workspace_id,
        agent_id=agent_id,
        total_runs=len(results),
        passed_runs=passed,
        pass_rate=(passed / len(results)) if results else 0.0,
        profile_count=len(results),
        execution_mode_count=len(results),
        completed_run_count=sum(1 for result in results if result.completed),
        deduped_replay_count=sum(1 for result in results if result.deduped_replay),
        tool_schema_count=sum(result.tool_schema_count for result in results),
        duration_ms=int((time.monotonic() - started) * 1000),
        p95_case_duration_ms=_percentile_ms(durations, 0.95),
        results=results,
    )


async def _record_profile_case(
    *,
    runs: HermesRunService,
    profile: RuntimeProfile,
    lane: HermesRunLane,
    run_mode: HermesRunMode,
    suite_key: str,
) -> RuntimeProfileEvalResult:
    started = time.monotonic()
    cache_key = _profile_cache_key(profile)
    profile_ref = _profile_eval_ref(profile)
    trigger_id = f"runtime-profile-eval:{suite_key}:{profile_ref}"
    payload = HermesRunInput(
        workspace_id=profile.workspace_id,
        agent_id=profile.agent_id,
        agent_kind=profile.agent_kind,
        lane=lane,
        run_mode=run_mode,
        trigger_type="runtime_profile_eval",
        trigger_id=trigger_id,
        event_id=trigger_id,
        runtime_profile_snapshot_id=cache_key,
        runtime_profile_cache_key=cache_key,
        source_refs=[f"runtime_profile:{profile_ref}", cache_key],
        input_summary=f"Prove {profile_ref} runtime profile lane and grants.",
        details=_profile_details(profile),
        correlation_id=f"runtime-profile-eval:{suite_key}",
    )
    created = await runs.start_or_dedupe(payload)
    if not created.deduped:
        running = await runs.mark_running(created.run_id, engine_run_id=f"eval:{profile_ref}")
        completed = await runs.complete(
            running.run_id,
            HermesRunPatch(
                output_action="runtime_profile_proof",
                output_ref=f"runtime_profile_eval:{suite_key}:{profile_ref}",
                confidence=1.0,
                details={"eval_completed": True},
            ),
        )
    else:
        completed = created
    replay = await runs.start_or_dedupe(payload)
    talk_tool_count = sum(1 for tool in profile.allowed_tool_names if tool.startswith("talk."))
    completed_state = str(completed.state) == "completed"
    checks = [
        RuntimeProfileEvalCheck(
            name="run_completed",
            passed=completed_state,
            detail=f"state={completed.state}",
        ),
        RuntimeProfileEvalCheck(
            name="profile_hash_recorded",
            passed=completed.runtime_profile_cache_key == cache_key
            and completed.details.get("runtime_profile_hash") == profile.profile_hash,
            detail=f"cache={completed.runtime_profile_cache_key} hash={profile.profile_hash}",
        ),
        RuntimeProfileEvalCheck(
            name="lane_and_mode_recorded",
            passed=str(completed.lane) == str(lane) and str(completed.run_mode) == str(run_mode),
            detail=f"lane={completed.lane} run_mode={completed.run_mode}",
        ),
        RuntimeProfileEvalCheck(
            name="tool_grants_recorded",
            passed=completed.details.get("tool_schema_count") == len(profile.allowed_tool_names),
            detail=f"tools={completed.details.get('allowed_tool_names')}",
        ),
        RuntimeProfileEvalCheck(
            name="replay_dedupes_run",
            passed=replay.deduped is True and replay.run_id == completed.run_id,
            detail=f"replay_run_id={replay.run_id} deduped={replay.deduped}",
        ),
    ]
    return RuntimeProfileEvalResult(
        case_id=f"{profile_ref}_runtime_profile_run",
        profile_kind=profile.profile_kind,
        execution_mode=profile.execution_mode,
        passed=all(check.passed for check in checks),
        lane=str(lane),
        run_mode=str(run_mode),
        agent_kind=profile.agent_kind,
        hermes_run_id=completed.run_id,
        runtime_profile_hash=profile.profile_hash,
        runtime_profile_cache_key=cache_key,
        tool_schema_count=len(profile.allowed_tool_names),
        talk_tool_count=talk_tool_count,
        completed=completed_state,
        deduped_replay=bool(replay.deduped),
        duration_ms=int((time.monotonic() - started) * 1000),
        checks=checks,
    )


def _agent_md(agent: Agent) -> str:
    instructions = str(agent.instructions or "").strip()
    if instructions:
        return instructions
    return f"# {agent.name}\n\nPhase 5 runtime profile eval fixture."


def _profile_cache_key(profile: RuntimeProfile) -> str:
    return (
        f"runtime_profile:{profile.workspace_id}:{profile.agent_id}:"
        f"{profile.profile_kind}:{profile.execution_mode}:{profile.profile_hash}"
    )


def _profile_eval_ref(profile: RuntimeProfile) -> str:
    return f"{profile.profile_kind}:{profile.execution_mode}"


def _profile_details(profile: RuntimeProfile) -> dict:
    return {
        "runtime_profile_kind": profile.profile_kind,
        "runtime_execution_mode": profile.execution_mode,
        "runtime_profile_hash": profile.profile_hash,
        "tool_schema_count": len(profile.allowed_tool_names),
        "allowed_tool_names": list(profile.allowed_tool_names),
        "retrieval_policy": profile.retrieval_policy.model_dump(mode="json"),
        "hermes_settings": profile.hermes_settings.model_dump(mode="json"),
        "action_policy": profile.action_policy.model_dump(mode="json"),
    }


def _percentile_ms(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * percentile)),
    )
    return ordered[index]
