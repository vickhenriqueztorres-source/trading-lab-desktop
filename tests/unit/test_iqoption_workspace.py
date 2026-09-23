from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton

from apps.ui.components.iqoption_strategy_panel import IqOptionStrategyConfigWidget
from apps.ui.components.iqoption_workspace import IqOptionWorkspaceWidget
from apps.ui.components.workspaces import BrokerWorkspaceWidget
from packages.protocol import (
    BrokerCardStatus,
    UiAccountMode,
    UiBalanceQuality,
    UiIqOptionRiskConfig,
)


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
    assert "ÚLTIMO SALDO CONFIRMADO" in workspace._balance_freshness.text()
    freshness_text = workspace._balance_freshness.text()
    assert "#1" in freshness_text or "tentativa 1" in freshness_text
    assert application is not None


def test_iqoption_rsi_and_risk_controls_are_visible() -> None:
    application = QApplication.instance() or QApplication([])
    panel = IqOptionStrategyConfigWidget()

    buttons = {button.text() for button in panel.findChildren(QPushButton)}
    combo_text = {combo.currentText() for combo in panel.findChildren(QComboBox)}

    assert any(
        term in item
        for item in combo_text
        for term in (
            "Hack Chino",
            "Liquidity Gap",
            "Pattern Reversal",
            "Microtrend Scalper",
            "AUTO",
        )
    )
    assert any("IQ Option" in item for item in buttons)
    assert application is not None


def test_iqoption_strategy_summary_displays_selected_bot() -> None:
    application = QApplication.instance() or QApplication([])
    workspace = IqOptionWorkspaceWidget()

    # Default / Pattern Reversal
    workspace.update_iqoption_risk(
        UiIqOptionRiskConfig(strategy_id="iqoption-pattern-reversal", symbol="AUTO")
    )
    assert "Pattern Reversal" in workspace.strategy_summary._info_title.text()
    assert "Pattern Reversal" in workspace.strategy_summary._mode_pill.text()
    assert "Pattern Reversal" in workspace._automation_detail.text()

    # Liquidity Gap
    workspace.update_iqoption_risk(
        UiIqOptionRiskConfig(strategy_id="iqoption-liquidity-gap", symbol="AUTO")
    )
    assert "Liquidity Gap" in workspace.strategy_summary._info_title.text()
    assert "Liquidity Gap" in workspace.strategy_summary._mode_pill.text()
    assert "Liquidity Gap" in workspace._automation_detail.text()

    # Microtrend Scalper
    workspace.update_iqoption_risk(
        UiIqOptionRiskConfig(strategy_id="iqoption-microtrend-scalper", symbol="EURUSD-OTC")
    )
    assert "Microtrend Scalper" in workspace.strategy_summary._info_title.text()
    assert "Microtrend Scalper" in workspace.strategy_summary._mode_pill.text()
    assert "EURUSD-OTC" in workspace._automation_detail.text()

    # AUTO
    workspace.update_iqoption_risk(UiIqOptionRiskConfig(strategy_id="AUTO", symbol="AUTO"))
    assert "Radar Multi-Ativos" in workspace.strategy_summary._info_title.text()
    assert "RADAR MULTI-ATIVOS" in workspace.strategy_summary._mode_pill.text()
    assert "Radar Multi-Ativos" in workspace._automation_detail.text()

    assert application is not None


def test_armed_transport_recovery_and_countdown_are_inline() -> None:
    application = QApplication.instance() or QApplication([])
    workspace = IqOptionWorkspaceWidget()

    workspace.update_bot_state(True, "TRANSPORT_DOWN", entry_ready=False)
    workspace.set_iqoption_reconnect_wait(272, 2)

    assert workspace._automation_pill.text() == "● BOT ARMADO · RECONECTANDO"
    assert "04:32" in workspace._iqoption_login_status.text()
    assert "2/3" in workspace._iqoption_login_status.text()
    assert "Reconectar" in workspace._iqoption_login_button.text()
    workspace._reconnect_timer.stop()
    assert application is not None
