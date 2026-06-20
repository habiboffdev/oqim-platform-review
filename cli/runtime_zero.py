"""Runtime-zero harness for local readiness checks."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from cli.config import PORTS, PROJECT_ROOT


DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5434/oqim_business"
DEFAULT_REDIS_URL = "redis://localhost:6381/0"
DEFAULT_BACKEND_URL = f"http://localhost:{PORTS['backend']}"
DEFAULT_SIDECAR_URL = f"http://localhost:{PORTS['gramjs']}"

IGNORED_TABLES = {"alembic_version"}

REQUIRED_RUNTIME_TABLES = {
    "action_runtime",
    "agent_conversation_state_snapshots",
    "agent_session_events",
    "agent_sessions",
    "commercial_action_executions",
    "commercial_action_proposals",
    "delivery_runtime",
    "hermes_run_events",
    "hermes_runs",
    "media_runtime",
    "telegram_sessions",
    "workspaces",
    "conversations",
    "messages",
}
EPHEMERAL_REDIS_PREFIXES = (
    "oqim:worker_lease:",
)
EVENT_STREAM_PREFIX = "oqim:events:"
EMPTY_WORKER_STREAM_KEYS = {"oqim:draft_jobs"}


@dataclass
class RuntimeZeroCheck:
    name: str
    passed: bool
    detail: str
    data: dict[str, Any] | None = None


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Read simple KEY=VALUE lines from .env without importing app settings."""
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def env_value(name: str, default: str, dotenv: dict[str, str]) -> str:
    return os.getenv(name) or dotenv.get(name) or default


