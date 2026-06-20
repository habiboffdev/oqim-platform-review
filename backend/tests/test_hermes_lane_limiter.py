from __future__ import annotations

import asyncio

from app.modules.hermes_runtime.lane_limiter import HermesLaneLimiter, HermesLaneLimits


async def test_global_lane_cap_rejects_extra_work_without_hanging() -> None:
    limiter = HermesLaneLimiter(
        HermesLaneLimits(
            global_caps={"fast_interactive": 1},
            per_workspace_caps={"fast_interactive": 1},
            wait_timeout_seconds=0.01,
        )
    )

    first, release = await limiter.acquire(lane="fast_interactive", workspace_id=1)
    second, second_release = await limiter.acquire(lane="fast_interactive", workspace_id=2)

    assert first.acquired is True
    assert second.acquired is False
    assert second.reason == "lane_capacity_timeout"

    release.release()
    second_release.release()


async def test_per_workspace_cap_blocks_one_workspace_without_blocking_another() -> None:
    limiter = HermesLaneLimiter(
        HermesLaneLimits(
            global_caps={"fast_interactive": 2},
            per_workspace_caps={"fast_interactive": 1},
            wait_timeout_seconds=0.01,
        )
    )

    first, first_release = await limiter.acquire(lane="fast_interactive", workspace_id=1)
    same_workspace, same_release = await limiter.acquire(lane="fast_interactive", workspace_id=1)
    other_workspace, other_release = await limiter.acquire(lane="fast_interactive", workspace_id=2)

    assert first.acquired is True
    assert same_workspace.acquired is False
    assert other_workspace.acquired is True

    first_release.release()
    same_release.release()
    other_release.release()


async def test_limit_context_manager_releases_after_exception() -> None:
    limiter = HermesLaneLimiter(
        HermesLaneLimits(
            global_caps={"background": 1},
            per_workspace_caps={"background": 1},
            wait_timeout_seconds=0.01,
        )
    )

    try:
        async with limiter.limit(lane="background", workspace_id=1) as acquisition:
            assert acquisition.acquired is True
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    async with limiter.limit(lane="background", workspace_id=1) as acquisition:
        assert acquisition.acquired is True


async def test_timeout_does_not_wait_forever() -> None:
    limiter = HermesLaneLimiter(
        HermesLaneLimits(
            global_caps={"deep_analysis": 1},
            per_workspace_caps={"deep_analysis": 1},
            wait_timeout_seconds=0.01,
        )
    )
    first, release = await limiter.acquire(lane="deep_analysis", workspace_id=1)

    started = asyncio.get_running_loop().time()
    second, second_release = await limiter.acquire(
        lane="deep_analysis",
        workspace_id=1,
        wait_timeout_seconds=0.01,
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert first.acquired is True
    assert second.acquired is False
    assert elapsed < 0.2

    release.release()
    second_release.release()
