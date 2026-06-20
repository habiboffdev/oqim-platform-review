import asyncio
from datetime import datetime, timezone
from typing import Any, Protocol

from app.core.logging import get_logger

logger = get_logger("supervisor")


class Consumer(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def set_heartbeat_callback(self, callback) -> None: ...


class ConsumerSupervisor:
    """Wraps consumer tasks with auto-restart on crash and liveness tracking."""

    def __init__(self):
        self._consumers: dict[str, Consumer] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._max_backoff = 60.0
        self._stopping = False

    def register(self, name: str, consumer: Consumer, *, heartbeat_timeout_seconds: float = 15.0):
        self._consumers[name] = consumer
        self._status[name] = {
            "status": "registered",
            "last_heartbeat": None,
            "restart_count": 0,
            "last_error": None,
            "heartbeat_timeout_seconds": heartbeat_timeout_seconds,
        }
        callback = getattr(consumer, "set_heartbeat_callback", None)
        if callable(callback):
            callback(lambda: self.heartbeat(name))

    async def start_all(self):
        self._stopping = False
        for name, consumer in self._consumers.items():
            task = asyncio.create_task(
                self._run_with_restart(name, consumer),
                name=f"consumer-supervisor:{name}",
            )
            self._tasks[name] = task
            self._status[name]["status"] = "running"
            self._status[name]["last_heartbeat"] = datetime.now(timezone.utc).isoformat()

    async def stop_all(self):
        self._stopping = True
        # Cancel tasks first, then await, then call stop() for cleanup
        for name, task in self._tasks.items():
            task.cancel()
        for name, task in self._tasks.items():
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._status[name]["status"] = "stopped"
        for name, consumer in self._consumers.items():
            try:
                await consumer.stop()
            except Exception as exc:
                self._status[name]["last_error"] = f"{type(exc).__name__}: {exc}"
                logger.exception("Consumer '%s' failed during stop()", name)

    async def _run_with_restart(self, name: str, consumer: Consumer):
        backoff = 1.0
        while not self._stopping:
            try:
                self._status[name]["status"] = "running"
                self._status[name]["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
                await consumer.start()
                if self._stopping:
                    break
                raise RuntimeError("Consumer exited unexpectedly")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._status[name]["restart_count"] += 1
                self._status[name]["status"] = "restarting"
                self._status[name]["last_error"] = f"{type(e).__name__}: {e}"
                logger.error(
                    "Consumer '%s' crashed (restart #%d, backoff %.1fs): %s",
                    name, self._status[name]["restart_count"], backoff, e,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
        if self._stopping:
            self._status[name]["status"] = "stopped"

    def heartbeat(self, name: str):
        if name in self._status:
            self._status[name]["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
            self._status[name]["status"] = "running"

    def get_status(self, name: str) -> dict | None:
        return self._status.get(name)

    def health_report(self) -> dict[str, dict]:
        report: dict[str, dict] = {}
        now = datetime.now(timezone.utc)
        for name, info in self._status.items():
            entry = dict(info)
            last_heartbeat = info.get("last_heartbeat")
            age_seconds = None
            if last_heartbeat:
                try:
                    age_seconds = (
                        now - datetime.fromisoformat(last_heartbeat)
                    ).total_seconds()
                except ValueError:
                    age_seconds = None
            entry["heartbeat_age_seconds"] = age_seconds
            timeout = float(info.get("heartbeat_timeout_seconds", 15.0))
            entry["heartbeat_stale"] = bool(
                age_seconds is not None
                and info.get("status") == "running"
                and age_seconds > timeout
            )
            report[name] = entry
        return report

    def is_healthy(self) -> bool:
        if any(task.done() for task in self._tasks.values()) and not self._stopping:
            return False
        for info in self.health_report().values():
            if info["status"] not in ("running", "registered", "stopped"):
                return False
            if info.get("heartbeat_stale"):
                return False
        return True
