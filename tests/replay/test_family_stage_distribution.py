"""Deterministic 24-hour replay evidence for family refusal stages."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.core.families import (
    F1Reversal,
    F2Pullback,
    F3LevelRejection,
    F4SqueezeBreak,
    F5Quadrant,
)
from packages.domain.market import MarketCandle
from packages.domain.models import Broker
from packages.strategies.models import RuntimeContext


def _fixture_24h() -> list[MarketCandle]:
    base = datetime(2026, 9, 2, tzinfo=UTC)
    candles: list[MarketCandle] = []
    previous = Decimal("100")
    for index in range(24 * 60):
        trend = Decimal((index % 120) - 60) / Decimal(1_000)
        noise = Decimal(((index * 17) % 13) - 6) / Decimal(10_000)
        close = Decimal("100") + trend + noise
        opened = previous
        candles.append(
            MarketCandle(
                broker=Broker.IQ_OPTION,
                broker_symbol="EURUSD",
                timeframe_seconds=60,
                open_time=base + timedelta(minutes=index),
                close_time=base + timedelta(minutes=index + 1),
                open=opened,
                high=max(opened, close) + Decimal("0.02"),
                low=min(opened, close) - Decimal("0.02"),
                close=close,
                is_closed=True,
                tick_volume=100 + (index * 37) % 80,
            )
        )
        previous = close
    return candles


def test_family_stage_distribution_over_deterministic_24h_fixture() -> None:
    candles = _fixture_24h()
    families = {
        "F1": F1Reversal("replay:f1", {}, asset="EURUSD"),
        "F2": F2Pullback("replay:f2", {}, asset="EURUSD"),
        "F3": F3LevelRejection("replay:f3", {}, asset="EURUSD"),
        "F4": F4SqueezeBreak("replay:f4", {}, asset="EURUSD"),
        "F5": F5Quadrant("replay:f5", {}, asset="EURUSD"),
    }
    observed: dict[str, dict[str, int]] = {}
    for family_name, family in families.items():
        context = RuntimeContext(
            broker=Broker.IQ_OPTION,
            account_id="fixture",
            symbol="EURUSD",
            product="BINARY_OPTION",
            timeframe_seconds=60,
            strategy_id=family.strategy_key,
            strategy_version="1.0.0",
            configuration_version="fixture-24h",
        )
        stages: Counter[str] = Counter()
        for index in range(len(candles)):
            history = candles[max(0, index - 119) : index + 1]
            stages[family.evaluate_detailed(history, context).stage] += 1
        observed[family_name] = dict(sorted(stages.items()))

    print(observed)
    assert all(sum(stages.values()) == 1_440 for stages in observed.values())
    assert {
        family_name: stages.get("WARMING_UP", 0) for family_name, stages in observed.items()
    } == {"F1": 27, "F2": 19, "F3": 0, "F4": 38, "F5": 14}
