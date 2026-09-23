from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.kpi_card import KpiCard, RingGauge
from apps.ui.i18n import I18nManager
from apps.ui.pages.overview_page import OverviewPage
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    UiAccountMode,
    UiDigitRiskConfig,
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


def test_ring_gauge() -> None:
    _get_qapp()
    gauge = RingGauge(percent=75.0)
    assert gauge._percent == 75.0
    gauge.set_value(50.0, "#E5484D")
    assert gauge._percent == 50.0
    assert gauge._color == "#E5484D"

    # Edge cases
    gauge.set_value(-10.0)
    assert gauge._percent == 0.0
    gauge.set_value(150.0)
    assert gauge._percent == 100.0


def test_kpi_card() -> None:
    _get_qapp()
    card = KpiCard(
        title="TOTAL TRADES",
        value="10",
        delta="+2 today",
        show_gauge=True,
        gauge_percent=60.0,
        tooltip="Test tooltip",
    )
    assert card._lbl_title.text() == "TOTAL TRADES"
    assert card._lbl_value.text() == "10"
    assert card._lbl_delta.text() == "+2 today"
    assert card.toolTip() == "Test tooltip"
    assert card._gauge is not None

    card.set_data(
        title="NEW TITLE",
        value="20",
        delta="+5 today",
        gauge_percent=80.0,
        value_color="#1FB57A",
    )
    assert card._lbl_title.text() == "NEW TITLE"
    assert card._lbl_value.text() == "20"
    assert card._gauge._percent == 80.0


def test_overview_page_instantiation_and_projection() -> None:
    _get_qapp()
    I18nManager.set_language("es")
    page = OverviewPage()

    mock_controller = MagicMock()
    mock_controller.connected = True

    # Initial projection with None
    page.update_projection(None, mock_controller)
    assert page._lbl_core_status_val.text() == "CONECTADO"
    assert page._radar_table.isHidden() is True
    assert page._empty_label.isHidden() is False

    # Create full snapshot
    b1 = BrokerCardStatus("DERIV", UiAccountMode.PRACTICE, True, 1000000, "USD", True, "Demo", 40)
    b2 = BrokerCardStatus(
        "IQOPTION", UiAccountMode.PRACTICE, True, 500000, "USD", True, "Practice", 25
    )
    o1 = OrderSummary(
        "ord-1",
        "DERIV",
        "R_100",
        "CALL",
        2500,
        "USD",
        "SETTLED",
        datetime.now(UTC),
        realized_pnl_minor_units=1500,
    )
    o2 = OrderSummary(
        "ord-2",
        "DERIV",
        "R_100",
        "PUT",
        2500,
        "USD",
        "SETTLED",
        datetime.now(UTC),
        realized_pnl_minor_units=-2500,
    )
    rank1 = UiIqOptionAssetRank(
        "EURUSD-OTC", "EUR/USD OTC", "25.4", direction="CALL", condition="OVERSOLD"
    )
    rank2 = UiIqOptionAssetRank(
        "EURUSD", "EUR/USD", "74.1", direction="PUT", condition="OVERBOUGHT"
    )
    rank3 = UiIqOptionAssetRank("GBPUSD", "GBP/USD", "50.0", direction=None, condition="NEUTRAL")

    snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "Operational"),),
        broker_cards=(b1, b2),
        active_orders=(o1, o2),
        daily_pnl_minor_units=4500,
        daily_pnl_currency="USD",
        global_exposure_minor_units=2500,
        global_max_exposure_minor_units=50000,
        consecutive_losses=1,
        risk_state="NORMAL",
        deriv_bot_armed=False,
        iqoption_asset_ranking=(rank1, rank2, rank3),
        digit_risk_config=UiDigitRiskConfig(
            stake_minor_units=100,
            daily_stop_loss_minor_units=5000,
            daily_take_profit_minor_units=3000,
            max_consecutive_losses=3,
            cooldown_seconds_after_loss=30,
            min_quantum_confidence_pct=Decimal("92.5"),
            selected_symbol="R_100",
            active_strategy_id="selective-differs-edge",
        ),
    )

    page.update_projection(snapshot, mock_controller)

    # Hero verification
    assert page._lbl_strategy_name.text() == "Selective Differs Edge"
    assert page._lbl_broker_chip.text() == "Deriv"
    assert "PRÁCTICA" in page._lbl_mode_chip.text()
    assert page._lbl_core_status_val.text() == "CONECTADO"
    assert "10,000.00" in page._lbl_balance_val.text()
    assert page._lbl_bot_status_val.text() == "En espera"

    # KPI verification
    assert page._kpi_trades._lbl_value.text() == "2"
    assert page._kpi_wins._lbl_value.text() == "1"
    assert page._kpi_losses._lbl_value.text() == "1"
    assert "+USD 45.00" in page._kpi_profit._lbl_value.text()

    # Radar verification
    assert page._radar_table.isHidden() is False
    assert page._empty_label.isHidden() is True
    assert page._radar_table.rowCount() == 3

    # Test Radar filtering: OTC only
    page._filter_combo.setCurrentIndex(2)  # OTC
    assert page._radar_table.rowCount() == 1
    assert page._radar_table.item(0, 1).text() == "EUR/USD OTC"

    # Test Radar filtering: Forex only
    page._filter_combo.setCurrentIndex(1)  # Forex
    assert page._radar_table.rowCount() == 2

    # Test search filtering
    page._filter_combo.setCurrentIndex(0)  # All
    page._search_input.setText("GBP")
    assert page._radar_table.rowCount() == 1
    assert page._radar_table.item(0, 1).text() == "GBP/USD"
    page._search_input.setText("")
    assert page._radar_table.rowCount() == 3

    # Primary Action Button
    assert "ENCENDER" in page.primary_action_btn.text()
    assert page.primary_action_btn.isEnabled() is True

    # Test armed bot state
    from dataclasses import replace

    armed_snapshot = replace(snapshot, deriv_bot_armed=True)
    page.update_projection(armed_snapshot, mock_controller)
    assert page._lbl_bot_status_val.text() == "En ejecución"
    assert "DETENER" in page.primary_action_btn.text()

    # Retranslate to English and restore to Spanish
    try:
        I18nManager.set_language("en")
        page.retranslate()
        assert "STOP" in page.primary_action_btn.text()
        assert "TOTAL TRADES" in page._kpi_trades._lbl_title.text()
        assert "Market Radar" in page._radar_title.text()
    finally:
        I18nManager.set_language("es")


