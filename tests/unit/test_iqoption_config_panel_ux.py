from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.iqoption_strategy_panel import IqOptionStrategyConfigWidget
from packages.protocol import UiIqOptionRiskConfig


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    assert isinstance(app, QApplication)
    return app


def test_config_panel_initial_state() -> None:
    _get_qapp()
    widget = IqOptionStrategyConfigWidget()
    assert widget.has_unsaved_changes() is False
    assert widget._unsaved_banner.isHidden() is True
    assert widget._apply.height() >= 40


def test_config_panel_dirty_detection_on_stake_change() -> None:
    _get_qapp()
    widget = IqOptionStrategyConfigWidget()
    dirty_events: list[bool] = []
    widget.dirty_state_changed.connect(dirty_events.append)

    assert widget.has_unsaved_changes() is False

    # Modify stake value
    widget._stake.setValue(5.00)

    assert widget.has_unsaved_changes() is True
    assert widget._unsaved_banner.isHidden() is False
    assert True in dirty_events


def test_config_panel_discard_unsaved_changes() -> None:
    _get_qapp()
    widget = IqOptionStrategyConfigWidget()
    initial_stake = widget._stake.value()

    widget._stake.setValue(initial_stake + 10.0)
    assert widget.has_unsaved_changes() is True

    widget.discard_unsaved_changes()
    assert widget.has_unsaved_changes() is False
    assert widget._stake.value() == initial_stake
    assert widget._unsaved_banner.isHidden() is True


def test_config_panel_apply_resets_dirty() -> None:
    _get_qapp()
    widget = IqOptionStrategyConfigWidget()

    widget._daily_stop.setValue(50.0)
    assert widget.has_unsaved_changes() is True

    widget.set_apply_result(accepted=True, rearmed=False)
    assert widget.has_unsaved_changes() is False
    assert widget._unsaved_banner.isHidden() is True
    assert "CONFIGURAÇÕES SALVAS" in widget._apply.text().upper()


def test_config_panel_set_config_resets_dirty() -> None:
    _get_qapp()
    widget = IqOptionStrategyConfigWidget()

    widget._stake.setValue(25.0)
    assert widget.has_unsaved_changes() is True

    new_cfg = UiIqOptionRiskConfig(
        stake_minor_units=300,
        daily_stop_loss_minor_units=1500,
    )
    widget.set_config(new_cfg)
    assert widget.has_unsaved_changes() is False
    assert widget._stake.value() == 3.0
