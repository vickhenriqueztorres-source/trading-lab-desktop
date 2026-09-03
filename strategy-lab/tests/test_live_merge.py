"""Tests for live_merge out-of-sample window and degradation detection (R-RES-12)."""

from __future__ import annotations

from decimal import Decimal

from strategy_lab.research.live_merge import (
    aggregate_live_outcomes,
    merge_live_outcomes,
)


def test_aggregate_live_outcomes_by_strategy_key() -> None:
    """R-RES-12: agrega registros de live_outcomes agrupados por chave de estratégia."""
    raw_records = [
        {"strategy_key": "eurusd_f1", "won": True, "ts": 100},
        {"strategy_key": "eurusd_f1", "won": False, "ts": 160},
        {"strategy_key": "gbpusd_f2", "won": True, "ts": 200},
        {"strategy_key": "eurusd_f1", "won": True, "ts": 220},
    ]

    aggregated = aggregate_live_outcomes(raw_records)
    assert set(aggregated.keys()) == {"eurusd_f1", "gbpusd_f2"}
    assert aggregated["eurusd_f1"] == [True, False, True]
    assert aggregated["gbpusd_f2"] == [True]


def test_live_merge_reduces_p_hat_when_live_is_worse() -> None:
    """R-RES-12: live_outcomes piores que o backtest reduzem p_hat e o limite inferior de Wilson."""
    p_min = Decimal("0.55")

    # Backtest com excelente desempenho: 65% em 500 operações
    bt_n = 500
    bt_wins = 325

    # Live com desempenho degradado: 40% em 200 operações
    live_trades = [True] * 80 + [False] * 120

    merged = merge_live_outcomes(
        strategy_key="eurusd_f1",
        backtest_wins=bt_wins,
        backtest_n=bt_n,
        live_trades=live_trades,
        p_min=p_min,
    )

    p_hat_bt = Decimal(bt_wins) / Decimal(bt_n)  # 0.650
    p_hat_combined = merged["p_hat_combined"]  # (325 + 80) / 700 = 405 / 700 ≈ 0.5786

    assert p_hat_combined < p_hat_bt
    assert merged["combined_n"] == 700
    assert merged["combined_wins"] == 405

    # A janela ao vivo deve ser reprovada no gate
    live_gate = merged["live_gate"]
    assert live_gate.passed is False
    assert "LIVE_WILSON_BELOW_PMIN" in live_gate.reason


def test_live_merge_preserves_edge_when_live_is_healthy() -> None:
    """R-RES-12: live_outcomes consistentes com backtest mantêm aprovação na janela OOS."""
    p_min = Decimal("0.55")
    bt_n = 500
    bt_wins = 310  # 62%

    # Live saudável: 70% em 200 operações (Wilson lower > 0.63 > 0.55)
    live_trades = [True] * 140 + [False] * 60

    merged = merge_live_outcomes(
        strategy_key="eurusd_f1",
        backtest_wins=bt_wins,
        backtest_n=bt_n,
        live_trades=live_trades,
        p_min=p_min,
    )

    live_gate = merged["live_gate"]
    assert live_gate.passed is True
    assert live_gate.metrics["p_hat"] == Decimal("0.70")
    assert merged["p_hat_combined"] > Decimal("0.62")
