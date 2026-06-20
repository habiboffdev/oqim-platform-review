"""Small shared helpers for GramJS sidecar HTTP responses."""

from __future__ import annotations

import httpx


def extract_retry_after_seconds(resp: httpx.Response) -> float:
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    retry_after = None
    if isinstance(payload, dict):
        retry_after = payload.get("retryAfter")
    if retry_after is None:
        retry_after = resp.headers.get("retry-after")
    try:
        return max(1.0, float(retry_after))
    except (TypeError, ValueError):
        return 5.0
