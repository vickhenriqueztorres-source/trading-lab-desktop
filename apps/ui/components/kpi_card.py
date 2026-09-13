from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from apps.ui.theme import (
    ACCENT_GREEN,
    BORDER_COLOR,
    FONT_MONO,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class RingGauge(QWidget):
    """Circular ring gauge rendered via QPainter (no timers, no animations)."""

    def __init__(
        self,
        percent: float = 0.0,
        color: str = ACCENT_GREEN,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._percent = max(0.0, min(100.0, percent))
        self._color = color
        self.setFixedSize(60, 60)

    def set_value(self, percent: float, color: str | None = None) -> None:
        self._percent = max(0.0, min(100.0, percent))
        if color is not None:
            self._color = color
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen_width = 6.0
        rect = QRectF(
            pen_width / 2 + 2,
            pen_width / 2 + 2,
            60 - pen_width - 4,
            60 - pen_width - 4,
        )

        # Background ring (full circle)
        bg_pen = QPen(QColor(BORDER_COLOR), pen_width)
        bg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(bg_pen)
        painter.drawArc(rect, 0, 360 * 16)

        # Foreground arc starting from top (90 deg) going clockwise
        if self._percent > 0:
            fg_pen = QPen(QColor(self._color), pen_width)
            fg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(fg_pen)
            span_angle = int(-self._percent * 360 * 16 / 100)
            painter.drawArc(rect, 90 * 16, span_angle)

        # Center text percentage
        painter.setPen(QColor(TEXT_PRIMARY))
        font = QFont("Consolas", 10, QFont.Weight.Bold)
        painter.setFont(font)
        text = f"{int(round(self._percent))}%"
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


class KpiCard(QFrame):
    """Institutional KPI card with title, large mono value, delta, and optional RingGauge."""

    def __init__(
        self,
        title: str,
        value: str = "0",
        delta: str = "",
        show_gauge: bool = False,
        gauge_percent: float = 0.0,
        gauge_color: str = ACCENT_GREEN,
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        if tooltip:
            self.setToolTip(tooltip)

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(18, 16, 18, 16)
        root_layout.setSpacing(12)

        left_vbox = QVBoxLayout()
        left_vbox.setSpacing(4)
        left_vbox.setContentsMargins(0, 0, 0, 0)

        self._lbl_title = QLabel(title)
        self._lbl_title.setObjectName("kpiLabel")
        self._lbl_title.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 600; "
            f"text-transform: uppercase;"
        )
        left_vbox.addWidget(self._lbl_title)

        self._lbl_value = QLabel(value)
        self._lbl_value.setObjectName("kpiValue")
        self._lbl_value.setStyleSheet(
            f"font-family: {FONT_MONO}; font-size: 26px; font-weight: 700; color: {TEXT_PRIMARY};"
        )
        left_vbox.addWidget(self._lbl_value)

        self._lbl_delta = QLabel(delta)
        self._lbl_delta.setObjectName("hint")
        self._lbl_delta.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        left_vbox.addWidget(self._lbl_delta)

        root_layout.addLayout(left_vbox, 1)

        self._gauge: RingGauge | None = None
        if show_gauge:
            self._gauge = RingGauge(percent=gauge_percent, color=gauge_color)
            root_layout.addWidget(self._gauge, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_data(
        self,
        title: str | None = None,
        value: str | None = None,
        delta: str | None = None,
        gauge_percent: float | None = None,
        gauge_color: str | None = None,
        value_color: str | None = None,
        delta_color: str | None = None,
        tooltip: str | None = None,
    ) -> None:
        if title is not None:
            self._lbl_title.setText(title)
        if value is not None:
            self._lbl_value.setText(value)
        if delta is not None:
            self._lbl_delta.setText(delta)
        if value_color is not None:
            self._lbl_value.setStyleSheet(
                f"font-family: {FONT_MONO}; font-size: 26px; font-weight: 700; "
                f"color: {value_color};"
            )
        if delta_color is not None:
            self._lbl_delta.setStyleSheet(f"color: {delta_color}; font-size: 11px;")
        if self._gauge is not None and gauge_percent is not None:
            self._gauge.set_value(gauge_percent, gauge_color)
        if tooltip is not None:
            self.setToolTip(tooltip)


__all__ = ["KpiCard", "RingGauge"]
