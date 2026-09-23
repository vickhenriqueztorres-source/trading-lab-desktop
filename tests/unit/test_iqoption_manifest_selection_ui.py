import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.iqoption_strategy_panel import IqOptionStrategyConfigWidget
from packages.protocol.ui_messages import UiIqOptionAssetRank, UiIqOptionRiskConfig
from tests.unit.test_iqoption_candidates import entry


def test_single_panel_locks_manifest_symbol_and_timeframe():
    app = QApplication.instance() or QApplication([])
    panel = IqOptionStrategyConfigWidget()
    panel.set_manifest({"strategies": [entry(asset="EURUSD", timeframe="M5")]})
    panel.set_config(UiIqOptionRiskConfig(strategy_id="f5:a", symbol="GBPUSD-OTC"))
    assert panel._symbol.currentData() == "EURUSD"
    assert not panel._symbol.isEnabled()
    assert panel._timeframe.text() == "M5"
    sent = []
    panel.config_apply_requested.connect(sent.append)
    panel._emit_config()
    assert sent[0].timeframe_seconds == 300 and sent[0].symbol == "EURUSD"
    panel._mode.setCurrentText("AUTO")
    panel._emit_config()
    assert sent[-1].symbol == "AUTO"
    assert sent[-1].active_strategy_key == "AUTO"
    assert not panel._strategy.isEnabled()
    panel.close()
    assert app is not None


def test_local_strategy_cannot_be_applied_for_real_account():
    app = QApplication.instance() or QApplication([])
    panel = IqOptionStrategyConfigWidget()
    panel.set_account_type("REAL")
    assert not panel._apply.isEnabled()
    sent = []
    panel.config_apply_requested.connect(sent.append)
    panel._emit_config()
    assert sent == []
    panel.close()
    assert app is not None


def test_asset_selector_uses_core_catalogue_and_disables_discovery_only_product():
    app = QApplication.instance() or QApplication([])
    panel = IqOptionStrategyConfigWidget()
    panel.set_available_assets(
        (
            UiIqOptionAssetRank(
                "EURUSD",
                "EUR/USD · TURBO",
                "--",
                condition="WAITING_DATA",
                status="WAITING_DATA",
                readiness="READY",
                candidate_details="TURBO: OPEN · executável",
            ),
            UiIqOptionAssetRank(
                "XAUUSD-OTC",
                "XAU/USD OTC · DIGITAL",
                "--",
                condition="OPEN_READ_ONLY",
                status="DISCOVERY_ONLY",
                readiness="READ_ONLY",
                candidate_details="DIGITAL: OPEN · somente detecção",
            ),
        )
    )
    model = panel._symbol.model()
    assert panel._symbol.findData("EURUSD") > 0
    digital_row = panel._symbol.findData("XAUUSD-OTC")
    assert digital_row > 0
    assert not model.item(digital_row).isEnabled()
    panel.close()
    assert app is not None


def test_local_strategy_allows_auto_asset_selection():
    app = QApplication.instance() or QApplication([])
    panel = IqOptionStrategyConfigWidget()
    panel.set_account_type("PRACTICE")
    sent = []
    panel.config_apply_requested.connect(sent.append)

    # Select Hack Chino with AUTO asset
    hc_idx = panel._strategy.findData("iqoption-hack-chino")
    assert hc_idx >= 0
    panel._strategy.setCurrentIndex(hc_idx)

    auto_idx = panel._symbol.findData("AUTO")
    assert auto_idx >= 0
    panel._symbol.setCurrentIndex(auto_idx)

    assert panel._strategy.isEnabled()
    assert panel._symbol.isEnabled()
    assert panel._symbol.currentData() == "AUTO"
    assert not panel._auto_radar_hint.isHidden()

    panel._emit_config()
    assert len(sent) == 1
    assert sent[0].strategy_id == "iqoption-hack-chino"
    assert sent[0].symbol == "AUTO"
    assert sent[0].duration_seconds == 60

    # Select specific asset
    panel.set_available_assets(
        (
            UiIqOptionAssetRank(
                "EURUSD-OTC",
                "EUR/USD OTC",
                "--",
                condition="READY",
                status="ACTIVE",
                readiness="READY",
                candidate_details="",
            ),
        )
    )
    sym_idx = panel._symbol.findData("EURUSD-OTC")
    assert sym_idx >= 0
    panel._symbol.setCurrentIndex(sym_idx)
    assert panel._auto_radar_hint.isHidden()

    panel._emit_config()
    assert len(sent) == 2
    assert sent[1].strategy_id == "iqoption-hack-chino"
    assert sent[1].symbol == "EURUSD-OTC"
    assert sent[1].duration_seconds == 60

    panel.close()
    assert app is not None


def test_unknown_account_defaults_to_practice_and_allows_saving():
    app = QApplication.instance() or QApplication([])
    panel = IqOptionStrategyConfigWidget()
    panel.set_account_type("UNKNOWN")
    assert panel._apply.isEnabled()
    assert panel._strategy.isEnabled()
    assert panel._symbol.isEnabled()

    # Select Hack Chino with AUTO asset
    hc_idx = panel._strategy.findData("iqoption-hack-chino")
    assert hc_idx >= 0
    panel._strategy.setCurrentIndex(hc_idx)
    auto_idx = panel._symbol.findData("AUTO")
    assert auto_idx >= 0
    panel._symbol.setCurrentIndex(auto_idx)

    sent = []
    panel.config_apply_requested.connect(sent.append)
    panel._emit_config()
    assert len(sent) == 1
    assert sent[0].strategy_id == "iqoption-hack-chino"
    assert sent[0].symbol == "AUTO"

    # Also test that loading AUTO strategy leaves strategy and symbol enabled
    panel.set_config(UiIqOptionRiskConfig(strategy_id="AUTO", symbol="AUTO"))
    assert panel._strategy.isEnabled()
    assert panel._symbol.isEnabled()
    assert panel._strategy.currentData() == "AUTO"

    panel.close()
    assert app is not None
