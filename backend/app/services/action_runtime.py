from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_runtime import ActionRuntime

logger = logging.getLogger("oqim_business.action_runtime")

ACTION_RUNNING = "running"
ACTION_SUCCESS = "success"
ACTION_DEGRADED = "degraded"


async def record_action_state(
    session: AsyncSession,
    *,
    workspace_id: int,
    conversation_id: int,
    message_id: int,
    action: str,
    state: str,
    source: str,
    error: str | None = None,
) -> ActionRuntime:
    # Atomic, race-safe upsert. The previous SELECT-then-INSERT had a TOCTOU race:
    # two concurrent projections of the SAME (workspace, conversation, message,
    # action) — e.g. the background persist consumer and the reply bridge both
    # projecting one message — could both miss the SELECT and both INSERT, hitting
    # the uq_action_runtime_message_action unique violation, which poisoned the
    # session (and broke the reply). ON CONFLICT DO UPDATE makes it idempotent
    # without relying on a Redis lock (callers may pass redis=None).
    now = datetime.now(UTC)
    insert_values: dict[str, Any] = {
        "workspace_id": workspace_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "action": action,
        "state": state,
        "source": source,
        "attempt_count": 1 if state == ACTION_RUNNING else 0,
        "last_error": None if state == ACTION_SUCCESS else error,
        "started_at": now if state == ACTION_RUNNING else None,
        "succeeded_at": now if state == ACTION_SUCCESS else None,
        "degraded_at": now if state == ACTION_DEGRADED else None,
        "created_at": now,
        "updated_at": now,
    }
    update_values: dict[str, Any] = {"state": state, "source": source, "updated_at": now}
    if state == ACTION_RUNNING:
        update_values["started_at"] = now
        update_values["attempt_count"] = ActionRuntime.attempt_count + 1
    elif state == ACTION_SUCCESS:
        update_values["succeeded_at"] = now
        update_values["last_error"] = None
    elif state == ACTION_DEGRADED:
        update_values["degraded_at"] = now
    if error is not None and state != ACTION_SUCCESS:
        update_values["last_error"] = error

    stmt = (
        pg_insert(ActionRuntime)
        .values(**insert_values)
        .on_conflict_do_update(
            constraint="uq_action_runtime_message_action",
            set_=update_values,
        )
        .returning(ActionRuntime.id)
    )
    row_id = await session.scalar(stmt)
    return await session.get(ActionRuntime, row_id, populate_existing=True)
