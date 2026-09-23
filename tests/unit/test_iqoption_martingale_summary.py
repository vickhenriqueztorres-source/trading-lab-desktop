from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.iqoption_strategy_summary import (
    IqOptionMartingaleStats,
    IqOptionStrategySummaryWidget,
    calculate_martingale_cycle_stats,
)
from packages.protocol.ui_messages import OrderSummary


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    assert isinstance(app, QApplication)
    return app


def _make_order(
    order_id: str,
    symbol: str,
    created_at: datetime,
    state: str = "SETTLED",
    pnl: int | None = None,
    amount: int = 1000,
    broker: str = "IQOPTION",
) -> OrderSummary:
    return OrderSummary(
        order_id=order_id,
        broker=broker,
        symbol=symbol,
        direction="CALL",
        amount_minor_units=amount,
        currency="USD",
        state=state,
        created_at_utc=created_at,
        realized_pnl_minor_units=pnl,
    )


def test_empty_orders() -> None:
    stats = calculate_martingale_cycle_stats(())
    assert isinstance(stats, IqOptionMartingaleStats)
    assert stats.total_cycles == 0
    assert stats.total_wins == 0
    assert stats.losses_g2 == 0
    assert stats.win_rate == 0.0
    assert stats.net_pnl_minor == 0


def test_win_sem_gale() -> None:
    t0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
    o1 = _make_order("o1", "EURUSD-OTC", t0, pnl=850)
    stats = calculate_martingale_cycle_stats([o1])

    assert stats.total_cycles == 1
    assert stats.wins_sem_gale == 1
    assert stats.wins_g1 == 0
    assert stats.wins_g2 == 0
    assert stats.total_wins == 1
    assert stats.losses_g2 == 0
    assert stats.win_rate == 100.0
    assert stats.net_pnl_minor == 850


def test_win_gale_1() -> None:
    t0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=60)
    o1 = _make_order("o1", "EURUSD-OTC", t0, pnl=-1000)
    o2 = _make_order("o2", "EURUSD-OTC", t1, pnl=1700, amount=2000)

    stats = calculate_martingale_cycle_stats([o1, o2])
    assert stats.total_cycles == 1
    assert stats.wins_sem_gale == 0
    assert stats.wins_g1 == 1
    assert stats.wins_g2 == 0
    assert stats.total_wins == 1
    assert stats.losses_g2 == 0
    assert stats.win_rate == 100.0
    assert stats.net_pnl_minor == 700


def test_win_gale_2() -> None:
    t0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=60)
    t2 = t1 + timedelta(seconds=60)
    o1 = _make_order("o1", "EURUSD-OTC", t0, pnl=-1000)
    o2 = _make_order("o2", "EURUSD-OTC", t1, pnl=-2000, amount=2000)
    o3 = _make_order("o3", "EURUSD-OTC", t2, pnl=3400, amount=4000)

    stats = calculate_martingale_cycle_stats([o1, o2, o3])
    assert stats.total_cycles == 1
    assert stats.wins_sem_gale == 0
    assert stats.wins_g1 == 0
    assert stats.wins_g2 == 1
    assert stats.total_wins == 1
    assert stats.losses_g2 == 0
    assert stats.win_rate == 100.0
    assert stats.net_pnl_minor == 400


def test_loss_gale_2_only() -> None:
    """Loss is strictly considered only when Gale 2 fails (full 3-step cycle exhausted)."""
    t0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=60)
    t2 = t1 + timedelta(seconds=60)
    o1 = _make_order("o1", "EURUSD-OTC", t0, pnl=-1000)
    o2 = _make_order("o2", "EURUSD-OTC", t1, pnl=-2000, amount=2000)
    o3 = _make_order("o3", "EURUSD-OTC", t2, pnl=-4000, amount=4000)

    stats = calculate_martingale_cycle_stats([o1, o2, o3])
    assert stats.total_cycles == 1
    assert stats.total_wins == 0
    assert stats.losses_g2 == 1
    assert stats.win_rate == 0.0
    assert stats.net_pnl_minor == -7000


def test_mixed_martingale_cycles() -> None:
    """
    Scenario:
    - Cycle 1: Win sem gale
    - Cycle 2: Win sem gale
    - Cycle 3: Win G1 (Loss, Win)
    - Cycle 4: Win G2 (Loss, Loss, Win)
    - Cycle 5: Loss G2 (Loss, Loss, Loss)
    Total cycles: 5. Wins: 4 (2 G0, 1 G1, 1 G2). Loss: 1 (Loss G2).
    Win rate: 4 / 5 = 80.0%.
    """
    t0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)

    orders = [
        # Cycle 1: G0 win
        _make_order("c1_o1", "EURUSD-OTC", t0, pnl=850),
        # Cycle 2: G0 win (after 5 minutes)
        _make_order("c2_o1", "GBPUSD-OTC", t0 + timedelta(minutes=5), pnl=850),
        # Cycle 3: G1 win
        _make_order("c3_o1", "USDJPY-OTC", t0 + timedelta(minutes=10), pnl=-1000),
        _make_order("c3_o2", "USDJPY-OTC", t0 + timedelta(minutes=11), pnl=1700),
        # Cycle 4: G2 win
        _make_order("c4_o1", "AUDCAD-OTC", t0 + timedelta(minutes=15), pnl=-1000),
        _make_order("c4_o2", "AUDCAD-OTC", t0 + timedelta(minutes=16), pnl=-2000),
        _make_order("c4_o3", "AUDCAD-OTC", t0 + timedelta(minutes=17), pnl=3400),
        # Cycle 5: Loss G2
        _make_order("c5_o1", "NZDUSD-OTC", t0 + timedelta(minutes=20), pnl=-1000),
        _make_order("c5_o2", "NZDUSD-OTC", t0 + timedelta(minutes=21), pnl=-2000),
        _make_order("c5_o3", "NZDUSD-OTC", t0 + timedelta(minutes=22), pnl=-4000),
    ]

    stats = calculate_martingale_cycle_stats(orders)
    assert stats.total_cycles == 5
    assert stats.wins_sem_gale == 2
    assert stats.wins_g1 == 1
    assert stats.wins_g2 == 1
    assert stats.total_wins == 4
    assert stats.losses_g2 == 1
    assert stats.win_rate == 80.0


def test_ignore_non_settled_and_non_iqoption() -> None:
    t0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
    o1 = _make_order("o1", "EURUSD-OTC", t0, pnl=850, state="SETTLED")
    o2 = _make_order("o2", "EURUSD-OTC", t0 + timedelta(seconds=60), pnl=None, state="SUBMITTED")
    o3 = _make_order("o3", "R_100", t0 + timedelta(seconds=120), pnl=950, broker="DERIV")

    stats = calculate_martingale_cycle_stats([o1, o2, o3])
    assert stats.total_cycles == 1
    assert stats.total_wins == 1
    assert stats.wins_sem_gale == 1


def test_widget_rendering() -> None:
    _get_qapp()
    widget = IqOptionStrategySummaryWidget()

    t0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
    orders = (
        _make_order("o1", "EURUSD-OTC", t0, pnl=850),
        _make_order("o2", "EURUSD-OTC", t0 + timedelta(minutes=5), pnl=-1000),
        _make_order("o3", "EURUSD-OTC", t0 + timedelta(minutes=6), pnl=1700),
    )

    widget.update_orders(orders=orders)
    assert widget._gain_val.text() == "2"
    assert "Sem Gale: 1" in widget._gain_sub.text()
    assert "G1: 1" in widget._gain_sub.text()
    assert widget._loss_val.text() == "0"
    assert "100.0%" in widget._win_val.text()
