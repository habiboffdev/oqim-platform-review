"""Tests for redis_streams helpers — xadd_event."""
from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.core.redis_streams import xadd_event


@pytest.fixture
async def fake_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


async def test_xadd_event_returns_stream_id(fake_redis):
    stream_id = await xadd_event(
        fake_redis,
        "oqim:events:7",
        fields={"type": "msg.inbound", "payload": '{"a":1}'},
        maxlen=100,
    )
    assert stream_id
    assert isinstance(stream_id, str)
    assert "-" in stream_id  # Redis stream IDs are "timestamp-seq"


async def test_xadd_event_honors_maxlen(fake_redis):
    for i in range(5):
        await xadd_event(
            fake_redis,
            "oqim:events:7",
            fields={"type": "msg.inbound", "n": str(i)},
            maxlen=2,
            approximate=False,  # exact cap for deterministic test
        )
    length = await fake_redis.xlen("oqim:events:7")
    assert length == 2


async def test_xadd_event_can_append_without_retention_cap(fake_redis):
    for i in range(5):
        await xadd_event(
            fake_redis,
            "oqim:events:7",
            fields={"type": "msg.inbound", "n": str(i)},
            maxlen=None,
        )

    length = await fake_redis.xlen("oqim:events:7")
    assert length == 5
