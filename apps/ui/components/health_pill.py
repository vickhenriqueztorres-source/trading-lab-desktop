from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from apps.ui.i18n import t
from apps.ui.theme import ACCENT_GREEN, ACCENT_RED
from packages.protocol.ui_messages import HealthGateStatus


class HealthGatePillWidget(QFrame):
    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Surface")

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 8, 12, 8)
        self._layout.setSpacing(10)

        self._title = QLabel("🛡️ " + t("gates.title") + ":")
        self._title.setStyleSheet("font-weight: bold; font-size: 12px;")
        self._layout.addWidget(self._title)

        self._pills_container = QWidget()
        self._pills_layout = QHBoxLayout(self._pills_container)
        self._pills_layout.setContentsMargins(0, 0, 0, 0)
        self._pills_layout.setSpacing(6)

        self._layout.addWidget(self._pills_container)
        self._layout.addStretch()

    def update_gates(self, gates: Sequence[HealthGateStatus]) -> None:
        while self._pills_layout.count():
            item = self._pills_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

        for gate in gates:
            pill = QLabel(f"{gate.gate_name}: {gate.reason_code or t('gates.open')}")
            pill.setToolTip(f"{gate.gate_name}\n{gate.description}")
            if gate.is_open:
                pill.setStyleSheet(
                    f"background: rgba(0, 245, 155, 0.1); color: {ACCENT_GREEN}; "
                    "border: 1px solid rgba(0, 245, 155, 0.3); "
                    "border-radius: 4px; padding: 2px 8px; font-size: 11px;"
                )
            else:
                pill.setStyleSheet(
                    f"background: rgba(255, 51, 102, 0.1); color: {ACCENT_RED}; "
                    "border: 1px solid rgba(255, 51, 102, 0.4); "
                    "border-radius: 4px; padding: 2px 8px; font-weight: bold; font-size: 11px;"
                )
            self._pills_layout.addWidget(pill)

    def retranslate(self) -> None:
        self._title.setText("🛡️ " + t("gates.title") + ":")
