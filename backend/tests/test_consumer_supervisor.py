import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services.consumer_supervisor import ConsumerSupervisor


pytestmark = pytest.mark.asyncio


class _OneShotConsumer:
    def __init__(self):
        self.started = asyncio.Event()

    async def start(self) -> None:
        self.started.set()

    async def stop(self) -> None:
        return None


class _BlockingConsumer:
    def __init__(self, *, fail_on_stop: bool = False):
        self.fail_on_stop = fail_on_stop
        self.started = asyncio.Event()

    async def start(self) -> None:
        self.started.set()
        await asyncio.Event().wait()

    async def stop(self) -> None:
        if self.fail_on_stop:
            raise RuntimeError("stop failed")


async def test_unexpected_consumer_exit_triggers_restart_state():
    supervisor = ConsumerSupervisor()
    consumer = _OneShotConsumer()
    supervisor.register("oneshot", consumer)

    await supervisor.start_all()
    await consumer.started.wait()
    await asyncio.sleep(0.05)

    status = supervisor.get_status("oneshot")
    assert status is not None
    assert status["status"] == "restarting"
    assert status["restart_count"] >= 1
    assert status["last_error"] == "RuntimeError: Consumer exited unexpectedly"
    assert supervisor.is_healthy() is False

    await supervisor.stop_all()


async def test_stop_all_isolates_stop_failures():
    supervisor = ConsumerSupervisor()
    bad_consumer = _BlockingConsumer(fail_on_stop=True)
    good_consumer = _BlockingConsumer()
    supervisor.register("bad", bad_consumer)
    supervisor.register("good", good_consumer)

    await supervisor.start_all()
    await asyncio.gather(bad_consumer.started.wait(), good_consumer.started.wait())

    await supervisor.stop_all()

    assert supervisor.get_status("bad")["status"] == "stopped"
    assert supervisor.get_status("good")["status"] == "stopped"
    assert "RuntimeError: stop failed" in (supervisor.get_status("bad")["last_error"] or "")


async def test_stale_heartbeat_marks_supervisor_unhealthy():
    supervisor = ConsumerSupervisor()
    consumer = _BlockingConsumer()
    supervisor.register("stale", consumer, heartbeat_timeout_seconds=1.0)

    await supervisor.start_all()
    await consumer.started.wait()

    supervisor._status["stale"]["last_heartbeat"] = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()

    report = supervisor.health_report()
    assert report["stale"]["heartbeat_stale"] is True
    assert supervisor.is_healthy() is False

    await supervisor.stop_all()
