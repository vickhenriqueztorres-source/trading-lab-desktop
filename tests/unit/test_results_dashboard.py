from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.results_dashboard import ResultsDashboardWidget
from apps.ui.i18n import I18nManager
from packages.protocol import OrderSummary


def _order(order_id: str, pnl: int, *, currency: str = "USD") -> OrderSummary:
    return OrderSummary(
        order_id=order_id,
        broker="DERIV",
        symbol="R_100",
        direction="CALL",
        amount_minor_units=100,
        currency=currency,
        state="SETTLED",
        created_at_utc=datetime(2026, 8, 24, 1, 2, 3, tzinfo=UTC),
        realized_pnl_minor_units=pnl,
    )


def test_results_dashboard_counts_only_confirmed_settlements() -> None:
    app = QApplication.instance() or QApplication([])
    I18nManager.set_language("es")
    widget = ResultsDashboardWidget()
    open_order = OrderSummary(
        "open",
        "DERIV",
        "R_100",
        "CALL",
        100,
        "USD",
        "OPEN",
        datetime(2026, 8, 24, tzinfo=UTC),
    )

    widget.update_results((_order("win", 95), _order("loss", -100), open_order))
    widget.show()
    app.processEvents()

    assert widget._total[1].text() == "2"
    assert widget._wins[1].text() == "1"
    assert widget._losses[1].text() == "1"
    assert widget._win_rate[1].text() == "50.0%"
    assert widget._net[1].text() == "-USD 0.05"
    assert widget._table.rowCount() == 2
    widget.close()


def test_results_dashboard_does_not_sum_mixed_currencies() -> None:
    app = QApplication.instance() or QApplication([])
    widget = ResultsDashboardWidget()

    widget.update_results((_order("usd", 100), _order("eur", 100, currency="EUR")))
    app.processEvents()

    assert widget._net[1].text() == "MIXTO"
