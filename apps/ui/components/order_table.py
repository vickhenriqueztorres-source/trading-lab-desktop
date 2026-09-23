from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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
from apps.ui.theme import ACCENT_AMBER, ACCENT_GREEN, ACCENT_PRIMARY, ACCENT_RED, TEXT_MUTED
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

        self._review_summary = QLabel()
        self._review_summary.setWordWrap(True)
        self._review_summary.setStyleSheet(f"color: {ACCENT_AMBER};")
        self._review_summary.setVisible(False)
        layout.addWidget(self._review_summary)

        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self._setup_headers()
        self._orders: tuple[OrderSummary, ...] | None = None
        layout.addWidget(self._table)

        self._empty_label = QLabel(t("activity.empty"))
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
        incoming = tuple(orders)
        if incoming == self._orders:
            return
        self._orders = incoming
        if not orders:
            self._table.setRowCount(0)
            self._table.setVisible(False)
            self._review_summary.setVisible(False)
            self._empty_label.setVisible(True)
            return

        self._empty_label.setVisible(False)
        self._table.setVisible(True)
        self._table.setRowCount(len(orders))
        review_count = sum(
            order.result_review_required or order.reconciliation_review_required for order in orders
        )
        self._review_summary.setVisible(review_count > 0)
        self._review_summary.setText(t("orders.review.summary", count=review_count))

        for row, ord in enumerate(orders):
            # ID
            short_id = ord.order_id[:16] + "..." if len(ord.order_id) > 16 else ord.order_id
            contract_id = f"#{ord.broker_order_id}" if ord.broker_order_id else ""
            id_item = QTableWidgetItem(f"{short_id} / {contract_id}" if contract_id else short_id)
            id_item.setToolTip(
                f"{ord.order_id}\nContrato Deriv: {contract_id}" if contract_id else ord.order_id
            )
            self._table.setItem(row, 0, id_item)

            # Broker
            self._table.setItem(row, 1, QTableWidgetItem(ord.broker))

            # Symbol
            self._table.setItem(row, 2, QTableWidgetItem(ord.symbol))

            # Direction
            dir_item = QTableWidgetItem(ord.direction)
            if ord.direction.upper() == "CALL":
                dir_item.setForeground(QColor(ACCENT_GREEN))
            else:
                dir_item.setForeground(QColor(ACCENT_RED))
            self._table.setItem(row, 3, dir_item)

            # Amount
            amt_str = format_minor_units(ord.amount_minor_units, ord.currency)
            self._table.setItem(row, 4, QTableWidgetItem(amt_str))

            # State
            state_label = ord.state
            state_color = ACCENT_PRIMARY
            if ord.state == "OPEN":
                state_label = "● OPEN"
                state_color = ACCENT_PRIMARY
            elif ord.state == "MANUAL_REVIEW":
                state_label = "⚠ REVISÃO MANUAL"
                state_color = ACCENT_RED
            elif ord.reconciliation_review_required:
                state_label = t("orders.reconciliation.review")
                state_color = ACCENT_AMBER
            elif ord.state in {"UNKNOWN", "SETTLEMENT_UNKNOWN"} and (
                ord.reconciliation_attempt_count > 0
            ):
                state_label = t(
                    "orders.reconciliation.retry",
                    count=ord.reconciliation_attempt_count,
                )
                state_color = ACCENT_AMBER
            elif ord.state == "SETTLED" and ord.realized_pnl_minor_units is not None:
                pnl = format_minor_units(ord.realized_pnl_minor_units, ord.currency)
                if ord.result_review_required:
                    state_label = t("orders.result.unconfirmed")
                    state_color = ACCENT_AMBER
                elif ord.realized_pnl_minor_units > 0:
                    state_label = f"✓ {t('result.win')} (+{pnl})"
                    state_color = ACCENT_GREEN
                elif ord.realized_pnl_minor_units < 0:
                    state_label = f"✗ {t('result.loss')} ({pnl})"
                    state_color = ACCENT_RED
                else:
                    state_label = f"↔ TIE ({pnl})"
                    state_color = TEXT_MUTED

            state_item = QTableWidgetItem(state_label)
            state_item.setForeground(QColor(state_color))
            if ord.reconciliation_review_required:
                next_due = (
                    "—"
                    if ord.reconciliation_next_due_at is None
                    else ord.reconciliation_next_due_at.strftime("%H:%M:%S UTC")
                )
                state_item.setToolTip(
                    t(
                        "orders.reconciliation.help",
                        count=ord.reconciliation_attempt_count,
                        next_due=next_due,
                    )
                )
            elif ord.result_review_required:
                state_item.setToolTip(t("orders.result.unconfirmed.help"))
            self._table.setItem(row, 5, state_item)

            # Time
            time_str = ord.created_at_utc.strftime("%H:%M:%S")
            self._table.setItem(row, 6, QTableWidgetItem(time_str))

    def retranslate(self) -> None:
        self._title.setText(t("orders.title"))
        self._empty_label.setText(t("activity.empty"))
        self._setup_headers()
        orders = self._orders
        self._orders = None
        if orders is not None:
            self.update_orders(orders)

    @property
    def order_count(self) -> int:
        return self._table.rowCount()
