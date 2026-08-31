"""Leader lease with monotonically increasing fencing tokens."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from packages.persistence.redis_store import RedisStore


@dataclass(frozen=True, slots=True)
class LeaseState:
    leader_id: str
    fencing_token: int
    expires_at: datetime
    acquired_at: datetime
    renewed_at: datetime


class LeaderLease:
    def __init__(
        self,
        store: RedisStore,
        *,
        resource: str,
        leader_id: str,
        ttl_seconds: int = 45,
        renew_interval_seconds: int = 15,
        min_time_between_leader_changes_seconds: int = 30,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if ttl_seconds <= 0 or renew_interval_seconds <= 0:
            raise ValueError("lease durations must be positive")
        self.store = store
        self.resource = resource
        self.leader_id = leader_id
        self.ttl_seconds = ttl_seconds
        self.renew_interval_seconds = renew_interval_seconds
        self.min_time_between_leader_changes_seconds = min_time_between_leader_changes_seconds
        self._clock = clock
        self._state: LeaseState | None = None
        self._last_change: datetime | None = None
        self._renew_task: asyncio.Task[None] | None = None

    async def acquire(self) -> bool:
        if self.is_leader():
            return True
        now = self._clock()
        if (
            self._last_change is not None
            and (now - self._last_change).total_seconds()
            < self.min_time_between_leader_changes_seconds
        ):
            return False
        result = await self.store.acquire_lease(self.resource, self.leader_id, self.ttl_seconds)
        if result is None:
            return False
        token, expiry_monotonic = result
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        self._state = LeaseState(self.leader_id, token, expires_at, now, now)
        self._last_change = now
        return expiry_monotonic > 0

    async def renew(self) -> bool:
        if self._state is None:
            return False
        renewed = await self.store.renew_lease(
            self.resource, self.leader_id, self._state.fencing_token, self.ttl_seconds
        )
        if not renewed:
            self._state = None
            return False
        now = self._clock()
        self._state = LeaseState(
            self.leader_id,
            self._state.fencing_token,
            now + timedelta(seconds=self.ttl_seconds),
            self._state.acquired_at,
            now,
        )
        return True

    async def release(self) -> None:
        if self._state is not None:
            await self.store.release_lease(self.resource, self.leader_id, self._state.fencing_token)
        self._state = None

    def is_leader(self) -> bool:
        return self._state is not None and self._state.expires_at > self._clock()

    def get_fencing_token(self) -> int | None:
        return None if not self.is_leader() else self._state.fencing_token  # type: ignore[union-attr]

    @property
    def state(self) -> LeaseState | None:
        return self._state

    async def start_auto_renew(self) -> None:
        if self._renew_task is None or self._renew_task.done():
            self._renew_task = asyncio.create_task(self._renew_loop(), name="leader-lease-renew")

    async def stop_auto_renew(self) -> None:
        if self._renew_task is not None:
            self._renew_task.cancel()
            await asyncio.gather(self._renew_task, return_exceptions=True)
            self._renew_task = None

    async def _renew_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.renew_interval_seconds)
                if self.is_leader() and not await self.renew():
                    return
        except asyncio.CancelledError:
            raise


__all__ = ["LeaderLease", "LeaseState"]
