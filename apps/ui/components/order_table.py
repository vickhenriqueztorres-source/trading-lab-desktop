from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import TEXT_MUTED
from packages.protocol.ui_messages import OrderSummary


class OrderTableView(QFrame):
    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self._title = QLabel(t("orders.title"))
        self._title.setObjectName("Title")
        layout.addWidget(self._title)

        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self._setup_headers()
        layout.addWidget(self._table)

        self._empty_label = QLabel(t("orders.empty"))
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(f"color: {TEXT_MUTED}; font-style: italic; padding: 20px;")
        layout.addWidget(self._empty_label)

    def _setup_headers(self) -> None:
        headers = [
            t("orders.col.id"),
            t("orders.col.broker"),
            t("orders.col.symbol"),
            t("orders.col.direction"),
            t("orders.col.amount"),
            t("orders.col.state"),
            t("orders.col.time"),
        ]
        self._table.setHorizontalHeaderLabels(headers)

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        if not orders:
            self._table.setRowCount(0)
            self._table.setVisible(False)
            self._empty_label.setVisible(True)
            return

        self._empty_label.setVisible(False)
        self._table.setVisible(True)
        self._table.setRowCount(len(orders))

        for row, ord in enumerate(orders):
            # ID
            short_id = ord.order_id[:16] + "..." if len(ord.order_id) > 16 else ord.order_id
            id_item = QTableWidgetItem(short_id)
            id_item.setToolTip(ord.order_id)
            self._table.setItem(row, 0, id_item)

            # Broker
            self._table.setItem(row, 1, QTableWidgetItem(ord.broker))

            # Symbol
            self._table.setItem(row, 2, QTableWidgetItem(ord.symbol))

            # Direction
            dir_item = QTableWidgetItem(ord.direction)
            if ord.direction.upper() == "CALL":
                dir_item.setForeground(Qt.GlobalColor.green)
            else:
                dir_item.setForeground(Qt.GlobalColor.red)
            self._table.setItem(row, 3, dir_item)

            # Amount
            amt_str = format_minor_units(ord.amount_minor_units, ord.currency)
            self._table.setItem(row, 4, QTableWidgetItem(amt_str))

            # State
            state_item = QTableWidgetItem(ord.state)
            if ord.state in {"SETTLED", "ACCEPTED", "OPEN"}:
                state_item.setForeground(Qt.GlobalColor.cyan)
            self._table.setItem(row, 5, state_item)

            # Time
            time_str = ord.created_at_utc.strftime("%H:%M:%S")
            self._table.setItem(row, 6, QTableWidgetItem(time_str))

    def retranslate(self) -> None:
        self._title.setText(t("orders.title"))
        self._empty_label.setText(t("orders.empty"))
        self._setup_headers()

    @property
    def order_count(self) -> int:
        return self._table.rowCount()
