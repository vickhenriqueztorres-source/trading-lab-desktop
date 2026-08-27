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

from apps.ui.i18n import t
from apps.ui.theme import ACCENT_AMBER, ACCENT_CYAN, ACCENT_GREEN, TEXT_MUTED
from packages.protocol import UiDerivAssetRank

_STRATEGY_NAMES = {
    "tail-probability-edge": "Tail Over/Under",
    "selective-differs-edge": "Selective Differs",
    "parity-regime-edge": "Parity Even/Odd",
}


class DerivAssetRadarWidget(QWidget):
    """Read-only multi-asset Shadow ranking; it has no execution controls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ranking: tuple[UiDerivAssetRank, ...] = ()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        header = QFrame()
        header.setObjectName("RiskSummary")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        titles = QVBoxLayout()
        self._title = QLabel()
        self._title.setObjectName("Title")
        titles.addWidget(self._title)
        self._subtitle = QLabel()
        self._subtitle.setObjectName("Subtitle")
        self._subtitle.setWordWrap(True)
        titles.addWidget(self._subtitle)
        header_layout.addLayout(titles, 1)
        self._state = QLabel()
        self._state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state.setObjectName("StatusPillOffline")
        header_layout.addWidget(self._state)
        root.addWidget(header)

        self._table = QTableWidget(0, 6)
        self._table.setObjectName("AssetRadarTable")
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(190)
        self._table.setMaximumHeight(250)
        table_header = self._table.horizontalHeader()
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self._table)

        self._notice = QLabel()
        self._notice.setWordWrap(True)
        self._notice.setStyleSheet(f"color: {TEXT_MUTED};")
        root.addWidget(self._notice)
        self.retranslate()

    def update_ranking(self, ranking: Sequence[UiDerivAssetRank]) -> None:
        self._ranking = tuple(ranking)
        self._table.setRowCount(len(self._ranking))
        selected = next((item for item in self._ranking if item.selected), None)
        if selected is None:
            self._state.setText(t("deriv.radar.abstain"))
            self._state.setObjectName("StatusPillOffline")
        else:
            self._state.setText(t("deriv.radar.candidate", symbol=selected.symbol))
            self._state.setObjectName("StatusPillOnline")
        self._state.style().unpolish(self._state)
        self._state.style().polish(self._state)

        for row, item in enumerate(self._ranking):
            candidate = _STRATEGY_NAMES.get(item.strategy_id or "", "—")
            if item.contract_type is not None:
                candidate = f"{candidate} · {item.contract_type}"
                if item.barrier is not None:
                    candidate += f" {item.barrier}"
            margin = (
                "—"
                if item.conservative_margin_pct is None
                else f"+{item.conservative_margin_pct} pp"
            )
            warmup = f"{item.warmup_current}/{item.warmup_required}"
            values = (
                "★" if item.selected else str(row + 1),
                item.symbol,
                self._state_text(item.state),
                candidate,
                margin,
                warmup,
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter
                    | (Qt.AlignmentFlag.AlignLeft if column == 3 else Qt.AlignmentFlag.AlignCenter)
                )
                if item.selected:
                    cell.setForeground(QColor(ACCENT_GREEN))
                elif item.state == "DATA_BLOCKED":
                    cell.setForeground(QColor(ACCENT_AMBER))
                elif item.state == "MONITORING":
                    cell.setForeground(QColor(ACCENT_CYAN))
                self._table.setItem(row, column, cell)
        self._table.resizeRowsToContents()

    @staticmethod
    def _state_text(state: str) -> str:
        key = {
            "CANDIDATE": "deriv.radar.state.candidate",
            "MONITORING": "deriv.radar.state.monitoring",
            "WARMING_UP": "deriv.radar.state.warming",
            "DATA_BLOCKED": "deriv.radar.state.blocked",
        }.get(state)
        return state if key is None else t(key)

    def retranslate(self) -> None:
        self._title.setText(t("deriv.radar.title"))
        self._subtitle.setText(t("deriv.radar.subtitle"))
        self._notice.setText(t("deriv.radar.notice"))
        self._table.setHorizontalHeaderLabels(
            [
                t("deriv.radar.rank"),
                t("deriv.radar.asset"),
                t("deriv.radar.state"),
                t("deriv.radar.best_signal"),
                t("deriv.radar.margin"),
                t("deriv.radar.warmup"),
            ]
        )
        self.update_ranking(self._ranking)
