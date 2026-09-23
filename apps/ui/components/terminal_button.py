from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QPushButton, QWidget

from apps.ui.design.icons import icon
from apps.ui.design.tokens import TOKENS


class TerminalButton(QPushButton):
    """Institutional terminal button with debounce and busy state support."""

    debounced_clicked = Signal()

    def __init__(
        self,
        text: str = "",
        variant: str = "secondary",
        icon_name: str | None = None,
        parent: QWidget | None = None,
        *,
        debounce_ms: int = 400,
    ) -> None:
        super().__init__(text, parent)
        self._variant = variant
        self._original_text = text
        self._original_icon_name = icon_name
        self._debounce_ms = debounce_ms
        self._last_click_mono: float = 0.0
        self._is_busy = False

        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(self._restore_original_state)

        self._apply_styling()
        if icon_name:
            self._set_themed_icon(icon_name)

        self.clicked.connect(self._handle_clicked)

    def _apply_styling(self) -> None:
        r = TOKENS.RADIUS_SM
        self.setMinimumHeight(34)
        if self._variant == "primary":
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(31, 181, 122, 0.12);
                    color: {TOKENS.ACCENT_GREEN};
                    border: 1px solid {TOKENS.ACCENT_GREEN};
                    border-radius: {r}px;
                    padding: 5px 14px;
                    font-weight: 700;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(31, 181, 122, 0.25);
                    border-color: #26D993;
                    color: #26D993;
                }}
                QPushButton:pressed {{
                    background-color: rgba(31, 181, 122, 0.35);
                }}
                QPushButton:disabled {{
                    background-color: {TOKENS.BG_ELEVATED};
                    color: {TOKENS.TEXT_MUTED};
                    border-color: {TOKENS.BORDER_COLOR};
                }}
            """)
        elif self._variant == "danger":
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(229, 72, 77, 0.08);
                    color: {TOKENS.ACCENT_RED};
                    border: 1px solid {TOKENS.ACCENT_RED};
                    border-radius: {r}px;
                    padding: 6px 14px;
                    font-weight: 700;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(229, 72, 77, 0.18);
                    border-color: #F05D62;
                    color: #F05D62;
                }}
                QPushButton:pressed {{
                    background-color: rgba(229, 72, 77, 0.28);
                }}
                QPushButton:disabled {{
                    background-color: {TOKENS.BG_ELEVATED};
                    color: {TOKENS.TEXT_MUTED};
                    border-color: {TOKENS.BORDER_COLOR};
                }}
            """)
        elif self._variant == "warning":
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(217, 162, 27, 0.08);
                    color: {TOKENS.ACCENT_AMBER};
                    border: 1px solid {TOKENS.ACCENT_AMBER};
                    border-radius: {r}px;
                    padding: 6px 14px;
                    font-weight: 700;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: rgba(217, 162, 27, 0.18);
                    border-color: #EDB42A;
                    color: #EDB42A;
                }}
                QPushButton:pressed {{
                    background-color: rgba(217, 162, 27, 0.28);
                }}
                QPushButton:disabled {{
                    background-color: {TOKENS.BG_ELEVATED};
                    color: {TOKENS.TEXT_MUTED};
                    border-color: {TOKENS.BORDER_COLOR};
                }}
            """)
        else:  # secondary
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {TOKENS.BG_SURFACE};
                    color: {TOKENS.TEXT_PRIMARY};
                    border: 1px solid {TOKENS.BORDER_ACCENT};
                    border-radius: {r}px;
                    padding: 6px 14px;
                    font-weight: 600;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: {TOKENS.BG_ELEVATED};
                    border-color: {TOKENS.ACCENT_PRIMARY};
                    color: {TOKENS.TEXT_PRIMARY};
                }}
                QPushButton:pressed {{
                    background-color: {TOKENS.BG_ROOT};
                }}
                QPushButton:disabled {{
                    background-color: {TOKENS.BG_ELEVATED};
                    color: {TOKENS.TEXT_MUTED};
                    border-color: {TOKENS.BORDER_COLOR};
                }}
            """)

    def _set_themed_icon(self, icon_name: str) -> None:
        if self._variant == "primary":
            ic = icon(icon_name, TOKENS.ACCENT_GREEN, 14)
        elif self._variant == "danger":
            ic = icon(icon_name, TOKENS.ACCENT_RED, 14)
        elif self._variant == "warning":
            ic = icon(icon_name, TOKENS.ACCENT_AMBER, 14)
        else:
            ic = icon(icon_name, TOKENS.TEXT_PRIMARY, 14)
        self.setIcon(ic)

    def _handle_clicked(self) -> None:
        now = time.monotonic()
        if (now - self._last_click_mono) * 1000 < self._debounce_ms:
            return
        if self._is_busy:
            return
        self._last_click_mono = now
        self.debounced_clicked.emit()

    def set_busy(self, busy: bool, busy_text: str | None = None) -> None:
        self._is_busy = busy
        self.setEnabled(not busy)
        if busy:
            if busy_text:
                self.setText(busy_text)
            self.setIcon(icon("icon-refresh", TOKENS.TEXT_MUTED, 14))
        else:
            self._restore_original_state()

    def flash_success(self, message: str = "Confirmado", duration_ms: int = 1500) -> None:
        self.setText(message)
        self.setIcon(icon("icon-check", TOKENS.ACCENT_GREEN, 14))
        self._reset_timer.start(duration_ms)

    def _restore_original_state(self) -> None:
        self.setText(self._original_text)
        if self._original_icon_name:
            self._set_themed_icon(self._original_icon_name)
        else:
            self.setIcon(QIcon())
        self.setEnabled(True)

    def update_label(self, text: str, icon_name: str | None = None) -> None:
        self._original_text = text
        self.setText(text)
        if icon_name:
            self._original_icon_name = icon_name
            self._set_themed_icon(icon_name)
