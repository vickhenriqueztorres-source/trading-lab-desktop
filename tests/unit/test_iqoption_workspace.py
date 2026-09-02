from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

from apps.ui.components.iqoption_strategy_panel import IqOptionStrategyConfigWidget
from apps.ui.components.workspaces import BrokerWorkspaceWidget
from packages.protocol import BrokerCardStatus, UiAccountMode


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


def test_iqoption_rsi_and_risk_controls_are_visible() -> None:
    application = QApplication.instance() or QApplication([])
    panel = IqOptionStrategyConfigWidget()

    buttons = {button.text() for button in panel.findChildren(QPushButton)}
    combo_text = {combo.currentText() for combo in panel.findChildren(QComboBox)}

    assert any("RSI" in item for item in combo_text)
    assert any("IQ Option" in item for item in buttons)
    assert application is not None
