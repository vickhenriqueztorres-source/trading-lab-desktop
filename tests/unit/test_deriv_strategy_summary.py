from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.deriv_strategy_summary import DerivStrategySummaryWidget
from apps.ui.i18n import I18nManager
from packages.protocol import OrderSummary, UiDigitRiskConfig


def _order(order_id: str, pnl: int) -> OrderSummary:
    return OrderSummary(
        order_id,
        "DERIV",
        "R_100",
        "CALL",
        100,
        "USD",
        "SETTLED",
        datetime(2026, 8, 24, tzinfo=UTC),
        realized_pnl_minor_units=pnl,
    )


def test_deriv_summary_shows_gain_loss_counts_and_risk_without_table() -> None:
    app = QApplication.instance() or QApplication([])
    I18nManager.set_language("es")
    widget = DerivStrategySummaryWidget()
    widget.resize(1000, 420)
    config = UiDigitRiskConfig(
        stake_minor_units=100,
        daily_stop_loss_minor_units=5000,
        daily_take_profit_minor_units=3000,
        max_consecutive_losses=3,
        cooldown_seconds_after_loss=30,
        min_quantum_confidence_pct=Decimal("92.5"),
        selected_symbol="R_100",
    )

    widget.update_results((_order("gain-1", 95), _order("gain-2", 95), _order("loss", -100)))
    widget.update_risk(250, 50000, "USD", "NORMAL", 1, config, 0)
    widget.show()
    app.processEvents()

    assert widget._gain[1].text() == "+USD 1.90"
    assert widget._gain[2].text() == "Operaciones: 2"
    assert widget._loss[1].text() == "-USD 1.00"
    assert widget._loss[2].text() == "Operaciones: 1"
    assert widget._net[1].text() == "+USD 0.90"
    assert widget._consecutive[1].text() == "1 / 3"
    assert widget._stop_loss[1].text() == "USD 50.00"
    assert widget._take_profit[1].text() == "USD 30.00"
    assert widget._cooldown[1].text() == "LISTO"
    assert widget._risk_frame.height() >= 205
    assert len(widget._risk_metric_frames) == 6
    assert all(frame.isVisible() and frame.height() >= 50 for frame in widget._risk_metric_frames)
    widget.close()
