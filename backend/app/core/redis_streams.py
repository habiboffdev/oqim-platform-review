from __future__ import annotations

from typing import Any


STALE_PEL_MIN_IDLE_MS = 30_000


def is_missing_consumer_group_error(exc: Exception) -> bool:
    return "NOGROUP" in str(exc)


async def ensure_consumer_group(
    redis: Any,
    *,
    stream_key: str,
    group_name: str,
    start_id: str = "0",
) -> None:
    """Create a consumer group and stream if they do not exist."""

    try:
        await redis.xgroup_create(stream_key, group_name, id=start_id, mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def reclaim_stale_pending_entries(
    redis: Any,
    *,
    stream_key: str,
    group_name: str,
    consumer_name: str,
    count: int,
    min_idle_ms: int = STALE_PEL_MIN_IDLE_MS,
) -> list[tuple[str, dict]]:
    """Claim stale pending stream entries from dead consumers.

    Unique consumer names improve isolation, but they also mean a restarted
    worker cannot see another consumer's PEL via XREADGROUP(..., id="0").
    We explicitly reclaim stale entries first so work is not stranded behind
    dead consumer names after reloads or crashes.
    """

    start_id = "0-0"
    reclaimed: list[tuple[str, dict]] = []

    while len(reclaimed) < count:
        remaining = max(1, count - len(reclaimed))
        try:
            next_start_id, entries, _deleted_ids = await redis.xautoclaim(
                stream_key,
                group_name,
                consumer_name,
                min_idle_ms,
                start_id,
                count=remaining,
            )
        except Exception as exc:
            if not is_missing_consumer_group_error(exc):
                raise
            await ensure_consumer_group(
                redis,
                stream_key=stream_key,
                group_name=group_name,
            )
            return reclaimed

        if entries:
            reclaimed.extend(entries[:remaining])

        if next_start_id == "0-0":
            break
        if next_start_id == start_id and not entries:
            break
        start_id = next_start_id

    return reclaimed


async def xadd_event(
    redis: Any,
    stream_key: str,
    fields: dict[str, str],
    *,
    maxlen: int | None = 10_000,
    approximate: bool = True,
) -> str:
    """Append an event to a Redis stream.

    ``maxlen`` keeps operational/work queues bounded. Pass ``None`` for
    canonical truth streams where trimming would destroy replay history.
    """
    if maxlen is None:
        return await redis.xadd(stream_key, fields)
    return await redis.xadd(
        stream_key,
        fields,
        maxlen=maxlen,
        approximate=approximate,
    )
