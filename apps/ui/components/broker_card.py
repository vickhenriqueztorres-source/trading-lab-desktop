from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from apps.ui.formatting import format_minor_units
from apps.ui.i18n import t
from apps.ui.theme import ACCENT_CYAN, ACCENT_GREEN, ACCENT_RED, TEXT_MUTED, TEXT_SECONDARY
from packages.protocol.ui_messages import BrokerCardStatus


class BrokerCardWidget(QFrame):
    def __init__(self, broker_name: str, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._broker_name = broker_name
        self._last_status: BrokerCardStatus | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Header Row: Name + Connection Pill
        header = QHBoxLayout()
        header.setSpacing(8)

        self._lbl_name = QLabel(broker_name)
        self._lbl_name.setObjectName("Title")
        header.addWidget(self._lbl_name)

        header.addStretch()

        self._lbl_status = QLabel(t("broker.disconnected"))
        self._lbl_status.setStyleSheet(
            f"color: {ACCENT_RED}; font-weight: bold; font-size: 11px; padding: 2px 6px;"
        )
        header.addWidget(self._lbl_status)
        layout.addLayout(header)

        # Mode & Connection detail
        self._lbl_detail = QLabel("—")
        self._lbl_detail.setObjectName("Subtitle")
        layout.addWidget(self._lbl_detail)

        # Balance Section
        balance_box = QVBoxLayout()
        balance_box.setSpacing(2)
        self._lbl_balance_title = QLabel(t("broker.balance"))
        self._lbl_balance_title.setObjectName("Subtitle")
        balance_box.addWidget(self._lbl_balance_title)

        self._lbl_balance = QLabel(t("broker.unavailable"))
        self._lbl_balance.setObjectName("ValueMono")
        self._lbl_balance.setStyleSheet(f"color: {ACCENT_CYAN}; font-size: 16px;")
        balance_box.addWidget(self._lbl_balance)
        layout.addLayout(balance_box)

        # Clock sync
        self._lbl_clock = QLabel("⏱️ " + t("broker.clock_untrusted"))
        self._lbl_clock.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self._lbl_clock)

    def update_card(self, status: BrokerCardStatus) -> None:
        self._last_status = status
        if status.is_connected:
            self._lbl_status.setText(f"● {t('broker.connected')}")
            self._lbl_status.setStyleSheet(
                f"color: {ACCENT_GREEN}; font-weight: bold; font-size: 11px; "
                "background: rgba(0, 245, 155, 0.1); "
                "border: 1px solid rgba(0, 245, 155, 0.3); "
                "border-radius: 4px; padding: 2px 6px;"
            )
        else:
            self._lbl_status.setText(f"○ {t('broker.disconnected')}")
            self._lbl_status.setStyleSheet(
                f"color: {ACCENT_RED}; font-weight: bold; font-size: 11px; "
                "background: rgba(255, 51, 102, 0.1); "
                "border: 1px solid rgba(255, 51, 102, 0.3); "
                "border-radius: 4px; padding: 2px 6px;"
            )

        mode = t(f"mode.{status.account_mode.value}")
        if mode.startswith("mode."):
            mode = status.account_mode.value
        self._lbl_detail.setText(f"{mode} | {status.connection_label}")

        if status.balance_minor_units is not None and status.currency is not None:
            self._lbl_balance.setText(
                format_minor_units(status.balance_minor_units, status.currency)
            )
        else:
            self._lbl_balance.setText(t("broker.unavailable"))

        if status.clock_synced:
            lat_str = (
                f" ({status.clock_latency_ms} ms)" if status.clock_latency_ms is not None else ""
            )
            self._lbl_clock.setText(f"⏱️ {t('broker.clock_synced')}{lat_str}")
            self._lbl_clock.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        else:
            self._lbl_clock.setText(f"⏱️ {t('broker.clock_untrusted')}")
            self._lbl_clock.setStyleSheet(f"color: {ACCENT_RED}; font-size: 11px;")

    def retranslate(self) -> None:
        self._lbl_balance_title.setText(t("broker.balance"))
        if self._last_status is None:
            self._lbl_status.setText(t("broker.disconnected"))
            self._lbl_balance.setText(t("broker.unavailable"))
        else:
            self.update_card(self._last_status)
