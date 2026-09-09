from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from apps.core.indicator_cache import (
    INDICATOR_BOOTSTRAP_IDENTITY,
    INDICATOR_EXECUTION_SEMANTICS_VERSION,
    INDICATOR_PRIMITIVES_VERSION,
    IndicatorCache,
    IndicatorCacheReason,
    IndicatorKey,
    IndicatorNodeState,
    ShadowComparisonStatus,
    canonical_indicator_params,
)
from apps.core.iqoption_series_hub import IQOPTION_SERIES_PRODUCT, IQOptionSeriesKey
from packages.domain.market import MarketCandle
from packages.domain.models import Broker
from packages.strategies.iqoption_rsi import calculate_wilder_rsi

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "strategy-lab"
    / "contracts"
    / "replay_contract_vectors.v1.json"
)


def _series_key(
    *,
    asset: str = "EURUSD-OTC",
    generation: str = "worker:g1",
    account_id: str = "IQOPTION_PRACTICE",
    timeframe_seconds: int = 60,
) -> IQOptionSeriesKey:
    return IQOptionSeriesKey(
        broker=Broker.IQ_OPTION,
        account_id=account_id,
        product=IQOPTION_SERIES_PRODUCT,
        generation=generation,
        asset=asset,
        timeframe_seconds=timeframe_seconds,
    )


def _indicator_key(
    *,
    series_key: IQOptionSeriesKey | None = None,
    period: int = 14,
    lower: str = "30",
    upper: str = "70",
) -> IndicatorKey:
    return IndicatorKey(
        series_key=series_key or _series_key(),
        name="rsi_extreme",
        params=canonical_indicator_params(
            {"period": period, "lower": Decimal(lower), "upper": Decimal(upper)}
        ),
        primitives_version=INDICATOR_PRIMITIVES_VERSION,
        execution_semantics_version=INDICATOR_EXECUTION_SEMANTICS_VERSION,
        bootstrap_identity=INDICATOR_BOOTSTRAP_IDENTITY,
    )


def _candle(
    index: int,
    *,
    asset: str = "EURUSD-OTC",
    timeframe_seconds: int = 60,
    close: Decimal | None = None,
    closed: bool = True,
    tick_volume: int | None = 10,
) -> MarketCandle:
    open_time = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index * timeframe_seconds)
    close_price = close if close is not None else Decimal("1.1000") + Decimal(index) / Decimal(1000)
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol=asset,
        timeframe_seconds=timeframe_seconds,
        open_time=open_time,
        close_time=open_time + timedelta(seconds=timeframe_seconds),
        open=close_price,
        high=close_price + Decimal("0.0002"),
        low=close_price - Decimal("0.0002"),
        close=close_price,
        is_closed=closed,
        tick_volume=tick_volume,
    )


def _zigzag(count: int, *, asset: str = "EURUSD-OTC") -> list[MarketCandle]:
    price = Decimal("1.1000")
    candles: list[MarketCandle] = []
    for index in range(count):
        price += Decimal("0.0010") if index % 3 else Decimal("-0.0007")
        candles.append(_candle(index, asset=asset, close=price))
    return candles


def _contract_candle(raw: dict[str, Any], asset: str, tf_seconds: int) -> MarketCandle:
    close_time = datetime.fromtimestamp(int(raw["ts"]), UTC)
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol=asset,
        timeframe_seconds=tf_seconds,
        open_time=close_time - timedelta(seconds=tf_seconds),
        close_time=close_time,
        open=Decimal(raw["o"]),
        high=Decimal(raw["h"]),
        low=Decimal(raw["l"]),
        close=Decimal(raw["c"]),
        tick_volume=raw["tick_vol"],
        is_closed=True,
    )


