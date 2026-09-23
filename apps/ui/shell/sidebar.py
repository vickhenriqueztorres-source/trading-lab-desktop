from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from apps.ui.design import TOKENS, asset_path, icon
from apps.ui.i18n import t

NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("nav.overview", "nav-overview"),
    ("nav.deriv", "logo-deriv-official"),
    ("nav.iqoption", "logo-iqoption-official"),
    ("nav.activity", "nav-activity"),
    ("nav.account", "nav-account"),
    ("nav.settings", "nav-settings"),
)


def _render_logo_pixmap(width: int, height: int) -> QPixmap:
    logo_file = asset_path("logo-wordmark.svg")
    if not logo_file.is_file():
        pix = QPixmap(width, height)
        pix.fill(Qt.GlobalColor.transparent)
        return pix

    app = QGuiApplication.instance()
    dpr = 1.0
    if app is not None and isinstance(app, QGuiApplication):
        screen = app.primaryScreen()
        if screen is not None:
            dpr = screen.devicePixelRatio()

    pix = QPixmap(int(width * dpr), int(height * dpr))
    pix.fill(Qt.GlobalColor.transparent)
    pix.setDevicePixelRatio(dpr)

    svg_data = QByteArray(logo_file.read_bytes())
    renderer = QSvgRenderer(svg_data)
    if renderer.isValid():
        painter = QPainter(pix)
        renderer.render(painter, QRectF(0, 0, width, height))
        painter.end()
    return pix


