from cli.runtime_zero import (
    REQUIRED_RUNTIME_TABLES,
    missing_required_runtime_tables,
    normalize_postgres_url,
    stale_sidecar_sessions,
    unexpected_redis_keys,
)


def test_normalize_postgres_url_converts_sqlalchemy_async_scheme() -> None:
    assert (
        normalize_postgres_url("postgresql+asyncpg://postgres:postgres@localhost:5434/oqim_business")
        == "postgresql://postgres:postgres@localhost:5434/oqim_business"
    )


def test_stale_sidecar_sessions_reports_workspace_not_in_database() -> None:
    sessions = [
        {"workspaceId": 1, "state": "connected"},
        {"workspaceId": 2, "state": "disconnected"},
        {"workspaceId": 3, "state": "connected"},
    ]

    assert stale_sidecar_sessions(sessions, [1, 3]) == [{"workspaceId": 2, "state": "disconnected"}]


def test_unexpected_redis_keys_allows_live_worker_leases() -> None:
    keys = [
        "oqim:worker_lease:follow_up_scheduler",
        "oqim:worker_lease:scheduled_reply_sender:lost",
        "oqim:events:7",
        "tokens:1:2026-04-28",
    ]

    assert unexpected_redis_keys(keys) == ["oqim:events:7", "tokens:1:2026-04-28"]


def test_unexpected_redis_keys_allows_empty_runtime_streams() -> None:
    keys = [
        "oqim:draft_jobs",
        "oqim:events:7",
        "oqim:events:8",
        "tokens:1:2026-04-28",
    ]

    assert unexpected_redis_keys(
        keys,
        empty_runtime_stream_keys={"oqim:draft_jobs", "oqim:events:7"},
    ) == [
        "oqim:events:8",
        "tokens:1:2026-04-28",
    ]


def test_runtime_zero_requires_agent_session_and_hermes_state_tables() -> None:
    required = {
        "agent_sessions",
        "agent_session_events",
        "agent_conversation_state_snapshots",
        "hermes_runs",
        "hermes_run_events",
        "commercial_action_proposals",
        "commercial_action_executions",
    }

    assert required.issubset(REQUIRED_RUNTIME_TABLES)
    present = sorted(REQUIRED_RUNTIME_TABLES - {"agent_conversation_state_snapshots"})
    assert missing_required_runtime_tables(present) == ["agent_conversation_state_snapshots"]
