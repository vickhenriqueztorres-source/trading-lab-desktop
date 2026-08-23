from __future__ import annotations

from decimal import Decimal

import pytest

from packages.domain.models import Broker
from packages.market_data import ClosedCandle
from packages.strategy_catalog.metrics import StrategyPerformanceMetrics
from packages.strategy_catalog.walk_forward import WalkForwardEngine


def _make_candle(index: int, open_sec: int, close_sec: int) -> ClosedCandle:
    return ClosedCandle(
        broker=Broker.DERIV,
        symbol="frxEURUSD",
        timeframe_seconds=60,
        open_time_ms=open_sec * 1000,
        close_time_ms=close_sec * 1000,
        open_units=100000,
        high_units=100050,
        low_units=99950,
        close_units=100010,
        price_scale=100000,
        source="TEST",
        source_event_id=f"src_{index}",
        source_timestamp_ms=close_sec * 1000,
        received_timestamp_ms=close_sec * 1000,
    )


def test_walk_forward_engine_generates_valid_non_overlapping_windows() -> None:
    # Create 30 consecutive candles of 60 seconds
    candles = [_make_candle(i, 1000 + i * 60, 1000 + (i + 1) * 60) for i in range(30)]

    # in_sample: 10, out_of_sample: 5, step: 5 -> exactly 4 windows
    windows = WalkForwardEngine.generate_rolling_windows(
        candles,
        in_sample_size=10,
        out_of_sample_size=5,
        step_size=5,
    )

    assert len(windows) == 4

    for i, w in enumerate(windows):
        assert w.window_index == i
        assert w.in_sample_candles_count == 10
        assert w.out_of_sample_candles_count == 5
        # Strict temporal non-overlapping assertion:
        assert w.in_sample_end_epoch <= w.out_of_sample_start_epoch
        assert w.in_sample_start_epoch < w.in_sample_end_epoch
        assert w.out_of_sample_start_epoch < w.out_of_sample_end_epoch


def test_walk_forward_engine_rejects_unsorted_candles() -> None:
    candles = [
        _make_candle(0, 1000, 1060),
        _make_candle(1, 2000, 2060),
        _make_candle(2, 1500, 1560),  # out of order
    ]

    with pytest.raises(ValueError, match="sorted strictly ascending"):
        WalkForwardEngine.generate_rolling_windows(
            candles,
            in_sample_size=1,
            out_of_sample_size=1,
        )


def test_walk_forward_summary_evaluation() -> None:
    m1 = StrategyPerformanceMetrics(
        total_trades=10,
        winning_trades=7,
        losing_trades=3,
        win_rate_decimal=Decimal("0.70"),
        gross_profit_minor_units=5000,
        gross_loss_minor_units=2000,
        net_profit_minor_units=3000,
        profit_factor_decimal=Decimal("2.5"),
        max_drawdown_minor_units=500,
        max_drawdown_pct_decimal=Decimal("0.05"),
        expectancy_minor_units_decimal=Decimal("300"),
    )
    m2 = StrategyPerformanceMetrics(
        total_trades=10,
        winning_trades=6,
        losing_trades=4,
        win_rate_decimal=Decimal("0.60"),
        gross_profit_minor_units=4000,
        gross_loss_minor_units=2500,
        net_profit_minor_units=1500,
        profit_factor_decimal=Decimal("1.6"),
        max_drawdown_minor_units=800,
        max_drawdown_pct_decimal=Decimal("0.08"),
        expectancy_minor_units_decimal=Decimal("150"),
    )
    m3 = StrategyPerformanceMetrics(
        total_trades=10,
        winning_trades=3,
        losing_trades=7,
        win_rate_decimal=Decimal("0.30"),
        gross_profit_minor_units=1000,
        gross_loss_minor_units=4000,
        net_profit_minor_units=-3000,
        profit_factor_decimal=Decimal("0.25"),
        max_drawdown_minor_units=3000,
        max_drawdown_pct_decimal=Decimal("0.30"),
        expectancy_minor_units_decimal=Decimal("-300"),
    )

    summary = WalkForwardEngine.evaluate_summary(
        [m1, m2, m3],
        min_win_rate=Decimal("0.50"),
        min_robustness=Decimal("0.60"),
    )

    assert summary.total_windows == 3
    assert summary.windows_passed == 2
    assert summary.robustness_score_decimal == Decimal("2") / Decimal("3")
    assert summary.is_approved is True
