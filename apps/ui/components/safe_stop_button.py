from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton

from apps.ui.i18n import t


class SafeStopButton(QPushButton):
    safe_stop_triggered = Signal()

    def __init__(self, parent: QPushButton | None = None) -> None:
        super().__init__(t("btn.safe_stop"), parent)
        self.setObjectName("SafeStopButton")
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self) -> None:
        self.safe_stop_triggered.emit()

    def retranslate(self) -> None:
        self.setText(t("btn.safe_stop"))
