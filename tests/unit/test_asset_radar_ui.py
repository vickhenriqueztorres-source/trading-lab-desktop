from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from apps.ui.components.asset_radar_panel import DerivAssetRadarWidget
from packages.protocol import UiDerivAssetRank


def test_asset_radar_renders_rank_and_has_no_execution_controls() -> None:
    app = QApplication.instance() or QApplication([])
    widget = DerivAssetRadarWidget()

    widget.update_ranking(
        (
            UiDerivAssetRank(
                "R_100",
                "CANDIDATE",
                "ASSET_SHADOW_CANDIDATE",
                500,
                500,
                selected=True,
                strategy_id="tail-probability-edge",
                contract_type="DIGITOVER",
                barrier=4,
                estimated_probability_pct="75.00",
                required_probability_pct="72.00",
                conservative_margin_pct="3.00",
                analysis_latency_microseconds=9,
            ),
            UiDerivAssetRank(
                "R_25",
                "MONITORING",
                "ASSET_SHADOW_NO_CONSERVATIVE_CANDIDATE",
                500,
                500,
            ),
        )
    )
    app.processEvents()

    assert widget._table.rowCount() == 2
    assert widget._table.item(0, 1).text() == "R_100"
    assert widget._table.item(0, 4).text() == "+3.00 pp"
    assert widget._state.text()
    assert widget.findChildren(QPushButton) == []


def test_asset_radar_shows_abstention_without_candidate() -> None:
    app = QApplication.instance() or QApplication([])
    widget = DerivAssetRadarWidget()

    widget.update_ranking(
        (
            UiDerivAssetRank(
                "R_100",
                "WARMING_UP",
                "ASSET_SHADOW_WARMING_UP",
                125,
                500,
            ),
        )
    )
    app.processEvents()

    assert "NO" in widget._state.text().upper() or "SIN" in widget._state.text().upper()
    assert widget._table.item(0, 5).text() == "125/500"
