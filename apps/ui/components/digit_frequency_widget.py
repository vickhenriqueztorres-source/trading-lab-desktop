from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout

from apps.ui.i18n import t
from apps.ui.theme import ACCENT_AMBER, ACCENT_CYAN, BORDER_ACCENT, BORDER_COLOR, TEXT_MUTED
from packages.market_data import DigitFrequencySnapshot


class DigitFrequencyWidget(QFrame):
    """Read-only 0–9 frequency view; observations are never presented as predictions."""

    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Surface")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self.title = QLabel()
        self.title.setObjectName("Title")
        root.addWidget(self.title)
        self.summary = QLabel()
        self.summary.setObjectName("Subtitle")
        root.addWidget(self.summary)

        bars = QHBoxLayout()
        bars.setSpacing(8)
        self._bars: list[QProgressBar] = []
        self._values: list[QLabel] = []
        for digit in range(10):
            column = QVBoxLayout()
            value = QLabel("0.0%")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
            bar = QProgressBar()
            bar.setOrientation(Qt.Orientation.Vertical)
            bar.setRange(0, 1000)
            bar.setValue(0)
            bar.setTextVisible(False)
            bar.setMinimumHeight(125)
            bar.setStyleSheet(self._bar_style(ACCENT_CYAN, 0.25))
            label = QLabel(str(digit))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("ValueMono")
            column.addWidget(value)
            column.addWidget(bar, 1)
            column.addWidget(label)
            bars.addLayout(column, 1)
            self._bars.append(bar)
            self._values.append(value)
        root.addLayout(bars)

        self.disclaimer = QLabel()
        self.disclaimer.setWordWrap(True)
        self.disclaimer.setObjectName("GuidanceText")
        root.addWidget(self.disclaimer)
        self._snapshot: DigitFrequencySnapshot | None = None
        self.retranslate()

    @staticmethod
    def _bar_style(color: str, opacity: float = 1.0) -> str:
        return (
            f"QProgressBar {{ border: 1px solid {BORDER_COLOR}; border-radius: 4px; "
            "background: rgba(8, 10, 15, 0.65); }} "
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )

    def update_snapshot(self, snapshot: DigitFrequencySnapshot | None) -> None:
        self._snapshot = snapshot
        if snapshot is None or snapshot.total_ticks == 0:
            for bar, value in zip(self._bars, self._values, strict=True):
                bar.setValue(0)
                bar.setStyleSheet(self._bar_style(ACCENT_CYAN, 0.25))
                value.setText("0.0%")
            self.summary.setText(t("DIGIT_FREQUENCY_WAITING"))
            return

        percentages = snapshot.frequency_percentages
        highest = max(range(10), key=lambda digit: (percentages[digit], -digit))
        lowest = min(range(10), key=lambda digit: (percentages[digit], -digit))
        for digit, (bar, value) in enumerate(zip(self._bars, self._values, strict=True)):
            percentage = percentages[digit]
            bar.setValue(min(1000, int(percentage * 10)))
            color = (
                ACCENT_AMBER
                if digit == highest
                else ACCENT_CYAN
                if digit == lowest
                else BORDER_ACCENT
            )
            opacity = 1.0
            bar.setStyleSheet(self._bar_style(color, opacity))
            value.setText(f"{percentage:.1f}%")
            bar.setToolTip(f"{digit}: {snapshot.frequency_counts[digit]} / {snapshot.total_ticks}")
        self.summary.setText(
            t(
                "DIGIT_FREQUENCY_SUMMARY",
                symbol=snapshot.symbol,
                ticks=snapshot.total_ticks,
                latency=snapshot.transport_latency_microseconds,
            )
        )

    def retranslate(self) -> None:
        self.title.setText(t("DIGIT_FREQUENCY_TITLE"))
        self.disclaimer.setText(t("DIGIT_FREQUENCY_DISCLAIMER"))
        self.update_snapshot(self._snapshot)
