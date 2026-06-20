from __future__ import annotations

from app.services.worker_lease import WorkerLease, worker_lease_counter_key


async def test_worker_lease_allows_only_one_owner(fake_redis):
    first = WorkerLease(
        fake_redis,
        role="agent_runtime_worker",
        owner_id="worker-a",
        ttl_seconds=30,
    )
    second = WorkerLease(
        fake_redis,
        role="agent_runtime_worker",
        owner_id="worker-b",
        ttl_seconds=30,
    )

    assert await first.acquire() is True
    assert await second.acquire() is False

    snapshot = await first.snapshot()
    assert snapshot.active is True
    assert snapshot.owner == "worker-a"
    assert snapshot.contended_count == 1


async def test_worker_lease_release_requires_owner(fake_redis):
    first = WorkerLease(
        fake_redis,
        role="action_runtime_worker",
        owner_id="worker-a",
        ttl_seconds=30,
    )
    second = WorkerLease(
        fake_redis,
        role="action_runtime_worker",
        owner_id="worker-b",
        ttl_seconds=30,
    )

    assert await first.acquire() is True
    assert await second.release() is False
    assert await first.release() is True
    assert await second.acquire() is True


async def test_worker_lease_renew_tracks_lost_ownership(fake_redis):
    lease = WorkerLease(
        fake_redis,
        role="agent_runtime_worker",
        owner_id="worker-a",
        ttl_seconds=30,
    )

    assert await lease.renew() is False
    assert await fake_redis.get(
        worker_lease_counter_key("agent_runtime_worker", "lost")
    ) == "1"
