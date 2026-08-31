from __future__ import annotations

import asyncio

from apps.core.resilience.chaos import ChaosInjector, ChaosScenario


def test_chaos_injector_recovers_after_bounded_fault() -> None:
    events: list[tuple[ChaosScenario, bool]] = []

    async def scenario() -> None:
        await ChaosInjector(lambda kind, enabled: events.append((kind, enabled))).inject(
            ChaosScenario.NETWORK_PARTITION, 0.001
        )

    asyncio.run(scenario())
    assert events == [
        (ChaosScenario.NETWORK_PARTITION, True),
        (ChaosScenario.NETWORK_PARTITION, False),
    ]


def test_chaos_injector_rejects_invalid_duration() -> None:
    async def scenario() -> None:
        try:
            await ChaosInjector().inject(ChaosScenario.API_TIMEOUT, 0)
        except ValueError:
            return
        raise AssertionError("invalid duration accepted")

    asyncio.run(scenario())
