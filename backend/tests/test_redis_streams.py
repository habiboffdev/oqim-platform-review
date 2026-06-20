from unittest.mock import AsyncMock

import pytest

from app.core.redis_streams import reclaim_stale_pending_entries


pytestmark = pytest.mark.asyncio


async def test_reclaim_stale_pending_entries_returns_claimed_batch():
    redis = AsyncMock()
    redis.xautoclaim = AsyncMock(
        return_value=[
            "0-0",
            [("1-0", {"foo": "bar"}), ("2-0", {"baz": "qux"})],
            [],
        ]
    )

    claimed = await reclaim_stale_pending_entries(
        redis,
        stream_key="oqim:test",
        group_name="oqim-test-group",
        consumer_name="worker-a",
        count=10,
    )

    assert claimed == [("1-0", {"foo": "bar"}), ("2-0", {"baz": "qux"})]
    redis.xautoclaim.assert_awaited_once()


async def test_reclaim_stale_pending_entries_continues_scanning_until_cursor_resets():
    redis = AsyncMock()
    redis.xautoclaim = AsyncMock(
        side_effect=[
            ["177-0", [], []],
            ["0-0", [("5-0", {"payload": "ok"})], []],
        ]
    )

    claimed = await reclaim_stale_pending_entries(
        redis,
        stream_key="oqim:test",
        group_name="oqim-test-group",
        consumer_name="worker-a",
        count=10,
    )

    assert claimed == [("5-0", {"payload": "ok"})]
    assert redis.xautoclaim.await_count == 2
    first_call = redis.xautoclaim.await_args_list[0]
    second_call = redis.xautoclaim.await_args_list[1]
    assert first_call.args[4] == "0-0"
    assert second_call.args[4] == "177-0"


async def test_reclaim_stale_pending_entries_respects_requested_limit():
    redis = AsyncMock()
    redis.xautoclaim = AsyncMock(
        return_value=[
            "0-0",
            [("1-0", {"a": "1"}), ("2-0", {"a": "2"}), ("3-0", {"a": "3"})],
            [],
        ]
    )

    claimed = await reclaim_stale_pending_entries(
        redis,
        stream_key="oqim:test",
        group_name="oqim-test-group",
        consumer_name="worker-a",
        count=2,
    )

    assert claimed == [("1-0", {"a": "1"}), ("2-0", {"a": "2"})]


async def test_reclaim_stale_pending_entries_creates_missing_group_and_returns_empty():
    redis = AsyncMock()
    redis.xautoclaim = AsyncMock(side_effect=RuntimeError("NOGROUP missing stream/group"))
    redis.xgroup_create = AsyncMock()

    claimed = await reclaim_stale_pending_entries(
        redis,
        stream_key="oqim:events:8",
        group_name="persist",
        consumer_name="worker-a",
        count=10,
    )

    assert claimed == []
    redis.xgroup_create.assert_awaited_once_with(
        "oqim:events:8",
        "persist",
        id="0",
        mkstream=True,
    )
