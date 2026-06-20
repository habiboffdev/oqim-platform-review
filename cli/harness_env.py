"""Environment helpers for proof harness subprocesses."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

_SAFE_SUFFIX_RE = re.compile(r"[^a-zA-Z0-9_]+")


def sanitize_test_db_suffix(value: str | None) -> str:
    """Return a Postgres-identifier-safe suffix for isolated test databases."""
    sanitized = _SAFE_SUFFIX_RE.sub("_", (value or "").strip()).strip("_").lower()
    if not sanitized:
        return "isolated"
    return sanitized[:48].strip("_") or "isolated"


def make_harness_db_suffix(
    name: str,
    *,
    sequence: int,
    pid: int | None = None,
) -> str:
    """Build a stable, traceable suffix for one harness subprocess."""
    process_id = os.getpid() if pid is None else pid
    return sanitize_test_db_suffix(f"{name}_{process_id}_{sequence}")


def build_backend_pytest_env(
    *,
    db_suffix: str | None = None,
    drop_db_at_end: bool = False,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return env vars for a backend pytest run.

    The backend test conftest owns DATABASE_URL construction. We only pass a
    safe suffix so parallel proof suites cannot drop each other's database.
    """
    env = dict(base_env or os.environ)
    if db_suffix:
        env["OQIM_TEST_DB_SUFFIX"] = sanitize_test_db_suffix(db_suffix)
    if drop_db_at_end:
        env["OQIM_TEST_DB_DROP_AT_END"] = "1"
    return env
