from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Ensure offscreen Qt platform before importing PySide6
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QScrollArea

from apps.ui.app import APP_VERSION, TradingLabMainWindow
from apps.ui.i18n import I18nManager, t
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    UiAccountMode,
    UiCommandAck,
    UiDigitRiskConfigStatus,
    UiGlobalState,
    UiIqOptionLoginAck,
    UiProjectionSnapshot,
    UiUpdateDigitRiskConfigAck,
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
        "IQOPTION", UiAccountMode.PRACTICE, True, 500000, "USD", True, "Practice", 25
    )
    o1 = OrderSummary(
        "ord-1234567890", "DERIV", "R_100", "CALL", 2500, "USD", "OPEN", datetime.now(UTC)
    )
    o2 = OrderSummary(
        "ord-iq-1234567890",
        "IQOPTION",
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
        deriv_bot_armed=True,
    )
    mock_controller.snapshot = snapshot

    window = TradingLabMainWindow(mock_controller)
    window.show()

    # Verify widget updates
    window._refresh_projection()

    assert window._lbl_ipc_status.text() != ""
    assert window._lbl_version.text() == f"v{APP_VERSION}  ·  DIGIT EDGE"
    assert f"v{APP_VERSION}" in window.windowTitle()
    assert window._lbl_pnl_val.text() == "+USD 45.00"
    assert window._btn_bot.isEnabled() is True
    assert "BOT" in window._btn_bot.text()
    assert window._main_tabs.count() == 5
    assert window._main_tabs.tabText(window._TAB_DERIV) == "Deriv — PRÁCTICA"
    assert window._main_tabs.tabText(window._TAB_IQ_OPTION) == "IQ Option — PRÁCTICA"
    assert window._deriv_workspace.tabs.count() == 4
    assert not isinstance(window._deriv_workspace.tabs.widget(0), QScrollArea)
    assert not isinstance(window._deriv_workspace.tabs.widget(1), QScrollArea)
    assert isinstance(window._deriv_workspace.tabs.widget(2), QScrollArea)
    assert window._deriv_workspace._deriv_connect_button is not None
    assert not window._deriv_workspace._deriv_connect_button.isHidden()
    assert window._iqoption_workspace.tabs.count() == 2
    assert window._deriv_workspace.orders.order_count == 1
    assert window._iqoption_workspace.orders.order_count == 1
    assert window._order_table_widget.order_count == 2
    assert window._order_table_widget._table.item(1, 5).text() == "UNKNOWN"
    assert window._settings_workspace.tabs.count() == 4
    assert "modo real" in window._deriv_workspace._real_mode_notice.text().lower()

    # One bot toggle turns entries off when the Core currently projects them as enabled.
    window._btn_bot.click()
    mock_controller.safe_stop.assert_called_once()

    mock_controller.snapshot = replace(
        snapshot,
        global_state=UiGlobalState.SAFE_STOPPED,
        safe_stop_active=True,
        deriv_bot_armed=False,
    )
    window._refresh_projection()
    assert "ENCENDER" in window._btn_bot.text()
    window._btn_bot.click()
    mock_controller.resume.assert_called_once()

    # Simulate Language change
    I18nManager.set_language("en")
    assert window._lbl_badge.text() == "PRACTICE MODE"
    assert window._main_tabs.tabText(window._TAB_DERIV) == "Deriv — PRACTICE"

    I18nManager.set_language("es")
    assert window._lbl_badge.text() == "MODO PRÁCTICA"

    window.close()


def test_saved_iqoption_practice_login_runs_without_password_dialog(qapp: QApplication) -> None:
    mock_controller = MagicMock()
    mock_controller.connected = True
    mock_controller.login_iqoption.return_value = UiIqOptionLoginAck(
        True,
        True,
        "IQOPTION_PRACTICE_CONNECTED",
    )
    mock_controller.snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.SAFE_STOPPED,
        safe_stop_active=True,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "Operational"),),
        broker_cards=(
            BrokerCardStatus(
                "IQOPTION",
                UiAccountMode.PRACTICE,
                False,
                None,
                None,
                False,
                "DESCONECTADO",
            ),
        ),
        active_orders=(),
        daily_pnl_minor_units=0,
        daily_pnl_currency=None,
        global_exposure_minor_units=0,
        global_max_exposure_minor_units=0,
        consecutive_losses=0,
        risk_state="NORMAL",
    )
    window = TradingLabMainWindow(mock_controller)

    window._start_iqoption_saved_login()
    deadline = time.monotonic() + 2.0
    while not mock_controller.login_iqoption.called and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    while "reconectada" not in window._iqoption_workspace._iqoption_login_status.text().lower():
        if time.monotonic() >= deadline:
            break
        qapp.processEvents()
        time.sleep(0.01)

    mock_controller.login_iqoption.assert_called_once_with("saved")
    assert "reconectada" in window._iqoption_workspace._iqoption_login_status.text().lower()
    window.close()


