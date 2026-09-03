"""Aggregate live outcomes as an extra out-of-sample window in walk-forward evaluation.
R-RES-12.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from strategy_lab.research.gates.pipeline import GateResult
from strategy_lab.research.gates.wilson import wilson_lower


def aggregate_live_outcomes(
    records: Sequence[dict[str, Any] | Any],
) -> dict[str, list[bool]]:
    """Group anonymous live outcomes by strategy_key."""
    aggregated: dict[str, list[bool]] = {}
    for rec in records:
        if isinstance(rec, dict):
            key = str(rec.get("strategy_key", ""))
            won = bool(rec.get("won", False))
        else:
            key = str(getattr(rec, "strategy_key", ""))
            won = bool(getattr(rec, "won", False))

        if not key:
            continue
        if key not in aggregated:
            aggregated[key] = []
        aggregated[key].append(won)

    return aggregated


def compute_live_window_result(
    strategy_key: str,
    live_trades: Sequence[bool],
    p_min: Decimal,
) -> GateResult:
    """Evaluate live trades as an independent out-of-sample window."""
    if not live_trades:
        return GateResult(
            gate_name="live_out_of_sample",
            passed=True,
            metrics={"n": 0, "p_hat": Decimal("0.0"), "wilson_lower": Decimal("0.0")},
            reason="NO_LIVE_DATA",
        )

    n = len(live_trades)
    wins = sum(1 for w in live_trades if w)
    p_hat = Decimal(wins) / Decimal(n)
    wl = wilson_lower(wins, n)
    passed = wl >= p_min

    return GateResult(
        gate_name="live_out_of_sample",
        passed=passed,
        metrics={
            "n": n,
            "wins": wins,
            "p_hat": p_hat,
            "wilson_lower": wl,
            "p_min": p_min,
        },
        reason="" if passed else f"LIVE_WILSON_BELOW_PMIN({wl:.4f}<{p_min:.4f})",
    )


def merge_live_outcomes(
    strategy_key: str,
    backtest_wins: int,
    backtest_n: int,
    live_trades: Sequence[bool],
    p_min: Decimal,
) -> dict[str, Any]:
    """Combine backtest performance with live outcomes, reducing p_hat if live is worse."""
    live_n = len(live_trades)
    live_wins = sum(1 for w in live_trades if w)

    total_n = backtest_n + live_n
    total_wins = backtest_wins + live_wins

    p_hat_combined = Decimal(total_wins) / Decimal(total_n) if total_n > 0 else Decimal("0.0")
    wl_combined = wilson_lower(total_wins, total_n) if total_n > 0 else Decimal("0.0")

    live_gate = compute_live_window_result(strategy_key, live_trades, p_min)

    return {
        "combined_n": total_n,
        "combined_wins": total_wins,
        "p_hat_combined": p_hat_combined,
        "wilson_lower_combined": wl_combined,
        "live_gate": live_gate,
    }
