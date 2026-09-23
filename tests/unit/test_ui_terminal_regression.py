from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.terminal_button import TerminalButton
from apps.ui.i18n import I18nManager
from apps.ui.pages.overview_page import OverviewPage
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    UiAccountMode,
    UiGlobalState,
    UiIqOptionAssetRank,
    UiProjectionSnapshot,
)


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    assert isinstance(app, QApplication)
    return app


def test_terminal_button_debounce_and_busy() -> None:
    _get_qapp()
    btn = TerminalButton(text="Ejecutar", variant="primary", debounce_ms=300)
    clicks = []
    btn.debounced_clicked.connect(lambda: clicks.append(1))

    # Click once -> should emit
    btn._handle_clicked()
    assert len(clicks) == 1

    # Immediate second click -> debounced, should NOT emit
    btn._handle_clicked()
    assert len(clicks) == 1

    # Busy state
    btn.set_busy(True, "Procesando...")
    assert btn.isEnabled() is False
    assert btn.text() == "Procesando..."

    btn.set_busy(False)
    assert btn.isEnabled() is True
    assert btn.text() == "Ejecutar"


def test_signal_hygiene_no_false_call_put_on_rsi() -> None:
    _get_qapp()
    I18nManager.set_language("es")
    page = OverviewPage()
    mock_ctrl = MagicMock()
    mock_ctrl.connected = True

    # Asset with oversold RSI (22.5) but NO strategy direction
    item_oversold = UiIqOptionAssetRank(
        symbol="EURUSD",
        display_name="EUR/USD",
        rsi="22.5",
        direction=None,
        condition="OVERSOLD",
    )
    # Asset with overbought RSI (78.0) but NO strategy direction
    item_overbought = UiIqOptionAssetRank(
        symbol="GBPUSD",
        display_name="GBP/USD",
        rsi="78.0",
        direction=None,
        condition="OVERBOUGHT",
    )
    # Asset with validated strategy CALL signal
    item_call = UiIqOptionAssetRank(
        symbol="USDJPY",
        display_name="USD/JPY",
        rsi="28.0",
        direction="CALL",
        condition="OVERSOLD",
        status="TRIGGERED",
    )

    snap = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "OK"),),
        broker_cards=(
            BrokerCardStatus("DERIV", UiAccountMode.PRACTICE, True, 100000, "USD", True),
            BrokerCardStatus("IQOPTION", UiAccountMode.PRACTICE, True, 50000, "USD", True),
        ),
        active_orders=(),
        daily_pnl_minor_units=0,
        daily_pnl_currency=None,
        iqoption_asset_ranking=(item_oversold, item_overbought, item_call),
    )

    page.update_projection(snap, mock_ctrl)

    # In overview radar table:
    # Row 0 (EURUSD, RSI 22.5): Signal MUST NOT be CALL, must be NEUTRAL
    col_sig_0 = page._radar_table.item(0, 4).text()
    assert "CALL" not in col_sig_0
    assert col_sig_0 == "NEUTRAL"

    # Row 1 (GBPUSD, RSI 78.0): Signal MUST NOT be PUT, must be NEUTRAL
    col_sig_1 = page._radar_table.item(1, 4).text()
    assert "PUT" not in col_sig_1
    assert col_sig_1 == "NEUTRAL"

    # Row 2 (USDJPY, direction=CALL): Signal MUST be CALL
    col_sig_2 = page._radar_table.item(2, 4).text()
    assert "CALL" in col_sig_2


def test_dual_broker_overview_cards_and_orders() -> None:
    _get_qapp()
    I18nManager.set_language("es")
    page = OverviewPage()
    mock_ctrl = MagicMock()
    mock_ctrl.connected = True

    b_deriv = BrokerCardStatus("DERIV", UiAccountMode.PRACTICE, True, 1250000, "USD", True)
    b_iq = BrokerCardStatus("IQOPTION", UiAccountMode.REAL, True, 450000, "USD", True)

    ord_open = OrderSummary(
        order_id="ord-test-1",
        broker="DERIV",
        symbol="R_100",
        direction="CALL",
        amount_minor_units=2000,
        currency="USD",
        state="OPEN",
        created_at_utc=datetime.now(UTC),
    )

    snap = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "OK"),),
        broker_cards=(b_deriv, b_iq),
        active_orders=(ord_open,),
        daily_pnl_minor_units=1500,
        daily_pnl_currency="USD",
        deriv_bot_armed=True,
        iqoption_bot_armed=False,
    )

    page.update_projection(snap, mock_ctrl)

    # Dual cards verification
    assert page._lbl_deriv_conn_badge.text() == "CONECTADO"
    assert "12,500.00" in page._lbl_deriv_bal_val.text()
    assert page._lbl_deriv_op_status.text() == "Bot armado · Esperando señal"

    assert page._lbl_iq_conn_badge.text() == "CONECTADO"
    assert "4,500.00" in page._lbl_iq_bal_val.text()
    assert page._lbl_iq_op_status.text() == "Conectado · Bot apagado"

    # Active orders table
    assert page._table_active_orders.isHidden() is False
    assert page._table_active_orders.rowCount() == 1
    assert page._table_active_orders.item(0, 0).text() == "DERIV"
    assert page._table_active_orders.item(0, 1).text() == "R_100"
    assert page._table_active_orders.item(0, 2).text() == "CALL"
