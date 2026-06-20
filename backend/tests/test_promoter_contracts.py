"""Promoter contracts: caps + pure helpers."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.bi_promoter.contracts import (
    PROMOTER_DEFAULT_CAPS,
    TARGET_STATES,
    TIERS,
    SegmentSpec,
    effective_caps,
    within_working_hours,
)

pytestmark = pytest.mark.asyncio


async def test_default_caps_shape():
    assert PROMOTER_DEFAULT_CAPS["cold_daily"] >= 1
    assert PROMOTER_DEFAULT_CAPS["tz"] == "Asia/Tashkent"
    assert PROMOTER_DEFAULT_CAPS["hours"] == [9, 19]
    lo, hi = PROMOTER_DEFAULT_CAPS["jitter_s"]
    assert 0 < lo < hi


async def test_effective_caps_overrides_only_given_keys():
    caps = effective_caps({"cold_daily": 10})
    assert caps["cold_daily"] == 10            # overridden
    assert caps["tz"] == PROMOTER_DEFAULT_CAPS["tz"]  # default preserved
    # caller's dict is not mutated, defaults not mutated
    assert PROMOTER_DEFAULT_CAPS["cold_daily"] != 10


async def test_constants_are_the_blessed_sets():
    assert TIERS == ("warm", "cold")
    assert set(TARGET_STATES) == {"pending", "sending", "sent", "replied", "skipped", "failed"}


async def test_within_working_hours_respects_campaign_tz():
    caps = effective_caps(None)  # Asia/Tashkent = UTC+5, hours [9, 19)
    assert within_working_hours(caps, datetime(2026, 6, 15, 5, 0, tzinfo=UTC))       # 10:00 local
    assert within_working_hours(caps, datetime(2026, 6, 15, 4, 0, tzinfo=UTC))       # 09:00 local — start-inclusive
    assert not within_working_hours(caps, datetime(2026, 6, 15, 3, 59, tzinfo=UTC))  # 08:59 local
    assert not within_working_hours(caps, datetime(2026, 6, 15, 14, 0, tzinfo=UTC))  # 19:00 local — end-exclusive


async def test_within_working_hours_falls_back_on_malformed_hours():
    # A truthy-but-short hours override must not crash — fall back to defaults.
    caps = {"hours": [9], "tz": "Asia/Tashkent"}
    assert within_working_hours(caps, datetime(2026, 6, 15, 5, 0, tzinfo=UTC))  # 10:00 local


async def test_default_caps_active_window():
    assert PROMOTER_DEFAULT_CAPS["active_window_h"] == 72


async def test_segment_spec_carries_pipeline_id():
    spec = SegmentSpec(pipeline_id="777", stage_ids=("111",))
    assert spec.pipeline_id == "777"
    assert SegmentSpec().pipeline_id == ""  # default: contact-list segment
