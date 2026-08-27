from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.components.deriv_workspace import DerivWorkspaceWidget
from apps.ui.components.synthetic_strategy_panel import (
    SyntheticStrategyConfigWidget,
    SyntheticStrategyLiveWidget,
)
from packages.protocol import UiDerivStrategyStatus


def _statuses() -> tuple[UiDerivStrategyStatus, ...]:
    return (
        UiDerivStrategyStatus(
            "tail-probability-edge",
            "Tail Probability Edge",
            "R_100 · 1 tick",
            "RESEARCH_SHADOW",
            "MONITORING",
            "TAIL_EDGE_NO_CONSERVATIVE_ADVANTAGE",
            500,
            500,
        ),
        UiDerivStrategyStatus(
            "selective-differs-edge",
            "Selective Differs Edge",
            "R_100 · 1 tick",
            "RESEARCH_SHADOW",
            "WARMING_UP",
            "DIFFERS_EDGE_WARMING_UP",
            300,
            500,
        ),
        UiDerivStrategyStatus(
            "parity-regime-edge",
            "Parity Regime Edge",
            "R_100 · 1 tick",
            "RESEARCH_SHADOW",
            "SHADOW_SIGNAL",
            "PARITY_EDGE_CONSERVATIVE_SIGNAL",
            500,
            500,
            1_800_000_000,
            "R_100",
            "DIGITODD",
            "ODD",
            None,
            "61.20",
            "52.00",
            84,
        ),
    )


def test_workspace_selects_all_three_digit_strategies_and_shows_latency() -> None:
    app = QApplication.instance() or QApplication([])
    workspace = DerivWorkspaceWidget()
    config = SyntheticStrategyConfigWidget()
    live = SyntheticStrategyLiveWidget()
    workspace.strategy_selected.connect(config.set_strategy)
    workspace.strategy_selected.connect(live.set_strategy)
    workspace.update_strategy_statuses(_statuses())
    live.update_statuses(_statuses())
    workspace.show()
    app.processEvents()

    workspace._strategy_buttons["parity-regime-edge"].click()
    app.processEvents()

    assert workspace.selected_strategy_id == "parity-regime-edge"
    assert workspace._strategy_title.text() == "Parity Regime Edge"
    assert "SINAL DEMO ELEGÍVEL" in workspace._strategy_buttons["parity-regime-edge"].text()
    assert live._signal_contract.text() == "DIGITODD"
    assert live._signal_probability.text() == "61.20% / 52.00%"
    assert live._analysis_latency.text() == "84 µs"
    assert "Bounded Martingale" in config._evidence.text()
    assert config.risk_panel.martingale_enabled_input.isChecked() is False
    workspace.close()


def test_risk_configuration_uses_the_full_no_scroll_workspace() -> None:
    app = QApplication.instance() or QApplication([])
    config = SyntheticStrategyConfigWidget()
    config.resize(1040, 300)
    config.show()
    app.processEvents()

    assert config.risk_panel.isVisible()
    assert config.risk_panel.height() >= 280
    assert config.risk_panel.geometry().bottom() <= config.contentsRect().bottom()
    assert config.risk_panel.martingale_enabled_input.isVisible()
    assert config.risk_panel.martingale_enabled_input.width() >= 120
    assert config.risk_panel.apply_button.isVisible()
    assert (
        config.risk_panel.apply_button.mapTo(
            config, config.risk_panel.apply_button.rect().bottomRight()
        ).y()
        <= config.height()
    )
    config.close()
