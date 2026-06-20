from __future__ import annotations

from typing import Any


def parse_usage_cost_policy(raw: str | None) -> dict[str, dict[str, int]]:
    policy: dict[str, dict[str, int]] = {}
    for item in str(raw or "").split(","):
        parts = [part.strip().lower() for part in item.split(":")]
        if len(parts) != 3:
            continue
        provider, direction, raw_rate = parts
        if not provider or direction not in {"input", "output"}:
            continue
        try:
            rate = max(0, int(raw_rate))
        except ValueError:
            continue
        policy.setdefault(provider, {})[direction] = rate
    return dict(
        sorted(
            (provider, dict(sorted(rates.items())))
            for provider, rates in policy.items()
        )
    )


def estimate_token_cost_micros(
    *,
    provider: str,
    direction: str,
    tokens: int,
    cost_policy: dict[str, dict[str, int]],
) -> int:
    rate = cost_policy.get(provider, {}).get(direction, 0)
    return int(round(max(0, tokens) * rate / 1000))


def estimate_daily_usage_cost_micros(
    usage: dict[str, Any],
    *,
    cost_policy: dict[str, dict[str, int]],
) -> int:
    total = 0
    for raw_key, raw_value in usage.items():
        key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
        parts = key.split(":")
        if len(parts) != 3:
            continue
        _operation, provider, direction = parts
        try:
            tokens = int(raw_value)
        except (TypeError, ValueError):
            continue
        total += estimate_token_cost_micros(
            provider=provider,
            direction=direction,
            tokens=tokens,
            cost_policy=cost_policy,
        )
    return total
