from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_CYAN, ACCENT_GREEN, ACCENT_RED, TEXT_MUTED
from packages.protocol import OrderSummary


class ResultsDashboardWidget(QFrame):
    """Bounded dashboard built only from Core-confirmed settled order projections."""

    _MAX_RECENT = 10

    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        self._title = QLabel()
        self._title.setObjectName("Title")
        root.addWidget(self._title)
        self._scope = QLabel()
        self._scope.setWordWrap(True)
        self._scope.setObjectName("GuidanceText")
        root.addWidget(self._scope)

        kpis = QGridLayout()
        kpis.setHorizontalSpacing(12)
        self._total = self._kpi(kpis, 0)
        self._wins = self._kpi(kpis, 1)
        self._losses = self._kpi(kpis, 2)
        self._win_rate = self._kpi(kpis, 3)
        self._net = self._kpi(kpis, 4)
        root.addLayout(kpis)

        self._outcome_bar = QProgressBar()
        self._outcome_bar.setRange(0, 1000)
        self._outcome_bar.setValue(0)
        self._outcome_bar.setTextVisible(False)
        self._outcome_bar.setFixedHeight(9)
        self._outcome_bar.setStyleSheet(
            f"QProgressBar {{ background: rgba(255,51,102,0.35); border: none; "
            "border-radius: 4px; }} "
            f"QProgressBar::chunk {{ background: {ACCENT_GREEN}; border-radius: 4px; }}"
        )
        root.addWidget(self._outcome_bar)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setMaximumHeight(230)
        root.addWidget(self._table)

        self._empty = QLabel()
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(f"color: {TEXT_MUTED}; padding: 18px;")
        root.addWidget(self._empty)
        self._orders: tuple[OrderSummary, ...] = ()
        self.retranslate()

    @staticmethod
    def _kpi(layout: QGridLayout, column: int) -> tuple[QLabel, QLabel]:
        frame = QFrame()
        frame.setObjectName("Surface")
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 9, 12, 9)
        caption = QLabel()
        caption.setObjectName("Subtitle")
        value = QLabel("0")
        value.setObjectName("ValueMono")
        box.addWidget(caption)
        box.addWidget(value)
        layout.addWidget(frame, 0, column)
        return caption, value

    def update_results(self, orders: Sequence[OrderSummary]) -> None:
        self._orders = tuple(orders)
        settled = tuple(
            item
            for item in self._orders
            if item.state == "SETTLED" and item.realized_pnl_minor_units is not None
        )
        wins = sum((item.realized_pnl_minor_units or 0) > 0 for item in settled)
        losses = sum((item.realized_pnl_minor_units or 0) < 0 for item in settled)
        breakeven = len(settled) - wins - losses
        decided = wins + losses
        win_rate = (wins * 100 / decided) if decided else 0.0
        self._total[1].setText(str(len(settled)))
        self._wins[1].setText(str(wins))
        self._wins[1].setStyleSheet(f"color: {ACCENT_GREEN};")
        self._losses[1].setText(str(losses))
        self._losses[1].setStyleSheet(f"color: {ACCENT_RED};")
        self._win_rate[1].setText(f"{win_rate:.1f}%")
        self._win_rate[1].setStyleSheet(f"color: {ACCENT_CYAN};")
        self._outcome_bar.setValue(min(1000, int(win_rate * 10)))

        currencies = {item.currency for item in settled}
        if len(currencies) == 1:
            currency = next(iter(currencies))
            net = sum(item.realized_pnl_minor_units or 0 for item in settled)
            self._net[1].setText(format_minor_units(net, currency, positive_sign=True))
            self._net[1].setStyleSheet(f"color: {ACCENT_GREEN if net >= 0 else ACCENT_RED};")
        elif currencies:
            self._net[1].setText(t("results.mixed_currency"))
            self._net[1].setStyleSheet(f"color: {TEXT_MUTED};")
        else:
            self._net[1].setText("—")
            self._net[1].setStyleSheet(f"color: {TEXT_MUTED};")

        self._scope.setText(t("results.scope", count=len(settled), breakeven=breakeven))
        self._populate_recent(settled[: self._MAX_RECENT])

    def _populate_recent(self, settled: Sequence[OrderSummary]) -> None:
        self._table.setRowCount(len(settled))
        self._table.setVisible(bool(settled))
        self._empty.setVisible(not settled)
        for row, item in enumerate(settled):
            pnl_minor = item.realized_pnl_minor_units or 0
            result = (
                t("results.won")
                if pnl_minor > 0
                else t("results.lost")
                if pnl_minor < 0
                else t("results.even")
            )
            values = (
                item.created_at_utc.strftime("%H:%M:%S"),
                item.broker,
                item.symbol,
                result,
                format_minor_units(pnl_minor, item.currency, positive_sign=True),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column in {3, 4}:
                    cell.setForeground(
                        Qt.GlobalColor.green
                        if pnl_minor > 0
                        else Qt.GlobalColor.red
                        if pnl_minor < 0
                        else Qt.GlobalColor.gray
                    )
                self._table.setItem(row, column, cell)

    def retranslate(self) -> None:
        self._title.setText(t("results.title"))
        for pair, key in (
            (self._total, "results.total"),
            (self._wins, "results.wins"),
            (self._losses, "results.losses"),
            (self._win_rate, "results.win_rate"),
            (self._net, "results.net"),
        ):
            pair[0].setText(t(key))
        self._table.setHorizontalHeaderLabels(
            [
                t("results.time"),
                t("results.broker"),
                t("results.symbol"),
                t("results.outcome"),
                t("results.pnl"),
            ]
        )
        self._empty.setText(t("results.empty"))
        self.update_results(self._orders)
