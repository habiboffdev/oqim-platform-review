"""Operator-facing runtime health signals for the current OQIM backend.

This module is intentionally a reader. It does not decide reply behavior or
drive workers; it only summarizes EventSpine, queues, token usage, and runtime
degradation so local/dev tooling and admin surfaces can tell the truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


EVENT_SPINE_DIVERGENCE_KINDS = (
    "event_no_db",
    "db_no_event",
    "text_mismatch",
    "dedup_raced",
    "send_no_confirm",
    "confirm_no_send",
)


@dataclass(slots=True)
class EventSpineSignals:
    status: str = "ok"
    error: str | None = None
    publish_failures: int = 0
    global_divergences: dict[str, int] = field(default_factory=dict)
    workspace_divergences: dict[str, int] = field(default_factory=dict)
    persist_shadow: dict[str, int] = field(default_factory=dict)
    persist_shadow_ready: bool = True
    persist_shadow_blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RuntimeSignals:
    workspace_id: int
    period_days: int
    event_spine: EventSpineSignals
    seller_agent_reply_freshness: dict[str, Any]
    media: dict[str, Any]
    delivery: dict[str, Any]
    conversation_hydration: dict[str, Any]
    seller_agent_queue: dict[str, Any]
    autopilot: dict[str, Any]
    action_runtime: dict[str, Any]
    quotas: dict[str, Any]
    usage_accounting: dict[str, Any]
    slo: dict[str, Any]
    dependencies: dict[str, Any]
    worker_lifecycle: dict[str, Any]
    repair: dict[str, Any]
    operator_report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = "runtime_signals.v1"
        return payload


async def load_event_spine_signals(
    redis: Any,
    *,
    workspace_id: int | None = None,
) -> EventSpineSignals:
    try:
        publish_failures = await _redis_int(redis, "oqim:event_spine:publish_failures")
        global_divs = {
            kind: await _redis_int(redis, f"oqim:event_spine:div:{kind}")
            for kind in EVENT_SPINE_DIVERGENCE_KINDS
        }
        workspace_divs: dict[str, int] = {}
        if workspace_id is not None:
            workspace_divs = {
                kind: await _redis_int(redis, f"oqim:event_spine:div:{kind}:{workspace_id}")
                for kind in EVENT_SPINE_DIVERGENCE_KINDS
            }
        persist_shadow = await _redis_prefix_counts(redis, "oqim:event_spine:persist:shadow:")
        blockers = [
            key
            for key, value in {**global_divs, **workspace_divs}.items()
            if value > 0 and key in {"event_no_db", "db_no_event", "send_no_confirm"}
        ]
        status = "degraded" if publish_failures > 0 or blockers else "ok"
        return EventSpineSignals(
            status=status,
            publish_failures=publish_failures,
            global_divergences=global_divs,
            workspace_divergences=workspace_divs,
            persist_shadow=persist_shadow,
            persist_shadow_ready=not blockers,
            persist_shadow_blockers=blockers,
        )
    except Exception as exc:
        return EventSpineSignals(status="unreachable", error=str(exc))


async def load_runtime_signals(
    db: AsyncSession,
    redis: Any,
    *,
    workspace_id: int,
    period_days: int = 7,
) -> RuntimeSignals:
    workspace_id = int(workspace_id)
    period_days = max(1, int(period_days))
    event_spine = await load_event_spine_signals(redis, workspace_id=workspace_id)
    seller_freshness = await _seller_agent_reply_freshness(db, workspace_id, period_days)
    media = await _media_signals(db, workspace_id)
    delivery = await _delivery_signals(db, workspace_id)
    hydration = await _conversation_hydration_signals(db, workspace_id)
    queue = await _seller_agent_queue_signals(db, workspace_id)
    autopilot = await _autopilot_signals(db, workspace_id, period_days)
    action_runtime = await _action_runtime_signals(db, workspace_id)
    usage = await _usage_accounting(redis, workspace_id, period_days)
    slo = await _slo_signals(db, redis, workspace_id, period_days)
    dependencies = await _dependency_signals(db, redis)
    worker_lifecycle = await _worker_lifecycle_signals(redis)
    repair = _repair_signals(
        event_spine=event_spine,
        media=media,
        delivery=delivery,
        hydration=hydration,
        action_runtime=action_runtime,
    )
    operator_report = _operator_report(
        workspace_id=workspace_id,
        event_spine=event_spine,
        repair=repair,
        slo=slo,
        dependencies=dependencies,
    )
    return RuntimeSignals(
        workspace_id=workspace_id,
        period_days=period_days,
        event_spine=event_spine,
        seller_agent_reply_freshness=seller_freshness,
        media=media,
        delivery=delivery,
        conversation_hydration=hydration,
        seller_agent_queue=queue,
        autopilot=autopilot,
        action_runtime=action_runtime,
        quotas=await _quota_signals(redis, workspace_id),
        usage_accounting=usage,
        slo=slo,
        dependencies=dependencies,
        worker_lifecycle=worker_lifecycle,
        repair=repair,
        operator_report=operator_report,
    )


async def _redis_int(redis: Any, key: str) -> int:
    raw = await redis.get(key)
    if raw is None:
        return 0
    try:
        return int(raw.decode() if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError):
        return 0


async def _redis_hash_ints(redis: Any, key: str) -> dict[str, int]:
    raw = await redis.hgetall(key)
    if not raw:
        return {}
    result: dict[str, int] = {}
    for key_raw, value_raw in raw.items():
        name = key_raw.decode() if isinstance(key_raw, bytes) else str(key_raw)
        value = value_raw.decode() if isinstance(value_raw, bytes) else value_raw
        try:
            result[name] = int(value)
        except (TypeError, ValueError):
            result[name] = 0
    return result


async def _redis_prefix_counts(redis: Any, prefix: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    async for key_raw in redis.scan_iter(f"{prefix}*"):
        key = key_raw.decode() if isinstance(key_raw, bytes) else str(key_raw)
        counts[key.removeprefix(prefix)] = await _redis_int(redis, key)
    return counts


async def _scalar(db: AsyncSession, sql: str, params: dict[str, Any]) -> Any:
    try:
        return (await db.execute(text(sql), params)).scalar()
    except Exception:
        await db.rollback()
        return None


async def _mapping(db: AsyncSession, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        row = (await db.execute(text(sql), params)).mappings().first()
        return dict(row or {})
    except Exception:
        await db.rollback()
        return {}


async def _group_counts(
    db: AsyncSession,
    sql: str,
    params: dict[str, Any],
) -> dict[str, int]:
    try:
        rows = (await db.execute(text(sql), params)).all()
        return {str(key): int(value or 0) for key, value in rows}
    except Exception:
        await db.rollback()
        return {}


async def _seller_agent_reply_freshness(
    db: AsyncSession,
    workspace_id: int,
    period_days: int,
) -> dict[str, Any]:
    rows = await _mapping(
        db,
        """
        select
          count(*)::int as replies_total,
          count(*) filter (where state = 'expired')::int as expired_count,
          count(*) filter (where state = 'suppressed')::int as suppressed_count,
          count(*) filter (where stale_reason is not null)::int as freshness_loss_count
        from conversation_turn_sessions
        where workspace_id = :workspace_id
          and created_at >= now() - (:period_days * interval '1 day')
        """,
        {"workspace_id": workspace_id, "period_days": period_days},
    )
    total = int(rows.get("replies_total") or 0)
    freshness_loss = int(rows.get("freshness_loss_count") or 0)
    suppressed_reasons = await _group_counts(
        db,
        """
        select coalesce(stale_reason, state, 'unknown') as reason, count(*)::int
        from conversation_turn_sessions
        where workspace_id = :workspace_id
          and created_at >= now() - (:period_days * interval '1 day')
          and (stale_reason is not null or state = 'suppressed')
        group by 1
        """,
        {"workspace_id": workspace_id, "period_days": period_days},
    )
    return {
        "replies_total": total,
        "expired_count": int(rows.get("expired_count") or 0),
        "suppressed_count": int(rows.get("suppressed_count") or 0),
        "freshness_loss_count": freshness_loss,
        "freshness_loss_rate": round(freshness_loss / total, 4) if total else 0,
        "suppressed_reasons": suppressed_reasons,
    }


async def _media_signals(db: AsyncSession, workspace_id: int) -> dict[str, Any]:
    rows = await _mapping(
        db,
        """
        select
          count(*) filter (where ai_relevant is true)::int as ai_relevant_media_total,
          count(*) filter (where hydration_status = 'hydrated')::int as hydrated_count,
          count(*) filter (where action_state = 'pending')::int as pending_count,
          count(*) filter (where action_state = 'deferred')::int as deferred_count,
          count(*) filter (where hydration_status = 'unavailable')::int as unavailable_count,
          count(*) filter (where next_attempt_at is not null and next_attempt_at <= now())::int as due_count,
          count(*) filter (where leased_until is not null and leased_until > now())::int as leased_count,
          count(*) filter (where leased_until is not null and leased_until <= now())::int as stale_lease_count,
          count(*) filter (where action_state in ('failed', 'degraded'))::int as stuck_count
        from media_runtime
        where workspace_id = :workspace_id
        """,
        {"workspace_id": workspace_id},
    )
    return _defaults(
        rows,
        "ai_relevant_media_total",
        "hydrated_count",
        "pending_count",
        "deferred_count",
        "unavailable_count",
        "due_count",
        "leased_count",
        "stale_lease_count",
        "stuck_count",
    )


async def _delivery_signals(db: AsyncSession, workspace_id: int) -> dict[str, Any]:
    rows = await _mapping(
        db,
        """
        select
          count(*) filter (where state in ('requested', 'sending'))::int as active_count,
          count(*) filter (where state = 'unknown')::int as unknown_count,
          count(*) filter (where state = 'failed')::int as failed_count,
          count(*) filter (where state in ('unknown', 'failed') and attempt_count < 3)::int as retryable_count,
          count(*) filter (where state = 'unknown' and coalesce(unknown_at, updated_at) < now() - interval '5 minutes')::int as stale_unknown_count
        from delivery_runtime
        where workspace_id = :workspace_id
        """,
        {"workspace_id": workspace_id},
    )
    return _defaults(
        rows,
        "active_count",
        "unknown_count",
        "failed_count",
        "retryable_count",
        "stale_unknown_count",
    )


async def _conversation_hydration_signals(db: AsyncSession, workspace_id: int) -> dict[str, Any]:
    rows = await _mapping(
        db,
        """
        select
          count(*) filter (where state in ('queued', 'running', 'deferred'))::int as active_count,
          count(*) filter (where state = 'queued')::int as queued_count,
          count(*) filter (where state = 'running')::int as running_count,
          count(*) filter (where state = 'deferred')::int as deferred_count,
          count(*) filter (where state = 'failed')::int as failed_count,
          count(*) filter (where leased_until is not null and leased_until <= now())::int as stale_lease_count,
          count(*) filter (where state = 'failed' and attempt_count < max_attempts)::int as retryable_count
        from conversation_hydration_runtime
        where workspace_id = :workspace_id
        """,
        {"workspace_id": workspace_id},
    )
    return _defaults(
        rows,
        "active_count",
        "queued_count",
        "running_count",
        "deferred_count",
        "failed_count",
        "stale_lease_count",
        "retryable_count",
    )


async def _seller_agent_queue_signals(db: AsyncSession, workspace_id: int) -> dict[str, Any]:
    rows = await _mapping(
        db,
        """
        select
          count(*) filter (where state in ('open', 'starting', 'running', 'finalizing'))::int as active_candidates,
          count(*) filter (where state = 'open')::int as open_candidates,
          count(*) filter (where state = 'finalizing')::int as ready_candidates,
          count(*) filter (where state = 'starting')::int as leased_candidates,
          count(*) filter (where state = 'running')::int as generating_candidates,
          count(*) filter (where state = 'failed')::int as failed_candidates,
          count(*) filter (where state = 'suppressed')::int as suppressed_candidates,
          count(*) filter (where state = 'superseded')::int as superseded_candidates
        from conversation_turn_sessions
        where workspace_id = :workspace_id
        """,
        {"workspace_id": workspace_id},
    )
    return _defaults(
        rows,
        "active_candidates",
        "open_candidates",
        "ready_candidates",
        "leased_candidates",
        "generating_candidates",
        "failed_candidates",
        "suppressed_candidates",
        "superseded_candidates",
    )


async def _autopilot_signals(db: AsyncSession, workspace_id: int, period_days: int) -> dict[str, Any]:
    rows = await _mapping(
        db,
        """
        select
          count(*)::int as decisions_total,
          count(*) filter (where state in ('approved', 'sent', 'succeeded'))::int as allowed_count,
          count(*) filter (where state in ('blocked', 'rejected'))::int as blocked_count,
          count(*) filter (where state = 'scheduled')::int as scheduled_count,
          count(*) filter (where state in ('sent', 'succeeded'))::int as sent_count,
          count(*) filter (where state = 'delivery_failed')::int as delivery_failed_count,
          count(*) filter (where state = 'delivery_unknown')::int as delivery_unknown_count
        from action_runtime
        where workspace_id = :workspace_id
          and created_at >= now() - (:period_days * interval '1 day')
        """,
        {"workspace_id": workspace_id, "period_days": period_days},
    )
    blocked_reasons = await _group_counts(
        db,
        """
        select coalesce(last_error, 'blocked') as reason, count(*)::int
        from action_runtime
        where workspace_id = :workspace_id
          and state in ('blocked', 'rejected')
          and created_at >= now() - (:period_days * interval '1 day')
        group by 1
        """,
        {"workspace_id": workspace_id, "period_days": period_days},
    )
    return {
        **_defaults(
            rows,
            "decisions_total",
            "allowed_count",
            "blocked_count",
            "scheduled_count",
            "sent_count",
            "delivery_failed_count",
            "delivery_unknown_count",
        ),
        "blocked_reasons": blocked_reasons,
    }


async def _action_runtime_signals(db: AsyncSession, workspace_id: int) -> dict[str, Any]:
    degraded_by_action = await _group_counts(
        db,
        """
        select action, count(*)::int
        from action_runtime
        where workspace_id = :workspace_id
          and state = 'degraded'
        group by action
        """,
        {"workspace_id": workspace_id},
    )
    return {
        "degraded_total": sum(degraded_by_action.values()),
        "degraded_by_action": degraded_by_action,
    }


async def _usage_accounting(redis: Any, workspace_id: int, period_days: int) -> dict[str, Any]:
    today = date.today()
    by_operation: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    by_operation_cost: dict[str, int] = {}
    by_provider_cost: dict[str, int] = {}
    daily_history: list[dict[str, Any]] = []
    daily_input = daily_output = daily_ops = 0
    for offset in range(period_days):
        day = today - timedelta(days=offset)
        usage = await _redis_hash_ints(redis, f"tokens:{workspace_id}:{day.isoformat()}")
        ops = await _redis_hash_ints(redis, f"ops:{workspace_id}:{day.isoformat()}")
        input_tokens = 0
        output_tokens = 0
        for key, value in usage.items():
            parts = key.split(":")
            if len(parts) < 3:
                continue
            operation, provider, direction = parts[0], parts[1], parts[2]
            if direction == "input":
                input_tokens += value
                by_operation[operation] = by_operation.get(operation, 0) + value
                by_provider[provider] = by_provider.get(provider, 0) + value
            elif direction == "output":
                output_tokens += value
                by_operation[operation] = by_operation.get(operation, 0) + value
                by_provider[provider] = by_provider.get(provider, 0) + value
        operation_count = sum(ops.values())
        estimated_cost = _estimated_cost_micros(input_tokens, output_tokens)
        if offset == 0:
            daily_input = input_tokens
            daily_output = output_tokens
            daily_ops = operation_count
        daily_history.append(
            {
                "date": day.isoformat(),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "operation_count": operation_count,
                "estimated_cost_micros": estimated_cost,
            }
        )
    total_cost = _estimated_cost_micros(daily_input, daily_output)
    for operation, tokens in by_operation.items():
        by_operation_cost[operation] = _estimated_cost_micros(tokens, 0)
    for provider, tokens in by_provider.items():
        by_provider_cost[provider] = _estimated_cost_micros(tokens, 0)
    return {
        "daily_input_tokens": daily_input,
        "daily_output_tokens": daily_output,
        "daily_total_tokens": daily_input + daily_output,
        "daily_operation_count": daily_ops,
        "daily_estimated_cost_micros": total_cost,
        "by_operation": by_operation,
        "by_provider": by_provider,
        "by_operation_estimated_cost_micros": by_operation_cost,
        "by_provider_estimated_cost_micros": by_provider_cost,
        "cost_policy": {
            "default": {
                "input_micros_per_1k_tokens": 1,
                "output_micros_per_1k_tokens": 3,
            }
        },
        "daily_history": list(reversed(daily_history)),
    }


async def _slo_signals(db: AsyncSession, redis: Any, workspace_id: int, period_days: int) -> dict[str, Any]:
    row = await _mapping(
        db,
        """
        select
          percentile_cont(0.95) within group (order by total_latency_ms)::float as p95_ms,
          count(*)::int as sample_count,
          min(extract(epoch from (now() - created_at))) filter (
            where state in ('open', 'starting', 'running', 'finalizing')
          )::float as oldest_wait_seconds
        from hermes_runs
        where workspace_id = :workspace_id
          and trigger_type = 'telegram_message'
          and created_at >= now() - (:period_days * interval '1 day')
        """,
        {"workspace_id": workspace_id, "period_days": period_days},
    )
    p95 = row.get("p95_ms")
    sample_count = int(row.get("sample_count") or 0)
    replay_drift = sum((await load_event_spine_signals(redis, workspace_id=workspace_id)).workspace_divergences.values())
    return {
        "status": "ok" if replay_drift == 0 else "degraded",
        "message_visible_under_1s_status": "unknown",
        "message_visible_p95_ms": None,
        "message_visible_sample_count": 0,
        "seller_agent_or_degraded_under_20s_status": (
            "pass" if p95 is not None and float(p95) <= 20_000 else "unknown"
        ),
        "oldest_seller_agent_wait_seconds": row.get("oldest_wait_seconds"),
        "media_hydration_lag_seconds": None,
        "workspace_deadletter_length": await _redis_int(redis, f"oqim:deadletter:{workspace_id}:len"),
        "replay_drift_status": "ok" if replay_drift == 0 else "degraded",
        "replay_drift_count": replay_drift,
        "telegram_trigger_start_p50_ms": None,
        "telegram_trigger_start_sample_count": sample_count,
    }


async def _dependency_signals(db: AsyncSession, redis: Any) -> dict[str, Any]:
    errors: dict[str, str] = {}
    try:
        await db.execute(text("select 1"))
        database = "ok"
    except Exception as exc:
        await db.rollback()
        database = "down"
        errors["database"] = str(exc)
    try:
        await redis.ping()
        redis_status = "ok"
    except Exception as exc:
        redis_status = "down"
        errors["redis"] = str(exc)
    return {
        "status": "ok" if not errors else "degraded",
        "database": database,
        "redis": redis_status,
        "errors": errors,
    }


async def _worker_lifecycle_signals(redis: Any) -> dict[str, Any]:
    roles: dict[str, Any] = {}
    async for key_raw in redis.scan_iter("oqim:worker:*:heartbeat"):
        key = key_raw.decode() if isinstance(key_raw, bytes) else str(key_raw)
        role = key.removeprefix("oqim:worker:").removesuffix(":heartbeat")
        ttl = await redis.ttl(key)
        roles[role] = {
            "role": role,
            "lifecycle_model": "redis_heartbeat",
            "proof_status": "observed",
            "active": ttl is not None and int(ttl) > 0,
            "owner": None,
            "ttl_seconds": int(ttl) if ttl is not None and int(ttl) >= 0 else None,
            "contended_count": 0,
            "lost_count": 0,
            "supervisor_status": None,
            "heartbeat_stale": ttl is None or int(ttl) <= 0,
            "restart_count": None,
            "last_error": None,
        }
    return {
        "status": "ok",
        "error": None,
        "roles": roles,
    }


async def _quota_signals(redis: Any, workspace_id: int) -> dict[str, Any]:
    universal_count = await _redis_int(
        redis,
        f"ops:{workspace_id}:{date.today().isoformat()}:universal_extraction",
    )
    daily_cap = 1000
    return {
        "seller_agent_max_inflight": 1,
        "seller_agent_max_ready_claims_per_tick": 4,
        "media_max_claims_per_workspace": 5,
        "scheduled_send_max_claims_per_workspace": 10,
        "universal_extraction_daily_count": universal_count,
        "universal_extraction_daily_cap": daily_cap,
        "universal_extraction_exceeded": universal_count > daily_cap,
    }


def _repair_signals(
    *,
    event_spine: EventSpineSignals,
    media: dict[str, Any],
    delivery: dict[str, Any],
    hydration: dict[str, Any],
    action_runtime: dict[str, Any],
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    if event_spine.publish_failures or event_spine.persist_shadow_blockers:
        actions.append(
            {
                "key": "event_spine_replay",
                "severity": "critical",
                "scope": "event_spine",
                "reason": "EventSpine has publish failures or replay divergence.",
                "replay_entrypoint": "oqim test replay",
                "repair_entrypoint": None,
            }
        )
    for key, scope, count in (
        ("delivery_reconcile", "delivery", delivery.get("stale_unknown_count", 0)),
        ("media_hydration_retry", "media", media.get("stuck_count", 0)),
        ("conversation_hydration_retry", "conversation_hydration", hydration.get("retryable_count", 0)),
        ("action_runtime_retry", "action_runtime", action_runtime.get("degraded_total", 0)),
    ):
        if int(count or 0) > 0:
            actions.append(
                {
                    "key": key,
                    "severity": "warning",
                    "scope": scope,
                    "reason": f"{count} item(s) need operator review.",
                    "replay_entrypoint": None,
                    "repair_entrypoint": None,
                }
            )
    return {
        "status": "ok" if not actions else "degraded",
        "degraded_reasons": [item["key"] for item in actions],
        "actions": actions,
    }


def _operator_report(
    *,
    workspace_id: int,
    event_spine: EventSpineSignals,
    repair: dict[str, Any],
    slo: dict[str, Any],
    dependencies: dict[str, Any],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for action in repair.get("actions", []):
        findings.append(
            {
                "key": action["key"],
                "severity": action["severity"],
                "owner": "runtime",
                "state": "open",
                "scope": action["scope"],
                "reason": action["reason"],
                "safe_action": "inspect",
                "replay_entrypoint": action.get("replay_entrypoint"),
                "repair_entrypoint": action.get("repair_entrypoint"),
            }
        )
    if dependencies.get("status") != "ok":
        findings.append(
            {
                "key": "dependency_degraded",
                "severity": "critical",
                "owner": "runtime",
                "state": "open",
                "scope": "dependencies",
                "reason": str(dependencies.get("errors") or {}),
                "safe_action": "inspect",
                "replay_entrypoint": None,
                "repair_entrypoint": None,
            }
        )
    if event_spine.status == "unreachable":
        findings.append(
            {
                "key": "event_spine_unreachable",
                "severity": "critical",
                "owner": "runtime",
                "state": "open",
                "scope": "event_spine",
                "reason": event_spine.error or "EventSpine signals cannot be loaded.",
                "safe_action": "inspect",
                "replay_entrypoint": "oqim dev status",
                "repair_entrypoint": None,
            }
        )
    critical = sum(1 for item in findings if item["severity"] == "critical")
    warnings = sum(1 for item in findings if item["severity"] == "warning")
    status = "ok" if not findings and slo.get("status") == "ok" else "degraded"
    return {
        "status": status,
        "workspace_id": workspace_id,
        "summary": "runtime signals healthy" if status == "ok" else "runtime signals degraded",
        "finding_count": len(findings),
        "critical_count": critical,
        "warning_count": warnings,
        "findings": findings,
    }


def _defaults(rows: dict[str, Any], *keys: str) -> dict[str, int]:
    return {key: int(rows.get(key) or 0) for key in keys}


def _estimated_cost_micros(input_tokens: int, output_tokens: int) -> int:
    return int((input_tokens / 1000) * 1 + (output_tokens / 1000) * 3)
