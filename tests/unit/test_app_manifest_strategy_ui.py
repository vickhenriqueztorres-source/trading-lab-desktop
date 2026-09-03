"""Unit test for TradingLabMainWindow integration with ManifestStrategyPanelWidget."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.ui.app import TradingLabMainWindow
from apps.ui.controller import UiController


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_has_manifest_strategy_tab(qapp: QApplication, tmp_path: Path) -> None:
    mock_controller = MagicMock(spec=UiController)
    mock_controller.connected = True
    mock_controller.snapshot = None

    window = TradingLabMainWindow(mock_controller, profile_dir=tmp_path)
    try:
        assert hasattr(window, "_manifest_strategy_panel")
        tab_idx = window._TAB_STRATEGIES
        assert window._main_tabs.count() > tab_idx
        tab_text = window._main_tabs.tabText(tab_idx)
        assert "Estratégias" in tab_text or "Catalog" in tab_text

        cards = window._manifest_strategy_panel._cards
        assert len(cards) > 0, "Expected at least one strategy card to be rendered"
        for key, card in cards.items():
            assert card._header_label.text() != ""
            assert card._stat1_label.text() != ""
            assert "Taxa de acerto validada" in card._stat1_label.text()
            assert "Margem de segurança" in card._stat2_label.text()
            assert "Operações por dia" in card._stat3_label.text()
            assert "Pior sequência de perdas" in card._stat4_label.text()
            assert "Resultado em 1.000 ops" in card._stat5_label.text()
    finally:
        window.close()