class UserProfileCard(QFrame):
    """Premium profile card with avatar, client name, and exclusive plan seal."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("userProfileCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._user_name: str = "Trader"
        self._plan: str = "PRO"
        self._is_compact: bool = False

        card_layout = QHBoxLayout(self)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(10)

        # 1. Avatar Circle
        self._avatar = QLabel()
        self._avatar.setFixedSize(36, 36)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._avatar)

        # 2. Details (Name + Seal)
        details_layout = QVBoxLayout()
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(2)

        self._lbl_name = QLabel()
        self._lbl_name.setStyleSheet(
            f"color: {TOKENS.TEXT_PRIMARY}; font-size: 13px; font-weight: 700; "
            'font-family: "Segoe UI", sans-serif;'
        )
        details_layout.addWidget(self._lbl_name)

        # Plan Seal Badge
        self._lbl_badge = QLabel()
        self._lbl_badge.setFixedHeight(18)
        details_layout.addWidget(self._lbl_badge)

        card_layout.addLayout(details_layout)
        card_layout.addStretch()

        self._render_profile()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is not None:
            super().mousePressEvent(event)
        self.clicked.emit()

    def set_user_info(self, name: str | None, plan: str | None) -> None:
        clean_name = str(name).strip() if name else ""
        if "@" in clean_name:
            parts = clean_name.split("@")[0].replace(".", " ").replace("_", " ").title()
            self._user_name = parts if parts else clean_name
        elif clean_name:
            self._user_name = clean_name
        else:
            self._user_name = "Trader"

        self._plan = str(plan).strip().upper() if plan else "PRO"
        self._render_profile()

    def set_compact_mode(self, is_compact: bool) -> None:
        self._is_compact = is_compact
        if is_compact:
            self.layout().setContentsMargins(6, 6, 6, 6)
            self._avatar.setFixedSize(30, 30)
        else:
            self.layout().setContentsMargins(10, 8, 10, 8)
            self._avatar.setFixedSize(36, 36)
        self._render_profile()

    def _render_profile(self) -> None:
        plan_upper = self._plan.upper()
        initial = (self._user_name[:1] or "T").upper()
        self._avatar.setText(initial)

        display_name = self._user_name
        max_len = 10 if self._is_compact else 14
        if len(display_name) > max_len:
            display_name = display_name[:max_len] + "..."
        self._lbl_name.setText(display_name)

        if (
            "TRIAL" in plan_upper
            or "TEST" in plan_upper
            or "DEMO" in plan_upper
            or "PHASE0" in plan_upper
        ):
            # ⚡ TRIAL SEAL (Amber / Gold)
            accent = "#F59E0B"
            glow_bg = "rgba(245, 158, 11, 0.15)"
            badge_text = "⚡ TRIAL"
            badge_style = (
                f"background-color: rgba(245, 158, 11, 0.14); color: {accent}; "
                f"border: 1px solid rgba(245, 158, 11, 0.45); border-radius: 4px; "
                f"padding: 1px 6px; font-size: 10px; font-weight: 800; letter-spacing: 0.5px;"
            )
            card_border = "rgba(245, 158, 11, 0.25)"
        elif "DIAMOND" in plan_upper or "VIP" in plan_upper or "DIAMANTE" in plan_upper:
            # 💎 DIAMOND SEAL (Electric Cyan / Diamond)
            accent = "#00F2FE"
            glow_bg = "rgba(0, 242, 254, 0.15)"
            badge_text = "💎 DIAMOND"
            badge_style = (
                f"background-color: rgba(0, 242, 254, 0.15); color: {accent}; "
                f"border: 1px solid rgba(0, 242, 254, 0.55); border-radius: 4px; "
                f"padding: 1px 6px; font-size: 10px; font-weight: 800; letter-spacing: 0.5px;"
            )
            card_border = "rgba(0, 242, 254, 0.3)"
        else:
            # ⭐ PRO SEAL (Emerald Green - Default)
            accent = "#1FB57A"
            glow_bg = "rgba(31, 181, 122, 0.15)"
            badge_text = "⭐ PRO"
            badge_style = (
                f"background-color: rgba(31, 181, 122, 0.14); color: {accent}; "
                f"border: 1px solid rgba(31, 181, 122, 0.45); border-radius: 4px; "
                f"padding: 1px 6px; font-size: 10px; font-weight: 800; letter-spacing: 0.5px;"
            )
            card_border = "rgba(31, 181, 122, 0.25)"

        r_radius = 15 if self._is_compact else 18
        self._avatar.setStyleSheet(
            f"background-color: {glow_bg}; color: {accent}; "
            f"border: 2px solid {accent}; border-radius: {r_radius}px; "
            f"font-size: {'12px' if self._is_compact else '14px'}; font-weight: 800;"
        )
        self._lbl_badge.setText(badge_text)
        self._lbl_badge.setStyleSheet(badge_style)
        self._lbl_badge.adjustSize()

        self.setStyleSheet(
            f"""
            QFrame#userProfileCard {{
                background-color: #0E1724;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                margin: 4px 8px 12px 8px;
            }}
            QFrame#userProfileCard:hover {{
                background-color: #142132;
                border: 1px solid {card_border};
            }}
            """
        )


class Sidebar(QFrame):
    """Vertical brand sidebar with logo, navigation buttons, and footer profile card."""

    page_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)
        self._compact_mode = False
        self.setStyleSheet(
            f"""
            QFrame#Sidebar {{
                background-color: {TOKENS.BG_CARD};
                border: none;
                border-right: 1px solid {TOKENS.BORDER_COLOR};
            }}
            QToolButton#navItem {{
                background-color: transparent;
                color: #8E9CA8;
                border: 1px solid transparent;
                border-left: 4px solid transparent;
                border-radius: 6px;
                padding: 0 14px;
                font-size: 14px;
                font-weight: 600;
                font-family: "Segoe UI", sans-serif;
                text-align: left;
                margin: 2px 8px;
            }}
            QToolButton#navItem:hover {{
                background-color: {TOKENS.BG_SURFACE};
                color: {TOKENS.TEXT_PRIMARY};
                border: 1px solid #1E2D3D;
                border-left: 4px solid #3A506B;
            }}
            QToolButton#navItem:checked {{
                background-color: {TOKENS.BG_ELEVATED};
                color: #FFFFFF;
                font-weight: 700;
                border: 1px solid rgba(0, 242, 254, 0.25);
                border-left: 4px solid {TOKENS.ACCENT_PRIMARY};
            }}
            QLabel#hint {{
                color: {TOKENS.TEXT_MUTED};
                font-size: 10px;
                font-weight: 700;
                padding: 16px 14px;
                border-top: 1px solid {TOKENS.BORDER_COLOR};
                letter-spacing: 0.5px;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.setSpacing(4)

        # 1. Brand Logo Header
        self._lbl_logo = QLabel()
        self._lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_logo.setContentsMargins(16, 0, 16, 20)
        self._lbl_logo.setPixmap(_render_logo_pixmap(160, 32))
        layout.addWidget(self._lbl_logo)

        # 2. Nav Items Group
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)
        self._nav_buttons: list[QToolButton] = []

        for idx, (key, icon_name) in enumerate(NAV_ITEMS):
            btn = QToolButton()
            btn.setObjectName("navItem")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setFixedHeight(48)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setText(t(key))
            # Load icon with generous 22px size
            btn.setIcon(icon(icon_name, TOKENS.TEXT_SECONDARY, size=22))
            btn.clicked.connect(lambda _, page_idx=idx: self._on_button_clicked(page_idx))

            self._btn_group.addButton(btn, idx)
            self._nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        # 3. Footer Profile Card
        self._profile_card = UserProfileCard(self)
        self._profile_card.clicked.connect(lambda: self.page_selected.emit(4))
        layout.addWidget(self._profile_card)

        # Default select first page
        if self._nav_buttons:
            self._nav_buttons[0].setChecked(True)
            self._update_button_icons(0)

    def set_compact_mode(self, is_compact: bool) -> None:
        if self._compact_mode == is_compact:
            return
        self._compact_mode = is_compact
        target_width = 175 if is_compact else 220
        self.setFixedWidth(target_width)
        self._profile_card.set_compact_mode(is_compact)

    def set_account_info(self, name: str | None, plan: str | None) -> None:
        """Update client display name and subscription seal in the profile card."""
        self._profile_card.set_user_info(name, plan)

    def _on_button_clicked(self, index: int) -> None:
        self._update_button_icons(index)
        self.page_selected.emit(index)

    def _update_button_icons(self, active_index: int) -> None:
        for idx, (btn, (_, icon_name)) in enumerate(
            zip(self._nav_buttons, NAV_ITEMS, strict=False)
        ):
            color = TOKENS.TEXT_PRIMARY if idx == active_index else TOKENS.TEXT_SECONDARY
            btn.setIcon(icon(icon_name, color, size=22))

    def set_current_page(self, index: int) -> None:
        """Programmatically select navigation page without re-emitting if already selected."""
        if 0 <= index < len(self._nav_buttons):
            btn = self._nav_buttons[index]
            if not btn.isChecked():
                btn.setChecked(True)
                self._update_button_icons(index)

    def retranslate(self) -> None:
        for (key, _), btn in zip(NAV_ITEMS, self._nav_buttons, strict=False):
            btn.setText(t(key))
