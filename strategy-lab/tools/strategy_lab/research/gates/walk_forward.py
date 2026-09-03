"""Anchored walk-forward windows and stability gates (R-RES-7)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, getcontext

from strategy_lab.research.replay_simulator import Trade

getcontext().prec = 28

SIX_MONTHS_S = 6 * 30 * 86400  # 180 days
TWO_MONTHS_S = 2 * 30 * 86400  # 60 days
MAX_STABILITY_STD_PP = Decimal("0.03")  # 3 percentage points


@dataclass(frozen=True)
class WalkForwardWindow:
    index: int
    train_from_ts: int
    train_to_ts: int
    test_from_ts: int
    test_to_ts: int
    trades: tuple[Trade, ...]
    wins: int
    total: int
    p_hat: Decimal


@dataclass(frozen=True)
class StabilityResult:
    passed: bool
    window_p_hats: tuple[Decimal, ...]
    mean_p_hat: Decimal
    std_p_hat: Decimal
    min_p_hat: Decimal
    p_min: Decimal
    reason: str = ""


def generate_anchored_slices(
    from_ts: int,
    to_ts: int,
    *,
    train_duration_s: int = SIX_MONTHS_S,
    test_duration_s: int = TWO_MONTHS_S,
) -> list[tuple[int, int, int, int]]:
    """Return (train_from, train_to, test_from, test_to) tuples for anchored walk-forward."""
    slices: list[tuple[int, int, int, int]] = []
    current_test_from = from_ts + train_duration_s
    while current_test_from + test_duration_s <= to_ts:
        train_from = from_ts
        train_to = current_test_from
        test_from = current_test_from
        test_to = current_test_from + test_duration_s
        slices.append((train_from, train_to, test_from, test_to))
        current_test_from += test_duration_s
    return slices


def partition_trades_into_windows(
    trades: Sequence[Trade],
    slices: Sequence[tuple[int, int, int, int]],
) -> list[WalkForwardWindow]:
    """Partition trades into walk-forward test slices and calculate out-of-sample p_hat."""
    windows: list[WalkForwardWindow] = []
    for index, (tr_from, tr_to, te_from, te_to) in enumerate(slices):
        test_trades = tuple(t for t in trades if te_from <= t.ts <= te_to)
        wins = sum(1 for t in test_trades if t.won)
        total = len(test_trades)
        p_hat = Decimal(wins) / Decimal(total) if total > 0 else Decimal("0")
        windows.append(
            WalkForwardWindow(
                index=index,
                train_from_ts=tr_from,
                train_to_ts=tr_to,
                test_from_ts=te_from,
                test_to_ts=te_to,
                trades=test_trades,
                wins=wins,
                total=total,
                p_hat=p_hat,
            )
        )
    return windows


def evaluate_stability(
    window_p_hats: Sequence[Decimal],
    p_min: Decimal,
    *,
    max_std: Decimal = MAX_STABILITY_STD_PP,
) -> StabilityResult:
    """Evaluate stability: no test window < p_min and std between windows < 3 pp."""
    if not window_p_hats:
        return StabilityResult(
            passed=False,
            window_p_hats=(),
            mean_p_hat=Decimal("0"),
            std_p_hat=Decimal("0"),
            min_p_hat=Decimal("0"),
            p_min=p_min,
            reason="NO_WINDOWS",
        )

    p_hats_tuple = tuple(window_p_hats)
    min_p_hat = min(p_hats_tuple)
    count = Decimal(len(p_hats_tuple))
    mean_p_hat = sum(p_hats_tuple, Decimal("0")) / count

    # Sample standard deviation (or 0 if single window)
    if len(p_hats_tuple) > 1:
        variance = sum(((p - mean_p_hat) ** 2 for p in p_hats_tuple), Decimal("0")) / (
            count - Decimal("1")
        )
        std_p_hat = variance.sqrt()
    else:
        std_p_hat = Decimal("0")

    if min_p_hat < p_min:
        return StabilityResult(
            passed=False,
            window_p_hats=p_hats_tuple,
            mean_p_hat=mean_p_hat,
            std_p_hat=std_p_hat,
            min_p_hat=min_p_hat,
            p_min=p_min,
            reason="WINDOW_BELOW_PMIN",
        )

    if std_p_hat >= max_std:
        return StabilityResult(
            passed=False,
            window_p_hats=p_hats_tuple,
            mean_p_hat=mean_p_hat,
            std_p_hat=std_p_hat,
            min_p_hat=min_p_hat,
            p_min=p_min,
            reason="STD_EXCEEDS_3PP",
        )

    return StabilityResult(
        passed=True,
        window_p_hats=p_hats_tuple,
        mean_p_hat=mean_p_hat,
        std_p_hat=std_p_hat,
        min_p_hat=min_p_hat,
        p_min=p_min,
        reason="",
    )
