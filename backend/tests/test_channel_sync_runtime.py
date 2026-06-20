from app.services.channel_sync_runtime import ChannelSyncRateLimitError, ChannelSyncRuntime


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def _fake_sleep_factory(clock: _FakeClock, sleeps: list[float]):
    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    return _sleep


async def test_runtime_waits_for_operation_spacing():
    clock = _FakeClock()
    sleeps: list[float] = []
    runtime = ChannelSyncRuntime(
        operation_delays={"messages": 0.5},
        clock=clock,
        sleep=await _fake_sleep_factory(clock, sleeps),
    )
    calls: list[str] = []

    async def _call() -> str:
        calls.append("run")
        return "ok"

    first = await runtime.run(
        workspace_id=1,
        channel="telegram_dm",
        operation="messages",
        func=_call,
    )
    second = await runtime.run(
        workspace_id=1,
        channel="telegram_dm",
        operation="messages",
        func=_call,
    )

    assert first == "ok"
    assert second == "ok"
    assert calls == ["run", "run"]
    assert sleeps == [0.5]


async def test_runtime_records_cooldown_after_rate_limit():
    clock = _FakeClock()
    sleeps: list[float] = []
    runtime = ChannelSyncRuntime(
        operation_delays={"messages": 0.25},
        max_wait_seconds=5.0,
        clock=clock,
        sleep=await _fake_sleep_factory(clock, sleeps),
    )
    calls = {"count": 0}

    async def _rate_limited() -> str:
        calls["count"] += 1
        raise ChannelSyncRateLimitError(
            retry_after_seconds=3,
            channel="telegram_dm",
            operation="messages",
        )

    async def _success() -> str:
        calls["count"] += 1
        return "ok"

    try:
        await runtime.run(
            workspace_id=1,
            channel="telegram_dm",
            operation="messages",
            func=_rate_limited,
        )
    except ChannelSyncRateLimitError:
        pass
    else:
        raise AssertionError("Expected ChannelSyncRateLimitError")

    result = await runtime.run(
        workspace_id=1,
        channel="telegram_dm",
        operation="messages",
        func=_success,
    )

    assert result == "ok"
    assert calls["count"] == 2
    assert sleeps == [3.0]
