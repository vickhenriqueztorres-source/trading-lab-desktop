"""Opt-in fault injection primitives for local resilience tests.

The injector is deliberately callback based.  It cannot reach a broker or
submit an order; production callers must provide explicit local test hooks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum


class ChaosScenario(StrEnum):
    NETWORK_PARTITION = "NETWORK_PARTITION"
    DATABASE_CRASH = "DATABASE_CRASH"
    WORKER_CRASH = "WORKER_CRASH"
    LEADER_CRASH = "LEADER_CRASH"
    API_TIMEOUT = "API_TIMEOUT"
    HIGH_LATENCY = "HIGH_LATENCY"
    MESSAGE_LOSS = "MESSAGE_LOSS"


Hook = Callable[[ChaosScenario, bool], Awaitable[None] | None]


class ChaosInjector:
    """Inject one bounded scenario and always emit a matching recovery hook."""

    def __init__(self, hook: Hook | None = None) -> None:
        self._hook = hook
        self.active: ChaosScenario | None = None

    async def inject(self, scenario: ChaosScenario, duration: float) -> None:
        if duration <= 0 or duration > 3600:
            raise ValueError("chaos duration must be between 0 and 3600 seconds")
        if self.active is not None:
            raise RuntimeError("another chaos scenario is active")
        self.active = ChaosScenario(scenario)
        try:
            await self._notify(self.active, True)
            await asyncio.sleep(duration)
        finally:
            active = self.active
            self.active = None
            if active is not None:
                await self._notify(active, False)

    async def _notify(self, scenario: ChaosScenario, enabled: bool) -> None:
        if self._hook is None:
            return
        result = self._hook(scenario, enabled)
        if asyncio.iscoroutine(result):
            await result


__all__ = ["ChaosInjector", "ChaosScenario"]