def normalize_postgres_url(url: str) -> str:
    """Convert SQLAlchemy async URLs into asyncpg-compatible DSNs."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def missing_required_runtime_tables(table_names: list[str] | set[str]) -> list[str]:
    return sorted(REQUIRED_RUNTIME_TABLES.difference(set(table_names)))


async def fetch_database_state(database_url: str) -> tuple[list[int], dict[str, int], list[str]]:
    import asyncpg

    conn = await asyncpg.connect(normalize_postgres_url(database_url))
    try:
        rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        table_names = [row["table_name"] for row in rows if row["table_name"] not in IGNORED_TABLES]

        counts: dict[str, int] = {}
        for table_name in table_names:
            count = await conn.fetchval(f'SELECT COUNT(*)::int FROM "{table_name}"')
            counts[table_name] = int(count or 0)

        workspace_ids: list[int] = []
        if "workspaces" in table_names:
            workspace_rows = await conn.fetch('SELECT id FROM "workspaces" ORDER BY id')
            workspace_ids = [int(row["id"]) for row in workspace_rows]
        return workspace_ids, counts, table_names
    finally:
        await conn.close()


async def reset_database(database_url: str) -> list[str]:
    import asyncpg

    conn = await asyncpg.connect(normalize_postgres_url(database_url))
    try:
        rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        table_names = [row["table_name"] for row in rows if row["table_name"] not in IGNORED_TABLES]
        if table_names:
            quoted = ", ".join(f'"{table_name}"' for table_name in table_names)
            await conn.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")
        return table_names
    finally:
        await conn.close()


async def reset_redis(redis_url: str) -> None:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await client.flushdb()
    finally:
        await client.aclose()


async def fetch_redis_keys(redis_url: str) -> list[str]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await client.ping()
        return sorted([key async for key in client.scan_iter("*")])
    finally:
        await client.aclose()


async def fetch_empty_runtime_stream_keys(redis_url: str, keys: list[str]) -> set[str]:
    import redis.asyncio as aioredis

    stream_keys = [
        key
        for key in keys
        if key.startswith(EVENT_STREAM_PREFIX) or key in EMPTY_WORKER_STREAM_KEYS
    ]
    if not stream_keys:
        return set()

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        empty: set[str] = set()
        for key in stream_keys:
            try:
                if await client.xlen(key) == 0:
                    empty.add(key)
            except Exception:
                continue
        return empty
    finally:
        await client.aclose()


def unexpected_redis_keys(
    keys: list[str],
    *,
    empty_runtime_stream_keys: set[str] | None = None,
) -> list[str]:
    empty_runtime_stream_keys = empty_runtime_stream_keys or set()
    return [
        key
        for key in keys
        if not any(key.startswith(prefix) for prefix in EPHEMERAL_REDIS_PREFIXES)
        and key not in empty_runtime_stream_keys
    ]


async def cleanup_sidecar_stale_workspaces(
    sidecar_url: str,
    sidecar_key: str,
    active_workspace_ids: list[int],
) -> dict[str, Any]:
    headers = {"x-sidecar-key": sidecar_key} if sidecar_key else {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(
            f"{sidecar_url.rstrip('/')}/runtime/cleanup-stale-workspaces",
            json={"activeWorkspaceIds": active_workspace_ids},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


async def fetch_sidecar_sessions(sidecar_url: str, sidecar_key: str) -> list[dict[str, Any]]:
    headers = {"x-sidecar-key": sidecar_key} if sidecar_key else {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{sidecar_url.rstrip('/')}/sessions", headers=headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError("sidecar /sessions returned a non-list payload")
        return [item for item in data if isinstance(item, dict)]


async def fetch_backend_health(backend_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{backend_url.rstrip('/')}/health/detailed")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("backend /health/detailed returned a non-object payload")
        return data


def stale_sidecar_sessions(
    sessions: list[dict[str, Any]],
    active_workspace_ids: list[int],
) -> list[dict[str, Any]]:
    active = set(active_workspace_ids)
    stale: list[dict[str, Any]] = []
    for session in sessions:
        workspace_id = session.get("workspaceId")
        if isinstance(workspace_id, int) and workspace_id not in active:
            stale.append(session)
    return stale


async def run_runtime_zero(
    *,
    reset: bool = False,
    cleanup_sidecar: bool = False,
    database_url: str | None = None,
    redis_url: str | None = None,
    backend_url: str | None = None,
    sidecar_url: str | None = None,
    sidecar_key: str | None = None,
) -> dict[str, Any]:
    dotenv = load_dotenv()
    db_url = database_url or env_value("DATABASE_URL", DEFAULT_DATABASE_URL, dotenv)
    redis = redis_url or env_value("REDIS_URL", DEFAULT_REDIS_URL, dotenv)
    backend = backend_url or env_value("BACKEND_URL", DEFAULT_BACKEND_URL, dotenv)
    sidecar = sidecar_url or env_value("SIDECAR_URL", DEFAULT_SIDECAR_URL, dotenv)
    key = sidecar_key if sidecar_key is not None else env_value("SIDECAR_API_KEY", "dev-sidecar-key", dotenv)

    reset_summary: dict[str, Any] = {}
    checks: list[RuntimeZeroCheck] = []
    if reset:
        try:
            reset_summary["database_tables_truncated"] = await reset_database(db_url)
            await reset_redis(redis)
            reset_summary["redis_flushed"] = True
        except Exception as exc:
            checks.append(RuntimeZeroCheck("reset", False, str(exc)))

    try:
        workspace_ids, counts, table_names = await fetch_database_state(db_url)
        missing_required = missing_required_runtime_tables(table_names)
        checks.append(
            RuntimeZeroCheck(
                name="database_schema",
                passed=not missing_required,
                detail="required runtime tables exist" if not missing_required else "required runtime tables are missing",
                data={"missing_tables": missing_required},
            )
        )
        nonzero = {table: count for table, count in counts.items() if count > 0}
        checks.append(
            RuntimeZeroCheck(
                name="database_zero",
                passed=not nonzero,
                detail="critical app tables are empty" if not nonzero else "critical app tables contain rows",
                data={"workspace_ids": workspace_ids, "nonzero_tables": nonzero},
            )
        )
    except Exception as exc:
        workspace_ids = []
        checks.append(RuntimeZeroCheck("database_zero", False, str(exc)))

    try:
        redis_keys = await fetch_redis_keys(redis)
        empty_runtime_stream_keys = await fetch_empty_runtime_stream_keys(redis, redis_keys)
        unexpected_keys = unexpected_redis_keys(
            redis_keys,
            empty_runtime_stream_keys=empty_runtime_stream_keys,
        )
        checks.append(
            RuntimeZeroCheck(
                name="redis_zero",
                passed=not unexpected_keys,
                detail=(
                    "Redis has no durable app keys"
                    if not unexpected_keys
                    else "Redis contains durable app keys"
                ),
                data={
                    "keys": unexpected_keys[:50],
                    "key_count": len(unexpected_keys),
                    "ignored_ephemeral_key_count": len(redis_keys) - len(unexpected_keys),
                    "ignored_empty_runtime_streams": sorted(empty_runtime_stream_keys),
                },
            )
        )
    except Exception as exc:
        checks.append(RuntimeZeroCheck("redis_zero", False, str(exc)))

    try:
        health = await fetch_backend_health(backend)
        backend_passed = (
            health.get("status") == "ok"
            and health.get("database") == "connected"
            and health.get("redis") == "connected"
        )
        checks.append(
            RuntimeZeroCheck(
                name="backend_health",
                passed=backend_passed,
                detail="backend detailed health is ok" if backend_passed else "backend detailed health is degraded",
                data=health,
            )
        )
    except Exception as exc:
        checks.append(RuntimeZeroCheck("backend_health", False, str(exc)))

    try:
        if cleanup_sidecar:
            reset_summary["sidecar_cleanup"] = await cleanup_sidecar_stale_workspaces(
                sidecar,
                key,
                workspace_ids,
            )
        sessions = await fetch_sidecar_sessions(sidecar, key)
        stale = stale_sidecar_sessions(sessions, workspace_ids)
        checks.append(
            RuntimeZeroCheck(
                name="sidecar_stale_workspaces",
                passed=not stale,
                detail="sidecar has no stale workspace runtime" if not stale else "sidecar has stale workspace runtime",
                data={"sessions": sessions, "stale": stale},
            )
        )
    except Exception as exc:
        checks.append(RuntimeZeroCheck("sidecar_stale_workspaces", False, str(exc)))

    result = {
        "passed": all(check.passed for check in checks),
        "reset": reset_summary,
        "checks": [asdict(check) for check in checks],
    }
    result["summary"] = {
        "passed": sum(1 for check in checks if check.passed),
        "total": len(checks),
    }
    return result


def run_runtime_zero_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_runtime_zero(**kwargs))


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True)