def test_overview_page_separated_broker_stats_and_compact_mode() -> None:
    _get_qapp()
    I18nManager.set_language("es")
    page = OverviewPage()

    mock_controller = MagicMock()
    mock_controller.connected = True

    # Check official logos loaded on broker cards
    assert page._lbl_deriv_logo.pixmap() is not None
    assert not page._lbl_deriv_logo.pixmap().isNull()
    assert page._lbl_iq_logo.pixmap() is not None
    assert not page._lbl_iq_logo.pixmap().isNull()

    # Test compact mode scaling
    page.set_compact_mode(True)
    assert page._content_layout.contentsMargins().left() == 12
    assert page._hero.minimumHeight() == 105

    page.set_compact_mode(False)
    assert page._content_layout.contentsMargins().left() == 24
    assert page._hero.minimumHeight() == 125

    # Create broker cards with unlimited operations (> 50 trades)
    b_deriv = BrokerCardStatus(
        "DERIV",
        UiAccountMode.PRACTICE,
        True,
        1500000,
        "USD",
        True,
        "Demo",
        15,
        total_trades=75,
        wins=50,
        losses=25,
        realized_pnl_minor_units=15000,
    )
    b_iq = BrokerCardStatus(
        "IQOPTION",
        UiAccountMode.PRACTICE,
        True,
        2800000,
        "USD",
        True,
        "Practice",
        10,
        total_trades=120,
        wins=80,
        losses=40,
        realized_pnl_minor_units=32000,
    )

    snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "Operational"),),
        broker_cards=(b_deriv, b_iq),
        active_orders=(),
        daily_pnl_minor_units=47000,
        daily_pnl_currency="USD",
        global_exposure_minor_units=0,
        global_max_exposure_minor_units=50000,
        consecutive_losses=0,
        risk_state="NORMAL",
        deriv_bot_armed=False,
    )

    page.update_projection(snapshot, mock_controller)

    # Deriv separated statistics verification
    assert page._lbl_deriv_trades_val.text() == "75"
    assert page._lbl_deriv_wins_val.text() == "50"
    assert page._lbl_deriv_losses_val.text() == "25"
    assert page._lbl_deriv_winrate_val.text() == "66.7%"
    assert "+USD 150.00" in page._lbl_deriv_pnl_val.text()

    # IQ Option separated statistics verification
    assert page._lbl_iq_trades_val.text() == "120"
    assert page._lbl_iq_wins_val.text() == "80"
    assert page._lbl_iq_losses_val.text() == "40"
    assert page._lbl_iq_winrate_val.text() == "66.7%"
    assert "+USD 320.00" in page._lbl_iq_pnl_val.text()

    # Consolidated KPIs: total trades should reflect full sum 75 + 120 = 195 (not limited to 50!)
    assert page._kpi_trades._lbl_value.text() == "195"
    assert page._kpi_wins._lbl_value.text() == "130"
    assert page._kpi_losses._lbl_value.text() == "65"
    assert "+USD 470.00" in page._kpi_profit._lbl_value.text()
