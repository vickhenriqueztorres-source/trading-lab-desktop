"""Dedicated Activity Page with client-side filtering and operational audit terminal."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components.log_terminal import OperationalLogTerminal
from apps.ui.components.manual_review_panel import ManualReviewPanel
from apps.ui.components.order_table import OrderTableView
from apps.ui.i18n import t
from packages.protocol.ui_messages import OrderSummary


class ActivityPage(QWidget):
    """Activity and audit workspace with client-side broker and outcome filtering."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raw_orders: tuple[OrderSummary, ...] = ()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # Header
        header = QVBoxLayout()
        header.setSpacing(4)
        self._title = QLabel(t("page.activity"))
        self._title.setObjectName("sectionTitle")
        self._title.setStyleSheet("font-size: 20px; font-weight: 700;")
        header.addWidget(self._title)

        self._subtitle = QLabel(t("page.activity_subtitle"))
        self._subtitle.setObjectName("hint")
        header.addWidget(self._subtitle)
        root.addLayout(header)

        # Backward compatibility reference for headless tests
        self.intro_label = self._subtitle

        # Filter Bar
        filter_bar = QFrame()
        filter_bar.setObjectName("Surface")
        filter_layout = QHBoxLayout(filter_bar)
        filter_layout.setContentsMargins(12, 8, 12, 8)
        filter_layout.setSpacing(12)

        self._filter_label = QLabel(t("activity.filter_prefix"))
        self._filter_label.setObjectName("Subtitle")
        filter_layout.addWidget(self._filter_label)

        self._broker_filter = QComboBox()
        self._broker_filter.addItem(t("activity.filter_all"), "all")
        self._broker_filter.addItem(t("activity.filter_deriv"), "DERIV")
        self._broker_filter.addItem(t("activity.filter_iqoption"), "IQOPTION")
        self._broker_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self._broker_filter)

        self._result_filter = QComboBox()
        self._result_filter.addItem(t("activity.filter_all_results"), "all")
        self._result_filter.addItem(t("activity.filter_wins"), "win")
        self._result_filter.addItem(t("activity.filter_losses"), "loss")
        self._result_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self._result_filter)

        filter_layout.addStretch()
        root.addWidget(filter_bar)

        # Tabs: Orders & Audit Log
        self.tabs = QTabWidget()
        self.tabs.setObjectName("ActivityTabs")

        orders_page = QWidget()
        orders_layout = QVBoxLayout(orders_page)
        orders_layout.setContentsMargins(0, 8, 0, 0)
        self.manual_review_panel = ManualReviewPanel()
        orders_layout.addWidget(self.manual_review_panel)
        self.order_table = OrderTableView()
        orders_layout.addWidget(self.order_table)
        self.tabs.addTab(orders_page, t("tabs.orders"))

        self.log_terminal = OperationalLogTerminal()
        self.tabs.addTab(self.log_terminal, t("tabs.logs"))

        root.addWidget(self.tabs, 1)

    def set_controller(self, controller: Any) -> None:
        self.manual_review_panel.set_controller(controller)

    def update_orders(self, orders: Sequence[OrderSummary]) -> None:
        self._raw_orders = tuple(orders)
        self.manual_review_panel.update_orders(orders)
        self._apply_filters()

    def _on_filter_changed(self, _index: int = 0) -> None:
        self._apply_filters()

    def _apply_filters(self) -> None:
        selected_broker = str(self._broker_filter.currentData())
        selected_result = str(self._result_filter.currentData())

        filtered: list[OrderSummary] = []
        for ord in self._raw_orders:
            # Broker filtering
            if selected_broker == "DERIV" and ord.broker.upper() != "DERIV":
                continue
            if selected_broker == "IQOPTION" and "IQ" not in ord.broker.upper():
                continue

            # Result filtering
            is_invalid_win = selected_result == "win" and (
                ord.state != "SETTLED" or (ord.realized_pnl_minor_units or 0) <= 0
            )
            is_invalid_loss = selected_result == "loss" and (
                ord.state != "SETTLED" or (ord.realized_pnl_minor_units or 0) >= 0
            )
            if is_invalid_win or is_invalid_loss:
                continue

            filtered.append(ord)

        self.order_table.update_orders(filtered)

    def retranslate(self) -> None:
        self._title.setText(t("page.activity"))
        self._subtitle.setText(t("page.activity_subtitle"))
        self._filter_label.setText(t("activity.filter_prefix"))

        # Update broker filter texts
        broker_keys = {
            "all": "activity.filter_all",
            "DERIV": "activity.filter_deriv",
            "IQOPTION": "activity.filter_iqoption",
        }
        for i in range(self._broker_filter.count()):
            key = str(self._broker_filter.itemData(i))
            if key in broker_keys:
                self._broker_filter.setItemText(i, t(broker_keys[key]))

        # Update result filter texts
        result_keys = {
            "all": "activity.filter_all_results",
            "win": "activity.filter_wins",
            "loss": "activity.filter_losses",
        }
        for i in range(self._result_filter.count()):
            key = str(self._result_filter.itemData(i))
            if key in result_keys:
                self._result_filter.setItemText(i, t(result_keys[key]))

        self.tabs.setTabText(0, t("tabs.orders"))
        self.tabs.setTabText(1, t("tabs.logs"))
        self._filter_label.setText(t("activity.filter_prefix"))
        self.order_table.retranslate()


__all__ = ["ActivityPage"]