def test_five_recipes_share_one_rsi14_node_and_one_calculation_per_candle() -> None:
    cache = IndicatorCache()
    key = _indicator_key()
    candles = _zigzag(20)

    for index in range(5):
        cache.acquire(key, recipe_id=f"recipe-{index}")

    outputs = [cache.update(key, candles) for _ in range(5)]

    assert {item.value for item in outputs} == {outputs[0].value}
    assert cache.node_ref_count(key) == 5
    assert cache.stats.nodes_created == 1
    assert cache.stats.indicator_calculations == 20
    assert cache.stats.dedup_hits == 4


def test_different_params_are_isolated_nodes() -> None:
    cache = IndicatorCache()
    rsi14 = _indicator_key(period=14)
    rsi7 = _indicator_key(period=7)
    candles = _zigzag(20)

    cache.acquire(rsi14, recipe_id="recipe-rsi14")
    cache.acquire(rsi7, recipe_id="recipe-rsi7")

    out14 = cache.update(rsi14, candles)
    out7 = cache.update(rsi7, candles)

    assert out14.warmup_required == 15
    assert out7.warmup_required == 8
    assert cache.node_ref_count(rsi14) == 1
    assert cache.node_ref_count(rsi7) == 1
    assert cache.stats.nodes_created == 2
    assert cache.stats.indicator_calculations == 40


def test_releasing_one_recipe_keeps_shared_node_for_other_recipes() -> None:
    cache = IndicatorCache()
    key = _indicator_key()

    cache.acquire(key, recipe_id="a")
    cache.acquire(key, recipe_id="b")
    cache.release(key, recipe_id="a")

    assert cache.node_ref_count(key) == 1
    assert cache.stats.nodes_evicted == 0

    cache.release(key, recipe_id="b")

    assert cache.node_ref_count(key) == 0
    assert cache.stats.nodes_evicted == 1


def test_reconnect_generation_uses_distinct_nodes_and_invalidates_without_leak() -> None:
    cache = IndicatorCache()
    old_key = _indicator_key(series_key=_series_key(generation="worker:old"))
    new_key = _indicator_key(series_key=_series_key(generation="worker:new"))

    cache.acquire(old_key, recipe_id="same-recipe")
    cache.acquire(new_key, recipe_id="same-recipe")

    assert cache.node_ref_count(old_key) == 1
    assert cache.node_ref_count(new_key) == 1

    cache.invalidate_series(old_key.series_key, reason=IndicatorCacheReason.SERIES_MISMATCH)
    old_output = cache.update(old_key, _zigzag(15))
    new_output = cache.update(new_key, _zigzag(15))

    assert old_output.state is IndicatorNodeState.INVALID
    assert new_output.state is IndicatorNodeState.READY

    cache.release_recipe("same-recipe")

    assert cache.stats.active_nodes == 0


def test_warmup_gap_symbol_partial_and_correction_are_fail_closed() -> None:
    cache = IndicatorCache()
    key = _indicator_key()
    cache.acquire(key, recipe_id="recipe")

    warming = cache.update(key, _zigzag(5))
    assert warming.state is IndicatorNodeState.WARMING_UP
    assert warming.reason is IndicatorCacheReason.WARMUP_INCOMPLETE

    gap_cache = IndicatorCache()
    gap_key = _indicator_key()
    gap_cache.acquire(gap_key, recipe_id="gap")
    gap = _zigzag(16)
    gap.pop(4)
    assert gap_cache.update(gap_key, gap).reason is IndicatorCacheReason.SERIES_GAP

    symbol_cache = IndicatorCache()
    symbol_key = _indicator_key()
    symbol_cache.acquire(symbol_key, recipe_id="symbol")
    assert (
        symbol_cache.update(symbol_key, _zigzag(16, asset="EURUSD")).reason
        is IndicatorCacheReason.SERIES_MISMATCH
    )

    partial_cache = IndicatorCache()
    partial_key = _indicator_key()
    partial_cache.acquire(partial_key, recipe_id="partial")
    partial = _zigzag(16)
    partial[-1] = _candle(15, closed=False)
    assert partial_cache.update(partial_key, partial).reason is IndicatorCacheReason.PARTIAL_CANDLE

    correction_cache = IndicatorCache()
    correction_key = _indicator_key()
    correction_cache.acquire(correction_key, recipe_id="correction")
    base = _zigzag(16)
    assert correction_cache.update(correction_key, base).state is IndicatorNodeState.READY
    corrected = list(base)
    corrected[3] = _candle(3, close=Decimal("1.9999"))
    assert (
        correction_cache.update(correction_key, corrected).reason
        is IndicatorCacheReason.HISTORICAL_CORRECTION
    )


