from __future__ import annotations

import asyncio

from apps.core.resilience.circuit_breakers import CircuitBreaker, CircuitState
from apps.iqoption_worker.connection_manager import BackoffConfig, ConnectionManager
from packages.brokers.iqoption_adapter import IQOptionAdapter


class FlakyClient:
    def __init__(self) -> None:
        self.attempts = 0

    def connect(self) -> dict[str, str]:
        self.attempts += 1
        if self.attempts < 3:
            raise ConnectionError("temporary network failure")
        return {"account_type": "practice"}

    def disconnect(self) -> None:
        pass

    def request(self, operation: str, **payload: object) -> object:
        return []


def test_connection_manager_retries_with_jitter_and_resets_after_sync() -> None:
    asyncio.run(_connection_manager_retries_with_jitter_and_resets_after_sync())


async def _connection_manager_retries_with_jitter_and_resets_after_sync() -> None:
    client = FlakyClient()
    delays: list[float] = []

    async def no_sleep(delay: float) -> None:
        delays.append(delay)

    manager = ConnectionManager(
        IQOptionAdapter(client),
        config=BackoffConfig(base_delay=0.5, max_delay=30, max_attempts=10),
        sleep=no_sleep,
        random_uniform=lambda low, high: high / 2,
    )
    assert await manager.connect() is True
    assert client.attempts == 3
    assert delays == [0.25, 0.5]
    assert manager.state.attempt == 3
    manager.mark_synchronized()
    assert manager.state.attempt == 0
    assert manager.state.synchronized is True


def test_circuit_breaker_recovers_to_half_open_and_closed() -> None:
    now = [0.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        minimum_samples=2,
        recovery_timeout=5,
        clock=lambda: now[0],
    )
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.can_execute() is False
    now[0] = 5.0
    assert breaker.can_execute() is True
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.can_execute() is False
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
