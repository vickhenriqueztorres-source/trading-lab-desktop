"""Interactive Multi-Asset RSI Radar for IQ Option."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.ui.theme import ACCENT_AMBER, ACCENT_CYAN, ACCENT_GREEN, ACCENT_RED, TEXT_MUTED
from packages.protocol import UiIqOptionAssetRank

DEFAULT_RADAR_ITEMS: tuple[UiIqOptionAssetRank, ...] = (
    UiIqOptionAssetRank("EURUSD-OTC", "EUR/USD OTC", "48.2", None, "NEUTRAL", True, "MONITORING"),
    UiIqOptionAssetRank("GBPUSD-OTC", "GBP/USD OTC", "52.1", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("USDJPY-OTC", "USD/JPY OTC", "49.5", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("AUDUSD-OTC", "AUD/USD OTC", "45.0", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("EURJPY-OTC", "EUR/JPY OTC", "54.3", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("GBPJPY-OTC", "GBP/JPY OTC", "51.8", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("AUDCAD-OTC", "AUD/CAD OTC", "47.6", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("NZDUSD-OTC", "NZD/USD OTC", "50.2", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("USDCAD-OTC", "USD/CAD OTC", "48.9", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("USDCHF-OTC", "USD/CHF OTC", "51.1", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("EURUSD", "EUR/USD", "49.0", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("GBPUSD", "GBP/USD", "50.5", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("USDJPY", "USD/JPY", "51.2", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("AUDUSD", "AUD/USD", "46.8", None, "NEUTRAL", False, "MONITORING"),
    UiIqOptionAssetRank("EURJPY", "EUR/JPY", "53.4", None, "NEUTRAL", False, "MONITORING"),
)


class IqOptionAssetRadarWidget(QWidget):
    """Real-time Multi-Asset Scanner & Radar for IQ Option RSI strategy."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ranking: tuple[UiIqOptionAssetRank, ...] = ()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        header = QFrame()
        header.setObjectName("RiskSummary")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)

        titles = QVBoxLayout()
        self._title = QLabel("RADAR MULTI-ATIVOS · IQ OPTION (RSI 14)")
        self._title.setObjectName("Title")
        titles.addWidget(self._title)

        self._subtitle = QLabel(
            "Varredura em tempo real de todas as paridades OTC e Forex "
            "com execução instantânea no primeiro sinal."
        )
        self._subtitle.setObjectName("Subtitle")
        self._subtitle.setWordWrap(True)
        titles.addWidget(self._subtitle)
        header_layout.addLayout(titles, 1)

        self._state = QLabel("AUTO SCANNING")
        self._state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state.setObjectName("StatusPillOnline")
        header_layout.addWidget(self._state)
        root.addWidget(header)

        self._table = QTableWidget(0, 5)
        self._table.setObjectName("AssetRadarTable")
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(220)
        self._table.setMaximumHeight(320)

        self._table.setHorizontalHeaderLabels(
            [
                "Ativo / Par",
                "RSI (14)",
                "Sinal",
                "Zona / Condição",
                "Status",
            ]
        )

        table_header = self._table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self._table)

        self._notice = QLabel(
            "⚡ Estratégia RSI Bounded Edge: Compra (CALL) quando RSI < 30 "
            "e Venda (PUT) quando RSI > 70."
        )
        self._notice.setWordWrap(True)
        self._notice.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        root.addWidget(self._notice)
        self.update_ranking(DEFAULT_RADAR_ITEMS)

    def update_ranking(self, ranking: Sequence[UiIqOptionAssetRank]) -> None:
        if not ranking:
            return
        self._ranking = tuple(ranking)
        self._table.setRowCount(len(self._ranking))

        triggered = next((item for item in self._ranking if item.status == "TRIGGERED"), None)
        selected = next((item for item in self._ranking if item.selected), None)

        if triggered is not None:
            self._state.setText(f"⚡ SINAL: {triggered.display_name}")
            self._state.setObjectName("StatusPillOnline")
        elif selected is not None and selected.symbol != "AUTO":
            self._state.setText(f"FOCO: {selected.display_name}")
            self._state.setObjectName("StatusPillOnline")
        else:
            self._state.setText("AUTO SCANNING")
            self._state.setObjectName("StatusPillOnline")

        self._state.style().unpolish(self._state)
        self._state.style().polish(self._state)

        for row, item in enumerate(self._ranking):
            # Column 0: Symbol display name
            sym_item = QTableWidgetItem(item.display_name)
            sym_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

            # Column 1: RSI Value
            rsi_val = float(item.rsi) if item.rsi.replace(".", "", 1).isdigit() else 50.0
            rsi_item = QTableWidgetItem(f"{item.rsi}")
            rsi_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Color coding for RSI value
            if rsi_val <= 30.0:
                rsi_item.setForeground(QColor(ACCENT_GREEN))
            elif rsi_val >= 70.0:
                rsi_item.setForeground(QColor(ACCENT_RED))
            else:
                rsi_item.setForeground(QColor(ACCENT_CYAN))

            # Column 2: Direction / Signal
            if item.direction == "CALL":
                sig_text = "🟢 COMPRA (CALL)"
                sig_color = ACCENT_GREEN
            elif item.direction == "PUT":
                sig_text = "🔴 VENDA (PUT)"
                sig_color = ACCENT_RED
            else:
                sig_text = "⚪ NEUTRO"
                sig_color = TEXT_MUTED

            sig_item = QTableWidgetItem(sig_text)
            sig_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sig_item.setForeground(QColor(sig_color))

            # Column 3: Condition / Zone
            if item.condition == "OVERSOLD":
                cond_text = "SOBREVENDA (< 30)"
                cond_color = ACCENT_GREEN
            elif item.condition == "OVERBOUGHT":
                cond_text = "SOBRECOMPRA (> 70)"
                cond_color = ACCENT_RED
            else:
                cond_text = "ZONA NEUTRA (30 — 70)"
                cond_color = TEXT_MUTED

            cond_item = QTableWidgetItem(cond_text)
            cond_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            cond_item.setForeground(QColor(cond_color))

            # Column 4: Status
            if item.status == "TRIGGERED":
                status_text = "⚡ SINAL DISPARADO"
                status_color = ACCENT_AMBER
            elif item.selected:
                status_text = "EM FOCO"
                status_color = ACCENT_CYAN
            else:
                status_text = "MONITORANDO"
                status_color = TEXT_MUTED

            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setForeground(QColor(status_color))

            self._table.setItem(row, 0, sym_item)
            self._table.setItem(row, 1, rsi_item)
            self._table.setItem(row, 2, sig_item)
            self._table.setItem(row, 3, cond_item)
            self._table.setItem(row, 4, status_item)


__all__ = ["IqOptionAssetRadarWidget"]
