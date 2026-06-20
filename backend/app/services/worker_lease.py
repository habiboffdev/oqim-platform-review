"""Redis-backed worker leases for API-lifespan background roles."""

from __future__ import annotations

import socket
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from redis.exceptions import ResponseError


LEASE_KEY_PREFIX = "oqim:worker_lease:"

_RENEW_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("EXPIRE", KEYS[1], tonumber(ARGV[2]))
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""


def make_worker_owner_id(role: str) -> str:
    return f"{socket.gethostname()}:{role}:{uuid.uuid4().hex[:12]}"


def worker_lease_key(role: str) -> str:
    return f"{LEASE_KEY_PREFIX}{role}"


def worker_lease_counter_key(role: str, counter: str) -> str:
    return f"{LEASE_KEY_PREFIX}{role}:{counter}"


@dataclass(slots=True)
class WorkerLeaseSnapshot:
    role: str
    active: bool
    owner: str | None
    ttl_seconds: int | None
    contended_count: int
    lost_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkerLease:
    """Small distributed singleton lease.

    The lease reduces duplicate worker work across API processes. Database
    idempotency remains the source of safety; this only shrinks contention and
    blast radius before work begins.
    """

    def __init__(
        self,
        redis: Any,
        *,
        role: str,
        owner_id: str | None = None,
        ttl_seconds: int = 30,
    ) -> None:
        self.redis = redis
        self.role = role
        self.owner_id = owner_id or make_worker_owner_id(role)
        self.ttl_seconds = ttl_seconds
        self.key = worker_lease_key(role)

    @staticmethod
    def _is_eval_unsupported(exc: ResponseError) -> bool:
        return "unknown command" in str(exc).lower() and "eval" in str(exc).lower()

    async def acquire(self) -> bool:
        acquired = await self.redis.set(
            self.key,
            self.owner_id,
            ex=self.ttl_seconds,
            nx=True,
        )
        if bool(acquired):
            return True
        await self.redis.incr(worker_lease_counter_key(self.role, "contended"))
        return False

    async def renew(self) -> bool:
        try:
            renewed = await self.redis.eval(
                _RENEW_SCRIPT,
                1,
                self.key,
                self.owner_id,
                str(self.ttl_seconds),
            )
        except (AttributeError, NotImplementedError):
            renewed = await self._renew_without_lua()
        except ResponseError as exc:
            if not self._is_eval_unsupported(exc):
                raise
            renewed = await self._renew_without_lua()
        if int(renewed or 0) == 1:
            return True
        await self.redis.incr(worker_lease_counter_key(self.role, "lost"))
        return False

    async def _renew_without_lua(self) -> int:
        owner = await self.redis.get(self.key)
        owner_text = owner.decode() if isinstance(owner, bytes) else owner
        renewed = 1 if owner_text == self.owner_id else 0
        if renewed:
            await self.redis.expire(self.key, self.ttl_seconds)
        return renewed

    async def release(self) -> bool:
        try:
            released = await self.redis.eval(
                _RELEASE_SCRIPT,
                1,
                self.key,
                self.owner_id,
            )
        except (AttributeError, NotImplementedError):
            released = await self._release_without_lua()
        except ResponseError as exc:
            if not self._is_eval_unsupported(exc):
                raise
            released = await self._release_without_lua()
        return int(released or 0) == 1

    async def _release_without_lua(self) -> int:
        owner = await self.redis.get(self.key)
        owner_text = owner.decode() if isinstance(owner, bytes) else owner
        released = 0
        if owner_text == self.owner_id:
            released = await self.redis.delete(self.key)
        return int(released or 0)

    async def snapshot(self) -> WorkerLeaseSnapshot:
        owner = await self.redis.get(self.key)
        ttl = await self.redis.ttl(self.key)
        contended = await self.redis.get(worker_lease_counter_key(self.role, "contended"))
        lost = await self.redis.get(worker_lease_counter_key(self.role, "lost"))
        owner_text = owner.decode() if isinstance(owner, bytes) else owner
        try:
            ttl_value = int(ttl)
        except (TypeError, ValueError):
            ttl_value = -1
        ttl_int = ttl_value if ttl_value >= 0 else None
        return WorkerLeaseSnapshot(
            role=self.role,
            active=bool(owner_text),
            owner=str(owner_text) if owner_text else None,
            ttl_seconds=ttl_int,
            contended_count=int(contended or 0),
            lost_count=int(lost or 0),
        )


async def load_worker_lease_snapshots(
    redis: Any,
    *,
    roles: tuple[str, ...],
) -> dict[str, WorkerLeaseSnapshot]:
    snapshots: dict[str, WorkerLeaseSnapshot] = {}
    for role in roles:
        lease = WorkerLease(redis, role=role)
        snapshots[role] = await lease.snapshot()
    return snapshots
