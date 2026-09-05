import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.iqoption_strategy_panel import IqOptionStrategyConfigWidget
from packages.protocol.ui_messages import UiIqOptionRiskConfig
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


def test_local_rsi_cannot_be_applied_for_real_account():
    app = QApplication.instance() or QApplication([])
    panel = IqOptionStrategyConfigWidget()
    panel.set_account_type("REAL")
    assert not panel._apply.isEnabled()
    sent = []
    panel.config_apply_requested.connect(sent.append)
    panel._emit_config()
    assert sent == []
    assert "não validado" in panel._strategy.currentText()
    panel.close()
    assert app is not None
