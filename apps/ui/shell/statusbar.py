from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from apps.ui.design import TOKENS, icon
from apps.ui.i18n import I18nManager, t


class BottomBar(QFrame):
    """Bottom bar containing local clock, primary action slot, safe stop, and system status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BottomBar")
        self.setFixedHeight(72)
        self._system_ready = True
        self._latency_ms: int | None = None
        self._primary_action_widget: QWidget | None = None

        self.setStyleSheet(
            f"""
            QFrame#BottomBar {{
                background-color: {TOKENS.BG_CARD};
                border: none;
                border-top: 1px solid {TOKENS.BORDER_COLOR};
            }}
            QLabel#clockTime {{
                color: {TOKENS.TEXT_PRIMARY};
                font-family: {TOKENS.FONT_MONO};
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#clockDate {{
                color: {TOKENS.TEXT_MUTED};
                font-size: 11px;
            }}
            QLabel#statusLabel {{
                color: {TOKENS.TEXT_SECONDARY};
                font-size: 12px;
                font-weight: 600;
            }}
            """
        )

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(24, 0, 24, 0)
        self._layout.setSpacing(16)

        # 1. Clock and Date (Left)
        clock_container = QWidget()
        clock_layout = QVBoxLayout(clock_container)
        clock_layout.setContentsMargins(0, 0, 0, 0)
        clock_layout.setSpacing(2)
        clock_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._lbl_clock_time = QLabel()
        self._lbl_clock_time.setObjectName("clockTime")
        clock_layout.addWidget(self._lbl_clock_time)

        self._lbl_clock_date = QLabel()
        self._lbl_clock_date.setObjectName("clockDate")
        clock_layout.addWidget(self._lbl_clock_date)

        self._layout.addWidget(clock_container)

        self._layout.addStretch(1)

        # 2. Action Area: Primary Action Slot + Emergency Safe Stop Slot (Center)
        self._action_container = QWidget()
        self._action_layout = QHBoxLayout(self._action_container)
        self._action_layout.setContentsMargins(0, 0, 0, 0)
        self._action_layout.setSpacing(12)
        self._action_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Container for dynamic primary action
        self._primary_action_container = QWidget()
        self._primary_layout = QHBoxLayout(self._primary_action_container)
        self._primary_layout.setContentsMargins(0, 0, 0, 0)
        self._primary_layout.setSpacing(0)
        self._action_layout.addWidget(self._primary_action_container)

        self._layout.addWidget(self._action_container)

        self._layout.addStretch(1)

        # 3. Indicators: System Status + Latency (Right)
        right_container = QWidget()
        right_layout = QHBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)
        right_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # System Ready Indicator
        ready_box = QHBoxLayout()
        ready_box.setSpacing(6)
        self._lbl_ready_icon = QLabel()
        self._lbl_ready_icon.setPixmap(
            icon("icon-check", TOKENS.ACCENT_GREEN, size=16).pixmap(16, 16)
        )
        self._lbl_ready_text = QLabel(t("status.system_ready"))
        self._lbl_ready_text.setObjectName("statusLabel")
        ready_box.addWidget(self._lbl_ready_icon)
        ready_box.addWidget(self._lbl_ready_text)
        right_layout.addLayout(ready_box)

        # Latency Indicator
        latency_box = QHBoxLayout()
        latency_box.setSpacing(6)
        self._lbl_latency_icon = QLabel()
        self._lbl_latency_icon.setPixmap(
            icon("icon-wifi", TOKENS.TEXT_SECONDARY, size=16).pixmap(16, 16)
        )
        self._lbl_latency_text = QLabel(f"{t('status.latency')}: --")
        self._lbl_latency_text.setObjectName("statusLabel")
        latency_box.addWidget(self._lbl_latency_icon)
        latency_box.addWidget(self._lbl_latency_text)
        right_layout.addLayout(latency_box)

        self._layout.addWidget(right_container)

        # Clock Timer (every 1 second)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_clock)
        self._timer.start()
        self._update_clock()

    def add_permanent_action(self, widget: QWidget) -> None:
        """Add a permanent action widget (e.g. Safe Stop / Close) to the center action area."""
        self._action_layout.addWidget(widget)

    def set_primary_action(self, widget: QWidget | None) -> None:
        """Place a page-specific primary action widget into the center slot."""
        if self._primary_action_widget is not None:
            self._primary_layout.removeWidget(self._primary_action_widget)
            self._primary_action_widget.setParent(None)
            self._primary_action_widget = None

        if widget is not None:
            self._primary_action_widget = widget
            self._primary_layout.addWidget(widget)

    def set_system_ready(self, ready: bool, text: str | None = None) -> None:
        self._system_ready = ready
        if ready:
            self._lbl_ready_icon.setPixmap(
                icon("icon-check", TOKENS.ACCENT_GREEN, size=16).pixmap(16, 16)
            )
            self._lbl_ready_text.setText(text or t("status.system_ready"))
            self._lbl_ready_text.setStyleSheet(f"color: {TOKENS.TEXT_PRIMARY};")
        else:
            self._lbl_ready_icon.setPixmap(
                icon("icon-alert", TOKENS.ACCENT_RED, size=16).pixmap(16, 16)
            )
            self._lbl_ready_text.setText(text or t("status.system_blocked"))
            self._lbl_ready_text.setStyleSheet(f"color: {TOKENS.ACCENT_RED};")

    def set_latency(self, latency_ms: int | None) -> None:
        self._latency_ms = latency_ms
        val_str = f"{latency_ms} ms" if latency_ms is not None else "--"
        self._lbl_latency_text.setText(f"{t('status.latency')}: {val_str}")

    def _update_clock(self) -> None:
        now = datetime.now()
        self._lbl_clock_time.setText(now.strftime("%H:%M:%S"))

        lang = I18nManager.get_language()
        date_str = now.strftime("%d/%m/%Y") if lang == "es" else now.strftime("%Y-%m-%d")
        self._lbl_clock_date.setText(date_str)

    def retranslate(self) -> None:
        self._update_clock()
        self.set_system_ready(self._system_ready)
        self.set_latency(self._latency_ms)