def test_shadow_compare_matches_same_semantics_and_first_rsi_is_bit_identical() -> None:
    cache = IndicatorCache()
    key = _indicator_key()
    candles = _zigzag(15)
    cache.acquire(key, recipe_id="shadow")

    comparison = cache.shadow_compare(key, candles)
    expected = calculate_wilder_rsi([candle.close for candle in candles])

    assert comparison.status is ShadowComparisonStatus.MATCH
    assert comparison.cached is not None
    assert comparison.cached.value == expected
    assert comparison.reason is IndicatorCacheReason.OK
    assert cache.stats.shadow_compares == 1
    assert cache.stats.shadow_mismatches == 0


def test_rsi_cache_matches_cat04_public_replay_confirm_outputs() -> None:
    vectors = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    case = next(item for item in vectors["cases"] if item["id"] == "f1_eurusd_m1_gate_boundary")
    cache = IndicatorCache()
    key = _indicator_key(
        series_key=_series_key(asset=case["asset"], timeframe_seconds=case["tf_seconds"]),
        period=int(case["params"]["rsi_len"]),
        lower=case["params"]["rsi_lo"],
        upper=case["params"]["rsi_hi"],
    )
    candles = [_contract_candle(raw, case["asset"], case["tf_seconds"]) for raw in case["candles"]]
    cache.acquire(key, recipe_id=case["id"])

    compared = 0
    for index, expected in enumerate(case["expected_trace"]):
        comparison = cache.shadow_compare(key, candles[: index + 1])
        if expected["stage"] == "WARMING_UP" or expected["confirm_value"] is None:
            continue
        assert comparison.status is ShadowComparisonStatus.MATCH
        assert comparison.cached is not None
        assert comparison.cached.value == Decimal(expected["confirm_value"])
        assert comparison.cached.direction == expected["confirm_direction"]
        compared += 1

    assert compared > 0


def test_shadow_mismatch_invalidates_recipe_without_financial_fallback() -> None:
    cache = IndicatorCache()
    key = _indicator_key()
    cache.acquire(key, recipe_id="shadow")
    candles = _zigzag(18)
    first = cache.shadow_compare(key, candles[:16])

    assert first.status is ShadowComparisonStatus.MATCH

    corrected = list(candles[:17])
    corrected[2] = _candle(2, close=Decimal("1.9999"))
    second = cache.shadow_compare(key, corrected)

    assert second.status is ShadowComparisonStatus.INVALID
    assert second.reason is IndicatorCacheReason.HISTORICAL_CORRECTION
    assert cache.stats.invalidations >= 1
    forbidden = {"buy", "sell", "submit", "submit_order", "order", "place_order"}
    assert not forbidden.intersection(dir(IndicatorCache))


def test_capacity_limit_blocks_new_nodes() -> None:
    cache = IndicatorCache(max_nodes=1)
    first = _indicator_key(series_key=_series_key(asset="EURUSD-OTC"))
    second = _indicator_key(series_key=_series_key(asset="GBPUSD-OTC"))

    cache.acquire(first, recipe_id="first")
    with pytest.raises(RuntimeError, match=IndicatorCacheReason.CACHE_CAPACITY_EXCEEDED.value):
        cache.acquire(second, recipe_id="second")
