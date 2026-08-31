from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from apps.core.orchestrator.leader_lease import LeaderLease
from packages.persistence.redis_store import RedisStore


def test_lease_acquire_renew_and_release() -> None:
    async def scenario() -> None:
        store = RedisStore()
        lease = LeaderLease(
            store,
            resource="test-lease-acquire",
            leader_id="worker-a",
            min_time_between_leader_changes_seconds=0,
        )
        assert await lease.acquire() is True
        assert lease.is_leader()
        assert lease.get_fencing_token() == 1
        assert await lease.renew() is True
        await lease.release()
        assert not lease.is_leader()

    asyncio.run(scenario())


def test_two_workers_compete_and_fencing_token_increments() -> None:
    async def scenario() -> None:
        store = RedisStore()
        first = LeaderLease(
            store,
            resource="test-lease-race",
            leader_id="worker-a",
            min_time_between_leader_changes_seconds=0,
        )
        second = LeaderLease(
            store,
            resource="test-lease-race",
            leader_id="worker-b",
            min_time_between_leader_changes_seconds=0,
        )
        acquired = await asyncio.gather(first.acquire(), second.acquire())
        assert sum(acquired) == 1
        leader = first if acquired[0] else second
        standby = second if acquired[0] else first
        old_token = leader.get_fencing_token()
        await leader.release()
        assert await standby.acquire() is True
        assert standby.get_fencing_token() == old_token + 1

    asyncio.run(scenario())


def test_lease_loses_leadership_after_expiry() -> None:
    async def scenario() -> None:
        store = RedisStore()
        now = [datetime.now(UTC)]
        lease = LeaderLease(
            store,
            resource="test-lease-expiry",
            leader_id="worker-a",
            ttl_seconds=1,
            min_time_between_leader_changes_seconds=0,
            clock=lambda: now[0],
        )
        assert await lease.acquire()
        now[0] += timedelta(seconds=2)
        assert not lease.is_leader()

    asyncio.run(scenario())
