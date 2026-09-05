"""Offscreen rendering of scoped rejection reasons; no desktop/profile interaction."""

import pytest


@pytest.mark.parametrize(
    "mode,text",
    [
        ("READ_ONLY_PROBE", "AGUARDANDO VERIFICAÇÃO"),
        ("CORRECT_CONFIGURATION", "CORRIGIR PARÂMETROS"),
        ("MANUAL_REVIEW", "REVISÃO MANUAL"),
    ],
)
def test_radar_explains_recovery_scope_and_condition(monkeypatch, mode, text):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from apps.ui.components.iqoption_asset_radar import IqOptionAssetRadarWidget
    from packages.protocol.ui_messages import UiIqOptionAssetRank

    app = QApplication.instance() or QApplication([])
    widget = IqOptionAssetRadarWidget()
    details = "EURUSD-OTC/BINARY_OPTION · consulta sem ordem em 30s"
    widget.update_ranking(
        [
            UiIqOptionAssetRank(
                symbol="EURUSD-OTC",
                display_name="EUR/USD OTC",
                rsi="--",
                direction=None,
                condition="IQOPTION_ACTIVE_SUSPENDED",
                selected=True,
                status=mode,
                candidate_details=details,
            )
        ]
    )
    assert widget._table.item(0, 4).text() == text
    assert widget._table.item(0, 4).toolTip() == details
    assert widget._table.item(0, 3).text() == details
    assert "SINAL DISPARADO" not in widget._table.item(0, 4).text()
    widget.close()
    assert app is not None
