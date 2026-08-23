from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

# Ensure offscreen Qt platform before importing PySide6
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from apps.ui.app import TradingLabMainWindow
from apps.ui.i18n import I18nManager
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    UiAccountMode,
    UiGlobalState,
    UiProjectionSnapshot,
)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_trading_lab_main_window_headless(qapp: QApplication) -> None:
    mock_controller = MagicMock()
    mock_controller.connected = True

    b1 = BrokerCardStatus("DERIV", UiAccountMode.PRACTICE, True, 1000000, "USD", True, "Demo", 40)
    b2 = BrokerCardStatus(
        "IQ_OPTION", UiAccountMode.PRACTICE, True, 500000, "USD", True, "Practice", 25
    )
    o1 = OrderSummary(
        "ord-1234567890", "DERIV", "R_100", "CALL", 2500, "USD", "OPEN", datetime.now(UTC)
    )
    o2 = OrderSummary(
        "ord-iq-1234567890",
        "IQ_OPTION",
        "EURUSD",
        "PUT",
        1750,
        "USD",
        "UNKNOWN",
        datetime.now(UTC),
    )

    snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(
            HealthGateStatus("HG_GLOBAL", True, None, "Operational"),
            HealthGateStatus("HG_DERIV", True, None, "Deriv Ready"),
        ),
        broker_cards=(b1, b2),
        active_orders=(o1, o2),
        daily_pnl_minor_units=4500,
        daily_pnl_currency="USD",
        global_exposure_minor_units=2500,
        global_max_exposure_minor_units=50000,
        consecutive_losses=1,
        risk_state="NORMAL",
    )
    mock_controller.snapshot = snapshot

    window = TradingLabMainWindow(mock_controller)
    window.show()

    # Verify widget updates
    window._refresh_projection()

    assert window._lbl_ipc_status.text() != ""
    assert window._lbl_pnl_val.text() == "+USD 45.00"
    assert window._btn_safe_stop.isEnabled() is True
    assert window._btn_resume.isEnabled() is False
    assert window._main_tabs.count() == 5
    assert window._main_tabs.tabText(window._TAB_DERIV) == "Deriv — PRÁCTICA"
    assert window._main_tabs.tabText(window._TAB_IQ_OPTION) == "IQ Option — PRÁCTICA"
    assert window._deriv_workspace.tabs.count() == 2
    assert window._iqoption_workspace.tabs.count() == 2
    assert window._deriv_workspace.orders.order_count == 1
    assert window._iqoption_workspace.orders.order_count == 1
    assert window._order_table_widget.order_count == 2
    assert window._order_table_widget._table.item(1, 5).text() == "UNKNOWN"
    assert window._settings_workspace.tabs.count() == 4
    assert "modo real" in window._deriv_workspace._real_mode_notice.text().lower()

    # Simulate Safe Stop click
    window._btn_safe_stop.click()
    mock_controller.safe_stop.assert_called_once()

    # Simulate Language change
    I18nManager.set_language("en")
    assert window._lbl_badge.text() == "PRACTICE MODE"
    assert window._main_tabs.tabText(window._TAB_DERIV) == "Deriv — PRACTICE"

    I18nManager.set_language("es")
    assert window._lbl_badge.text() == "MODO PRÁCTICA"

    window.close()
