from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from packages.strategy_catalog.metrics import StrategyPerformanceMetrics

if TYPE_CHECKING:
    from packages.market_data import ClosedCandle


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    window_index: int
    in_sample_start_epoch: int
    in_sample_end_epoch: int
    out_of_sample_start_epoch: int
    out_of_sample_end_epoch: int
    in_sample_candles_count: int
    out_of_sample_candles_count: int

    def __post_init__(self) -> None:
        if self.window_index < 0:
            raise ValueError("window_index cannot be negative")
        if self.in_sample_end_epoch <= self.in_sample_start_epoch:
            raise ValueError("in_sample_end_epoch must be greater than in_sample_start_epoch")
        if self.out_of_sample_start_epoch < self.in_sample_end_epoch:
            raise ValueError("out_of_sample period must not overlap in_sample period")
        if self.out_of_sample_end_epoch <= self.out_of_sample_start_epoch:
            raise ValueError(
                "out_of_sample_end_epoch must be greater than out_of_sample_start_epoch"
            )
        if self.in_sample_candles_count <= 0 or self.out_of_sample_candles_count <= 0:
            raise ValueError("candle counts must be positive")


@dataclass(frozen=True, slots=True)
class WalkForwardSummary:
    total_windows: int
    windows_passed: int
    robustness_score_decimal: Decimal
    window_metrics: tuple[StrategyPerformanceMetrics, ...]
    is_approved: bool


class WalkForwardEngine:
    """Partitions chronological closed candle series into non-overlapping rolling windows."""

    @staticmethod
    def generate_rolling_windows(
        candles: Sequence[ClosedCandle],
        in_sample_size: int,
        out_of_sample_size: int,
        step_size: int | None = None,
    ) -> tuple[WalkForwardWindow, ...]:
        if in_sample_size <= 0:
            raise ValueError("in_sample_size must be positive")
        if out_of_sample_size <= 0:
            raise ValueError("out_of_sample_size must be positive")

        step = step_size if step_size is not None else out_of_sample_size
        if step <= 0:
            raise ValueError("step_size must be positive")

        total_candles = len(candles)
        required_minimum = in_sample_size + out_of_sample_size
        if total_candles < required_minimum:
            return ()

        # Ensure candles are sorted chronologically
        for i in range(1, total_candles):
            if candles[i].close_time_ms <= candles[i - 1].close_time_ms:
                raise ValueError("candles must be sorted strictly ascending by close_time_ms")

        windows: list[WalkForwardWindow] = []
        window_idx = 0
        start = 0

        while start + in_sample_size + out_of_sample_size <= total_candles:
            is_candles = candles[start : start + in_sample_size]
            oos_candles = candles[
                start + in_sample_size : start + in_sample_size + out_of_sample_size
            ]

            is_start_epoch = is_candles[0].open_time_ms // 1000
            is_end_epoch = is_candles[-1].close_time_ms // 1000
            oos_start_epoch = oos_candles[0].open_time_ms // 1000
            oos_end_epoch = oos_candles[-1].close_time_ms // 1000

            window = WalkForwardWindow(
                window_index=window_idx,
                in_sample_start_epoch=is_start_epoch,
                in_sample_end_epoch=is_end_epoch,
                out_of_sample_start_epoch=oos_start_epoch,
                out_of_sample_end_epoch=oos_end_epoch,
                in_sample_candles_count=len(is_candles),
                out_of_sample_candles_count=len(oos_candles),
            )
            windows.append(window)
            window_idx += 1
            start += step

        return tuple(windows)

    @staticmethod
    def evaluate_summary(
        window_metrics: Sequence[StrategyPerformanceMetrics],
        *,
        min_win_rate: Decimal = Decimal("0.50"),
        min_robustness: Decimal = Decimal("0.60"),
    ) -> WalkForwardSummary:
        if not window_metrics:
            return WalkForwardSummary(
                total_windows=0,
                windows_passed=0,
                robustness_score_decimal=Decimal("0.0"),
                window_metrics=(),
                is_approved=False,
            )

        passed = 0
        for m in window_metrics:
            if (
                m.total_trades > 0
                and m.win_rate_decimal >= min_win_rate
                and m.net_profit_minor_units > 0
            ):
                passed += 1

        total = len(window_metrics)
        score = Decimal(passed) / Decimal(total)
        is_approved = score >= min_robustness

        return WalkForwardSummary(
            total_windows=total,
            windows_passed=passed,
            robustness_score_decimal=score,
            window_metrics=tuple(window_metrics),
            is_approved=is_approved,
        )
