from __future__ import annotations

from decimal import Decimal

from packages.strategy_catalog.metrics import (
    StrategyPerformanceMetrics,
    TradeOutcomeRecord,
    calculate_performance_metrics,
)


def test_calculate_performance_metrics_with_mixed_trades() -> None:
    initial_capital = 100_000  # $1,000.00
    trades = [
        TradeOutcomeRecord(
            trade_id="t1",
            entry_epoch_ms=1700000000000,
            exit_epoch_ms=1700000060000,
            stake_minor_units=1000,
            payout_minor_units=1800,
            pnl_minor_units=800,
            is_win=True,
            symbol="frxEURUSD",
            regime="TRENDING",
            duration_seconds=60,
        ),
        TradeOutcomeRecord(
            trade_id="t2",
            entry_epoch_ms=1700000100000,
            exit_epoch_ms=1700000160000,
            stake_minor_units=1000,
            payout_minor_units=0,
            pnl_minor_units=-1000,
            is_win=False,
            symbol="frxEURUSD",
            regime="RANGING",
            duration_seconds=60,
        ),
        TradeOutcomeRecord(
            trade_id="t3",
            entry_epoch_ms=1700000200000,
            exit_epoch_ms=1700000260000,
            stake_minor_units=1000,
            payout_minor_units=1800,
            pnl_minor_units=800,
            is_win=True,
            symbol="frxEURUSD",
            regime="TRENDING",
            duration_seconds=60,
        ),
    ]

    metrics = calculate_performance_metrics(trades, initial_capital)

    assert metrics.total_trades == 3
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert metrics.win_rate_decimal == Decimal("2") / Decimal("3")
    assert metrics.gross_profit_minor_units == 1600
    assert metrics.gross_loss_minor_units == 1000
    assert metrics.net_profit_minor_units == 600
    assert metrics.profit_factor_decimal == Decimal("1.6")
    assert metrics.expectancy_minor_units_decimal == Decimal("200")
    assert metrics.average_duration_seconds == Decimal("60")
    assert metrics.regime_distribution == {"TRENDING": 2, "RANGING": 1}

    # HWM progression:
    # Initial: 100,000, HWM: 100,000
    # After t1 (+800): 100,800, HWM: 100,800
    # After t2 (-1000): 99,800, Drawdown: 1,000 (from 100,800)
    # After t3 (+800): 100,600, Drawdown: 200
    assert metrics.max_drawdown_minor_units == 1000
    assert metrics.max_drawdown_pct_decimal == Decimal("1000") / Decimal("100800")


def test_calculate_performance_metrics_empty_trades() -> None:
    metrics = calculate_performance_metrics([], 100_000)

    assert metrics.total_trades == 0
    assert metrics.winning_trades == 0
    assert metrics.losing_trades == 0
    assert metrics.win_rate_decimal == Decimal("0.0")
    assert metrics.gross_profit_minor_units == 0
    assert metrics.gross_loss_minor_units == 0
    assert metrics.net_profit_minor_units == 0
    assert metrics.profit_factor_decimal is None
    assert metrics.max_drawdown_minor_units == 0
    assert metrics.max_drawdown_pct_decimal == Decimal("0.0")
    assert metrics.expectancy_minor_units_decimal == Decimal("0.0")
    assert metrics.regime_distribution == {}


def test_calculate_performance_metrics_all_winners() -> None:
    trades = [
        TradeOutcomeRecord(
            trade_id="w1",
            entry_epoch_ms=1000,
            exit_epoch_ms=2000,
            stake_minor_units=1000,
            payout_minor_units=1800,
            pnl_minor_units=800,
            is_win=True,
        ),
        TradeOutcomeRecord(
            trade_id="w2",
            entry_epoch_ms=2000,
            exit_epoch_ms=3000,
            stake_minor_units=1000,
            payout_minor_units=1800,
            pnl_minor_units=800,
            is_win=True,
        ),
    ]

    metrics = calculate_performance_metrics(trades, 50_000)

    assert metrics.total_trades == 2
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 0
    assert metrics.win_rate_decimal == Decimal("1.0")
    assert metrics.profit_factor_decimal is None
    assert metrics.max_drawdown_minor_units == 0
    assert metrics.max_drawdown_pct_decimal == Decimal("0.0")
    assert metrics.net_profit_minor_units == 1600


def test_calculate_performance_metrics_all_losers() -> None:
    trades = [
        TradeOutcomeRecord(
            trade_id="l1",
            entry_epoch_ms=1000,
            exit_epoch_ms=2000,
            stake_minor_units=1000,
            payout_minor_units=0,
            pnl_minor_units=-1000,
            is_win=False,
        ),
        TradeOutcomeRecord(
            trade_id="l2",
            entry_epoch_ms=2000,
            exit_epoch_ms=3000,
            stake_minor_units=1000,
            payout_minor_units=0,
            pnl_minor_units=-1000,
            is_win=False,
        ),
    ]

    metrics = calculate_performance_metrics(trades, 50_000)

    assert metrics.total_trades == 2
    assert metrics.winning_trades == 0
    assert metrics.losing_trades == 2
    assert metrics.win_rate_decimal == Decimal("0.0")
    assert metrics.profit_factor_decimal == Decimal("0.0")
    assert metrics.max_drawdown_minor_units == 2000
    assert metrics.max_drawdown_pct_decimal == Decimal("2000") / Decimal("50000")
    assert metrics.net_profit_minor_units == -2000


def test_metrics_payload_roundtrip() -> None:
    original = StrategyPerformanceMetrics(
        total_trades=10,
        winning_trades=7,
        losing_trades=3,
        win_rate_decimal=Decimal("0.70"),
        gross_profit_minor_units=5600,
        gross_loss_minor_units=3000,
        net_profit_minor_units=2600,
        profit_factor_decimal=Decimal("1.866"),
        max_drawdown_minor_units=1500,
        max_drawdown_pct_decimal=Decimal("0.015"),
        expectancy_minor_units_decimal=Decimal("260"),
        average_duration_seconds=Decimal("120"),
        regime_distribution={"VOLATILE": 4, "TRENDING": 6},
    )

    payload = original.to_payload()
    restored = StrategyPerformanceMetrics.from_payload(payload)

    assert restored == original