def test_pending_iqoption_order_leaves_saved_recovery_to_core(qapp: QApplication) -> None:
    mock_controller = MagicMock()
    mock_controller.connected = True
    mock_controller.snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.SAFE_STOPPED,
        safe_stop_active=True,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "Operational"),),
        broker_cards=(
            BrokerCardStatus(
                "IQOPTION",
                UiAccountMode.PRACTICE,
                False,
                None,
                None,
                False,
                "DESCONECTADO",
            ),
        ),
        active_orders=(
            OrderSummary(
                "iq-pending",
                "IQOPTION",
                "EURUSD-OTC",
                "PUT",
                100,
                "USD",
                "ACCEPTED",
                datetime.now(UTC),
                "broker-123",
            ),
        ),
        daily_pnl_minor_units=0,
        daily_pnl_currency=None,
        global_exposure_minor_units=100,
        global_max_exposure_minor_units=100,
        consecutive_losses=0,
        risk_state="RECONCILING",
    )
    window = TradingLabMainWindow(mock_controller)

    window._start_iqoption_saved_login()
    qapp.processEvents()

    mock_controller.login_iqoption.assert_not_called()
    window.close()


def test_manual_iqoption_login_keeps_ui_responsive_during_network_wait(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    mock_controller = MagicMock()
    mock_controller.connected = True
    mock_controller.snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.SAFE_STOPPED,
        safe_stop_active=True,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "Operational"),),
        broker_cards=(
            BrokerCardStatus(
                "IQOPTION",
                UiAccountMode.PRACTICE,
                True,
                1000000,
                "USD",
                True,
                "Practice",
            ),
        ),
        active_orders=(),
        daily_pnl_minor_units=0,
        daily_pnl_currency="USD",
        global_exposure_minor_units=0,
        global_max_exposure_minor_units=0,
        consecutive_losses=0,
        risk_state="NORMAL",
    )

    def wait_for_network(_account_mode: str) -> UiIqOptionLoginAck:
        started.set()
        assert release.wait(2.0)
        return UiIqOptionLoginAck(False, False, "IQOPTION_NETWORK_UNREACHABLE")

    mock_controller.login_iqoption.side_effect = wait_for_network
    monkeypatch.setattr(
        "apps.ui.app.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"status":"saved","account_mode":"practice"}\n',
        ),
    )
    warning = MagicMock()
    monkeypatch.setattr("apps.ui.app.QMessageBox.warning", warning)

    window = TradingLabMainWindow(mock_controller)
    before = time.monotonic()
    window._on_iqoption_login()

    assert time.monotonic() - before < 0.5
    assert started.wait(1.0)
    assert window._iqoption_workspace._iqoption_login_button.isEnabled() is False

    release.set()
    deadline = time.monotonic() + 2.0
    while not warning.called and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert warning.called
    assert window._iqoption_workspace._iqoption_login_button.isEnabled() is True
    assert "botão funcionou" in window._iqoption_workspace._iqoption_login_status.text().lower()
    window.close()


def test_iqoption_bot_button_does_not_arm_or_stop_deriv(qapp: QApplication) -> None:
    mock_controller = MagicMock()
    mock_controller.connected = True
    mock_controller.control_iqoption_bot.return_value = UiCommandAck(
        True,
        "IQOPTION_BOT_DISARMED",
        False,
    )
    mock_controller.snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "Operational"),),
        broker_cards=(
            BrokerCardStatus(
                "DERIV",
                UiAccountMode.PRACTICE,
                True,
                100000,
                "USD",
                True,
                "Demo",
            ),
            BrokerCardStatus(
                "IQOPTION",
                UiAccountMode.PRACTICE,
                True,
                100000,
                "USD",
                True,
                "Practice",
            ),
        ),
        active_orders=(),
        daily_pnl_minor_units=0,
        daily_pnl_currency="USD",
        deriv_bot_armed=False,
        iqoption_bot_armed=True,
        iqoption_bot_reason="IQOPTION_BOT_ARMED",
    )

    window = TradingLabMainWindow(mock_controller)
    window._refresh_projection()

    assert window._bot_enabled is False
    assert window._iqoption_bot_enabled is True
    assert window._btn_deriv_bot.text() == t("btn.bot.deriv.start")
    assert window._btn_iqoption_bot.text() == t("btn.bot.iq.stop")

    window._btn_iqoption_bot.click()

    mock_controller.control_iqoption_bot.assert_called_once_with(False)
    mock_controller.resume.assert_not_called()
    mock_controller.safe_stop.assert_not_called()
    window.close()


def test_applying_digit_risk_config_disarms_running_bot_first(qapp: QApplication) -> None:
    mock_controller = MagicMock()
    mock_controller.connected = True
    mock_controller.update_digit_risk_config.return_value = UiUpdateDigitRiskConfigAck(
        UiDigitRiskConfigStatus.OK,
        None,
    )
    mock_controller.snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(HealthGateStatus("HG_GLOBAL", True, None, "Operational"),),
        broker_cards=(
            BrokerCardStatus(
                "DERIV",
                UiAccountMode.PRACTICE,
                True,
                1000000,
                "USD",
                True,
                "Demo",
                0,
            ),
        ),
        active_orders=(),
        daily_pnl_minor_units=0,
        daily_pnl_currency="USD",
        global_exposure_minor_units=0,
        global_max_exposure_minor_units=50000,
        consecutive_losses=0,
        risk_state="NORMAL",
    )
    window = TradingLabMainWindow(mock_controller)
    window._bot_enabled = True
    window._synthetic_config_panel.set_apply_result = MagicMock()  # type: ignore[method-assign]

    config = MagicMock()
    window._on_digit_risk_config_apply(config)

    mock_controller.safe_stop.assert_called_once()
    mock_controller.update_digit_risk_config.assert_called_once_with(config)
    window.close()
