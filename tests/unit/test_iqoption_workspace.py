from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

from apps.ui.components.iqoption_strategy_panel import IqOptionStrategyConfigWidget
from apps.ui.components.iqoption_workspace import IqOptionWorkspaceWidget
from apps.ui.components.workspaces import BrokerWorkspaceWidget
from packages.protocol import BrokerCardStatus, UiAccountMode, UiBalanceQuality


def test_iqoption_workspace_exposes_protected_practice_access() -> None:
    application = QApplication.instance() or QApplication([])
    workspace = BrokerWorkspaceWidget(
        "IQOPTION",
        "IQ Option",
        "broker.iq_option.intro",
        "config.iq_option.body",
    )
    emitted: list[bool] = []
    workspace.iqoption_login_requested.connect(lambda: emitted.append(True))

    buttons = workspace.findChildren(QPushButton)
    login_button = next(button for button in buttons if "IQ Option" in button.text())
    login_button.click()

    assert emitted == [True]
    assert application is not None


def test_iqoption_workspace_renders_connected_balance_projection() -> None:
    application = QApplication.instance() or QApplication([])
    workspace = BrokerWorkspaceWidget(
        "IQOPTION",
        "IQ Option",
        "broker.iq_option.intro",
        "config.iq_option.body",
    )

    workspace.update_status(
        BrokerCardStatus(
            broker="IQOPTION",
            account_mode=UiAccountMode.PRACTICE,
            is_connected=True,
            balance_minor_units=987_096,
            currency="USD",
            clock_synced=True,
            connection_label="PRACTICE LIVE",
            clock_latency_ms=172,
        )
    )

    visible_text = {label.text() for label in workspace.findChildren(QLabel)}
    assert "USD 9,870.96" in visible_text
    assert application is not None


def test_iqoption_workspace_distinguishes_retrying_from_stale_balance() -> None:
    application = QApplication.instance() or QApplication([])
    workspace = IqOptionWorkspaceWidget()
    observed = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    workspace.update_status(
        BrokerCardStatus(
            broker="IQOPTION",
            account_mode=UiAccountMode.PRACTICE,
            is_connected=True,
            balance_minor_units=54_975,
            currency="USD",
            clock_synced=True,
            balance_observed_at_utc=observed,
            balance_is_fresh=True,
            balance_quality=UiBalanceQuality.RETRYING,
            balance_age_seconds=5,
            balance_retry_count=1,
        )
    )

    assert workspace._balance_value.text() == "USD 549.75"
    assert "ÚLTIMO SALDO CONFIRMADO há 5s" in workspace._balance_freshness.text()
    assert "tentativa 1" in workspace._balance_freshness.text()
    assert application is not None


def test_iqoption_rsi_and_risk_controls_are_visible() -> None:
    application = QApplication.instance() or QApplication([])
    panel = IqOptionStrategyConfigWidget()

    buttons = {button.text() for button in panel.findChildren(QPushButton)}
    combo_text = {combo.currentText() for combo in panel.findChildren(QComboBox)}

    assert any("RSI" in item for item in combo_text)
    assert any("IQ Option" in item for item in buttons)
    assert application is not None


def test_armed_transport_recovery_and_countdown_are_inline() -> None:
    application = QApplication.instance() or QApplication([])
    workspace = IqOptionWorkspaceWidget()

    workspace.update_bot_state(True, "TRANSPORT_DOWN", entry_ready=False)
    workspace.set_iqoption_reconnect_wait(272, 2)

    assert workspace._automation_pill.text() == "● BOT ARMADO · RECONECTANDO"
    assert "04:32" in workspace._iqoption_login_status.text()
    assert "tentativas 2/3" in workspace._iqoption_login_status.text()
    assert workspace._iqoption_login_button.text() == "↻ Reconectar agora"
    workspace._reconnect_timer.stop()
    assert application is not None
