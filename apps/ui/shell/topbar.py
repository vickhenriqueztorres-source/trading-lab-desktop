from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from apps.ui.design import TOKENS, icon
from apps.ui.i18n import t


class TopBar(QFrame):
    """Top bar containing section title, core connection status pill, and account chip."""

    account_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(56)
        self._core_connected = True
        self._current_title_key: str | None = "nav.overview"
        self._custom_title: str | None = None
        self._email: str | None = None
        self._plan: str | None = None

        self.setStyleSheet(
            f"""
            QFrame#TopBar {{
                background-color: {TOKENS.BG_CARD};
                border: none;
                border-bottom: 1px solid {TOKENS.BORDER_COLOR};
            }}
            QLabel#sectionTitle {{
                color: {TOKENS.TEXT_PRIMARY};
                font-size: 16px;
                font-weight: 700;
            }}
            QFrame#statusPill {{
                background-color: {TOKENS.BG_SURFACE};
                border: 1px solid {TOKENS.BORDER_COLOR};
                border-radius: {TOKENS.RADIUS_MD}px;
                padding: 4px 12px;
            }}
            QLabel#statusText {{
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#userChip {{
                background-color: {TOKENS.BG_SURFACE};
                border: 1px solid {TOKENS.BORDER_COLOR};
                border-radius: {TOKENS.RADIUS_MD}px;
                color: {TOKENS.TEXT_PRIMARY};
                font-size: 12px;
                font-weight: 600;
                padding: 4px 12px;
            }}
            QPushButton#userChip:hover {{
                background-color: {TOKENS.BG_ELEVATED};
                border-color: {TOKENS.BORDER_HOVER};
            }}
            QLabel#planBadge {{
                background-color: {TOKENS.ACCENT_PRIMARY};
                color: #070B14;
                font-size: 10px;
                font-weight: 800;
                border-radius: 4px;
                padding: 2px 6px;
            }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(16)

        # 1. Page Title
        self._lbl_title = QLabel(t("nav.overview"))
        self._lbl_title.setObjectName("sectionTitle")
        layout.addWidget(self._lbl_title)

        layout.addStretch()

        # 2. Core IPC Status Pill
        self._pill = QFrame()
        self._pill.setObjectName("statusPill")
        pill_layout = QHBoxLayout(self._pill)
        pill_layout.setContentsMargins(8, 4, 8, 4)
        pill_layout.setSpacing(6)

        self._lbl_dot = QLabel()
        self._lbl_dot.setPixmap(icon("status-dot", TOKENS.ACCENT_GREEN, size=12).pixmap(12, 12))
        pill_layout.addWidget(self._lbl_dot)

        self._lbl_status = QLabel(t("status.core_connected"))
        self._lbl_status.setObjectName("statusText")
        self._lbl_status.setStyleSheet(f"color: {TOKENS.ACCENT_GREEN};")
        pill_layout.addWidget(self._lbl_status)
        layout.addWidget(self._pill)

        # 3. User Account Chip
        self._btn_account = QPushButton()
        self._btn_account.setObjectName("userChip")
        self._btn_account.setIcon(icon("nav-account", TOKENS.TEXT_SECONDARY, size=16))
        self._btn_account.clicked.connect(self.account_clicked.emit)

        account_layout = QHBoxLayout()
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.setSpacing(6)

        self._lbl_plan = QLabel(t("account.plan_none"))
        self._lbl_plan.setObjectName("planBadge")
        self._layout = layout
        layout.addWidget(self._btn_account)
        layout.addWidget(self._lbl_plan)

        self._update_account_chip()

    def add_right_widget(self, widget: QWidget) -> None:
        """Insert a widget before the user account chip."""
        idx = self._layout.indexOf(self._btn_account)
        if idx >= 0:
            self._layout.insertWidget(idx, widget)
        else:
            self._layout.addWidget(widget)

    def set_title(self, title: str, i18n_key: str | None = None) -> None:
        self._custom_title = title
        self._current_title_key = i18n_key
        self._lbl_title.setText(title)

    def set_core_connected(self, connected: bool) -> None:
        self._core_connected = connected
        if connected:
            self._lbl_dot.setPixmap(icon("status-dot", TOKENS.ACCENT_GREEN, size=12).pixmap(12, 12))
            self._lbl_status.setText(t("status.core_connected"))
            self._lbl_status.setStyleSheet(f"color: {TOKENS.ACCENT_GREEN};")
        else:
            self._lbl_dot.setPixmap(icon("status-dot", TOKENS.ACCENT_RED, size=12).pixmap(12, 12))
            self._lbl_status.setText(t("status.core_disconnected"))
            self._lbl_status.setStyleSheet(f"color: {TOKENS.ACCENT_RED};")

    def set_account_info(self, email: str | None, plan: str | None) -> None:
        self._email = email
        self._plan = plan
        self._update_account_chip()

    def _update_account_chip(self) -> None:
        display_email = self._email or t("account.not_signed_in")
        self._btn_account.setText(display_email)
        plan_key = f"account.plan_{self._plan.lower()}" if self._plan else "account.plan_none"
        self._lbl_plan.setText(t(plan_key))

    def retranslate(self) -> None:
        if self._current_title_key:
            self._lbl_title.setText(t(self._current_title_key))
        elif self._custom_title:
            self._lbl_title.setText(self._custom_title)
        self.set_core_connected(self._core_connected)
        self._update_account_chip()
