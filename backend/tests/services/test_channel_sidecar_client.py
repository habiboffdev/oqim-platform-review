from __future__ import annotations

import httpx

from app.services.channel_sidecar_client import (
    extract_retry_after_seconds,
)


def test_extract_retry_after_seconds_prefers_json_retry_after():
    resp = httpx.Response(
        429,
        json={"retryAfter": 12},
        headers={"retry-after": "30"},
    )

    assert extract_retry_after_seconds(resp) == 12.0


def test_extract_retry_after_seconds_reads_retry_after_header():
    resp = httpx.Response(429, headers={"retry-after": "9"})

    assert extract_retry_after_seconds(resp) == 9.0


def test_extract_retry_after_seconds_clamps_and_defaults():
    assert extract_retry_after_seconds(httpx.Response(429, json={"retryAfter": 0})) == 1.0
    assert extract_retry_after_seconds(httpx.Response(429, content=b"nope")) == 5.0
