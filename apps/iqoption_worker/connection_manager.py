"""Single owner of IQ Option connection and reconnect policy."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apps.core.resilience.circuit_breakers import ConnectionBreaker
from packages.brokers.iqoption_adapter import IQOptionAdapter
from packages.brokers.port import BrokerError


@dataclass(frozen=True, slots=True)
class BackoffConfig:
    base_delay: float = 0.5
    max_delay: float = 30.0
    max_attempts: int = 10
    circuit_timeout: float = 300.0

    def __post_init__(self) -> None:
        if self.base_delay <= 0 or self.max_delay < self.base_delay:
            raise ValueError("invalid backoff delays")
        if not 1 <= self.max_attempts <= 100:
            raise ValueError("max_attempts must be between 1 and 100")
        if self.circuit_timeout <= 0:
            raise ValueError("circuit_timeout must be positive")


@dataclass(slots=True)
class BackoffState:
    attempt: int = 0
    last_delay: float = 0.0
    last_attempt_at: float | None = None
    synchronized: bool = False


class ConnectionManager:
    def __init__(
        self,
        adapter: IQOptionAdapter,
        *,
        config: BackoffConfig | None = None,
        breaker: ConnectionBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.adapter = adapter
        self.config = config or BackoffConfig()
        self.breaker = breaker or ConnectionBreaker(recovery_timeout=self.config.circuit_timeout)
        self.state = BackoffState()
        self._sleep = sleep
        self._random_uniform = random_uniform
        self._clock = clock
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        async with self._lock:
            if self.is_authenticated():
                return True
            while self.state.attempt < self.config.max_attempts:
                if not self.breaker.can_execute():
                    return False
                self.state.attempt += 1
                self.state.last_attempt_at = self._clock()
                try:
                    await asyncio.to_thread(self.adapter.connect)
                except BrokerError:
                    self.breaker.record_failure()
                    if self.state.attempt >= self.config.max_attempts:
                        return False
                    await self._wait_before_retry()
                    continue
                self.breaker.record_success()
                return self.is_authenticated()
            return False

    async def disconnect(self) -> None:
        async with self._lock:
            if self.adapter.is_connected():
                await asyncio.to_thread(self.adapter.disconnect)
            self.state.synchronized = False

    async def reconnect(self) -> bool:
        await self.disconnect()
        return await self.connect()

    def is_connected(self) -> bool:
        return self.adapter.is_connected()

    def is_authenticated(self) -> bool:
        return self.adapter.is_connected() and self.adapter.is_authenticated()

    def mark_synchronized(self) -> None:
        """Reset backoff only after subscriptions, snapshot and reconciliation complete."""
        if not self.is_authenticated():
            raise RuntimeError("cannot mark an unauthenticated connection synchronized")
        self.state.attempt = 0
        self.state.last_delay = 0.0
        self.state.synchronized = True

    def mark_unsynchronized(self) -> None:
        self.state.synchronized = False

    async def _wait_before_retry(self) -> None:
        ceiling = min(
            self.config.max_delay,
            self.config.base_delay * (2 ** max(0, self.state.attempt - 1)),
        )
        delay = self._random_uniform(0.0, ceiling)
        self.state.last_delay = delay
        await self._sleep(delay)


__all__ = ["BackoffConfig", "BackoffState", "ConnectionManager"]
