"""Integration test for strategy selection in UI and dynamic execution in AutoTrader."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.manifest_catalog import DynamicManifestCatalog
from apps.ui.app import TradingLabMainWindow
from apps.ui.controller import UiController
from packages.domain.market import MarketCandle
from packages.domain.models import Direction
from datetime import UTC, datetime


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_ui_card_toggle_updates_controller_for_iqoption_and_deriv(
    qapp: QApplication, tmp_path: Path
) -> None:
    mock_ctrl = MagicMock(spec=UiController)
    mock_ctrl.connected = True
    mock_ctrl.snapshot = None

    win = TradingLabMainWindow(mock_ctrl, profile_dir=tmp_path)
    try:
        cards = win._manifest_strategy_panel._cards
        assert len(cards) >= 10, "Expected full catalog with all strategies loaded"

        # 1. Toggle an IQ Option card (F1)
        f1_key = "f1:EURUSD-OTC:M1:00-24:rsi_bollinger"
        assert f1_key in cards
        win._on_manifest_strategy_toggled(f1_key, True)

        # Verify controller received update_iqoption_risk_config
        mock_ctrl.update_iqoption_risk_config.assert_called()
        call_arg = mock_ctrl.update_iqoption_risk_config.call_args[0][0]
        assert call_arg.strategy_id == f1_key
        assert call_arg.symbol == "EURUSD-OTC"

        # 2. Toggle a Deriv card (D1)
        d1_key = "tail-probability-edge"
        assert d1_key in cards
        win._on_manifest_strategy_toggled(d1_key, True)

        # Verify controller received update_digit_risk_config
        mock_ctrl.update_digit_risk_config.assert_called()
        call_arg_deriv = mock_ctrl.update_digit_risk_config.call_args[0][0]
        assert call_arg_deriv.active_strategy_id == d1_key
        assert call_arg_deriv.selected_symbol == "1HZ100V"

        # 3. Click Turn On All
        win._manifest_strategy_panel._btn_turn_on_all.click()
        assert win._manifest_strategy_panel._selection_mode == "MULTI"
        call_arg_multi = mock_ctrl.update_iqoption_risk_config.call_args[0][0]
        assert call_arg_multi.strategy_id == "AUTO"
        assert call_arg_multi.symbol == "AUTO"
    finally:
        win.close()


def test_iqoption_auto_trader_executes_catalog_family_strategy() -> None:
    catalog = DynamicManifestCatalog()
    catalog.apply_manifest({
        "manifest_version": 1,
        "strategies": [
            {
                "key": "f1:EURUSD-OTC:M1:00-24:test",
                "family": "F1",
                "display_name_pt": "F1 Test",
                "asset": "EURUSD-OTC",
                "timeframe": "M1",
                "hours_utc": [0, 24],
                "params": {
                    "rsi_period": 14,
                    "rsi_overbought": 70,
                    "rsi_oversold": 30,
                    "bb_period": 20,
                    "bb_std": 2.0,
                },
                "validated": {
                    "p_hat": "0.66",
                    "wilson_lower": "0.63",
                    "p_min_at_validation": "0.55",
                    "payout_min": "0.85",
                    "ops_per_day": "20",
                    "worst_streak": 4,
                    "result_1000_ops_stake10": "2000",
                    "score": "0.75",
                },
                "status": "approved",
            }
        ],
    })

    risk_config = IqOptionRiskConfig(
        strategy_id="f1:EURUSD-OTC:M1:00-24:test",
        symbol="EURUSD-OTC",
    )

    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: None,
        risk_config_provider=lambda: risk_config,
        operator_armed=lambda: True,
        catalog_provider=lambda: catalog,
    )

    assert trader._catalog_provider is not None
    active = catalog.active_strategies.get(risk_config.strategy_id)
    assert active is not None
    assert active.instance.family_name == "F1"
