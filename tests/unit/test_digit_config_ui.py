from __future__ import annotations

import os
import secrets
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from apps.core.digit_risk_config import DigitRiskConfig
from apps.core.ui_service import CoreUiProjectionService
from apps.ui.components.digit_config_panel import DigitConfigPanelWidget
from apps.ui.components.digit_frequency_widget import DigitFrequencyWidget
from apps.ui.i18n import TRANSLATIONS, I18nManager
from apps.ui.ipc_client import UiIpcClient
from packages.market_data import DigitFrequencySnapshot
from packages.protocol import (
    BrokerCardStatus,
    HealthGateStatus,
    UiAccountMode,
    UiDigitRiskConfig,
    UiDigitRiskConfigStatus,
    UiGlobalState,
    UiProjectionSnapshot,
)
from packages.security import SecretValue

_PANEL_KEYS = {
    "DIGIT_STRATEGY_TITLE",
    "STAKE_LABEL",
    "STOP_LOSS_LABEL",
    "TAKE_PROFIT_LABEL",
    "CONSECUTIVE_LOSS_LABEL",
    "COOLDOWN_LABEL",
    "CONFIDENCE_LABEL",
    "APPLY_CONFIG_BTN",
    "RESET_DEMO_SESSION_BTN",
    "DIGIT_SYMBOL_LABEL",
    "DIGIT_CONFIDENCE_DISCLAIMER",
    "MARTINGALE_ENABLED_LABEL",
    "MARTINGALE_MULTIPLIER_LABEL",
    "MARTINGALE_PROJECTION",
    "MARTINGALE_PROJECTION_UNAVAILABLE",
    "MARTINGALE_DISABLED_STATUS",
}


def _config() -> UiDigitRiskConfig:
    return UiDigitRiskConfig(
        stake_minor_units=1000,
        daily_stop_loss_minor_units=5000,
        daily_take_profit_minor_units=3000,
        max_consecutive_losses=2,
        cooldown_seconds_after_loss=30.0,
        min_quantum_confidence_pct=Decimal("92.5"),
        selected_symbol="R_100",
        currency="USD",
    )


def _snapshot() -> UiProjectionSnapshot:
    return UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(HealthGateStatus("GLOBAL_ENTRY_GATE", True, None, "ready"),),
        broker_cards=(
            BrokerCardStatus(
                "DERIV",
                UiAccountMode.PRACTICE,
                True,
                None,
                None,
                True,
            ),
        ),
        active_orders=(),
        daily_pnl_minor_units=0,
        daily_pnl_currency=None,
        digit_risk_config=_config(),
    )


def test_digit_config_ipc_roundtrip_updates_core_callback() -> None:
    received: list[DigitRiskConfig] = []
    token = SecretValue.from_text(secrets.token_hex(32))
    service = CoreUiProjectionService(
        token,
        _snapshot,
        lambda: None,
        lambda: True,
        lambda: None,
        digit_risk_config_update=lambda config: (received.append(config) is None, None),
    )
    service.start()
    client = UiIpcClient.connect(service.port, token)
    try:
        ack = client.update_digit_risk_config(_config())
        assert ack.status is UiDigitRiskConfigStatus.OK
        assert ack.reason_code is None
        assert len(received) == 1
        assert received[0].stake_minor_units == 1000
    finally:
        client.close()
        service.stop()


def test_digit_panel_i18n_parity_and_headless_rendering() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    for key in _PANEL_KEYS:
        assert set(TRANSLATIONS[key]) == {"en", "es"}
        assert all(TRANSLATIONS[key][language] for language in ("en", "es"))

    panel = DigitConfigPanelWidget()
    panel.set_config(_config())
    panel.show()
    app.processEvents()
    assert panel.current_config() == UiDigitRiskConfig(
        **{
            **_config().to_payload(),
            "min_quantum_confidence_pct": Decimal("92.5"),
            "martingale_multiplier": Decimal("2.00"),
            "martingale_max_stake_minor_units": 5000,
        }
    )
    assert panel.apply_button.isEnabled()
    assert panel.reset_session_button.isEnabled()
    assert "result" in panel.reset_session_button.text().lower()

    I18nManager.set_language("en")
    panel.retranslate()
    assert panel.title.text() == TRANSLATIONS["DIGIT_STRATEGY_TITLE"]["en"]
    I18nManager.set_language("es")
    panel.close()


def test_digit_frequency_widget_renders_live_observations_headlessly() -> None:
    app = QApplication.instance() or QApplication([])
    widget = DigitFrequencyWidget()
    widget.update_snapshot(
        DigitFrequencySnapshot(
            symbol="R_100",
            total_ticks=10,
            frequency_counts=(0, 1, 1, 1, 1, 1, 1, 1, 1, 2),
            frequency_percentages=tuple(
                Decimal(value) for value in (0, 10, 10, 10, 10, 10, 10, 10, 10, 20)
            ),
            transport_latency_microseconds=850,
        )
    )
    widget.show()
    app.processEvents()

    assert widget._bars[9].value() == 200
    assert widget._bars[0].value() == 0
    assert "R_100" in widget.summary.text()
    assert "predicción" in widget.disclaimer.text().lower()
    widget.close()


def test_digit_panel_enables_only_a_fully_bounded_martingale_sequence() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    panel = DigitConfigPanelWidget()
    bounded = UiDigitRiskConfig(
        stake_minor_units=1000,
        daily_stop_loss_minor_units=5000,
        daily_take_profit_minor_units=3000,
        max_consecutive_losses=2,
        cooldown_seconds_after_loss=30.0,
        min_quantum_confidence_pct=Decimal("92.5"),
        selected_symbol="R_100",
        martingale_max_steps=2,
        martingale_max_stake_minor_units=2000,
    )
    panel.set_config(bounded)

    panel.martingale_enabled_input.setChecked(True)
    config = panel.current_config()
    assert config is not None
    assert config.martingale_enabled is True
    assert config.martingale_max_steps == 2
    assert config.max_consecutive_losses == 3
    assert config.martingale_max_stake_minor_units == 5000
    assert "11.11" in panel.martingale_projection.text()
    assert "40.00" in panel.martingale_projection.text()
    panel.close()


def test_digit_panel_hides_internal_martingale_safety_bounds() -> None:
    app = QApplication.instance() or QApplication([])
    assert app is not None
    panel = DigitConfigPanelWidget()
    panel.set_config(_config())
    panel.show()
    app.processEvents()

    visible_text = " ".join(
        label.text() for label in panel.findChildren(QLabel) if label.isVisible()
    )
    for language in ("en", "es"):
        assert TRANSLATIONS["MARTINGALE_STEPS_LABEL"][language] not in visible_text
        assert TRANSLATIONS["MARTINGALE_MAX_STAKE_LABEL"][language] not in visible_text
    assert not hasattr(panel, "martingale_steps_input")
    assert not hasattr(panel, "martingale_max_stake_input")
    assert panel.current_config() is not None
    assert panel.current_config().martingale_max_stake_minor_units == 5000
    panel.close()
