from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from apps.ui.design import TOKENS, asset_path, icon
from apps.ui.i18n import t

NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("nav.overview", "nav-overview"),
    ("nav.deriv", "nav-deriv"),
    ("nav.iqoption", "nav-iqoption"),
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


class Sidebar(QFrame):
    """Vertical brand sidebar with logo, navigation buttons, and footer tagline."""

    page_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)
        self.setStyleSheet(
            f"""
            QFrame#Sidebar {{
                background-color: {TOKENS.BG_CARD};
                border: none;
                border-right: 1px solid {TOKENS.BORDER_COLOR};
            }}
            QToolButton#navItem {{
                background-color: transparent;
                color: {TOKENS.TEXT_SECONDARY};
                border: none;
                border-left: 3px solid transparent;
                border-radius: 0px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: 600;
                text-align: left;
            }}
            QToolButton#navItem:hover {{
                background-color: {TOKENS.BG_SURFACE};
                color: {TOKENS.TEXT_PRIMARY};
            }}
            QToolButton#navItem:checked {{
                background-color: {TOKENS.BG_ELEVATED};
                color: {TOKENS.TEXT_PRIMARY};
                border-left: 3px solid {TOKENS.ACCENT_PRIMARY};
            }}
            QLabel#hint {{
                color: {TOKENS.TEXT_MUTED};
                font-size: 11px;
                font-style: italic;
                padding: 16px 20px;
                border-top: 1px solid {TOKENS.BORDER_COLOR};
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 24, 0, 0)
        layout.setSpacing(4)

        # 1. Brand Logo Header
        self._lbl_logo = QLabel()
        self._lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_logo.setContentsMargins(20, 0, 20, 24)
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
            btn.setFixedHeight(44)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setText(t(key))
            btn.setIcon(icon(icon_name, TOKENS.TEXT_SECONDARY, size=20))
            btn.clicked.connect(lambda _, page_idx=idx: self._on_button_clicked(page_idx))

            self._btn_group.addButton(btn, idx)
            self._nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        # 3. Footer Tagline
        self._lbl_tagline = QLabel(t("brand.tagline"))
        self._lbl_tagline.setObjectName("hint")
        self._lbl_tagline.setWordWrap(True)
        self._lbl_tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._lbl_tagline)

        # Default select first page
        if self._nav_buttons:
            self._nav_buttons[0].setChecked(True)
            self._update_button_icons(0)

    def _on_button_clicked(self, index: int) -> None:
        self._update_button_icons(index)
        self.page_selected.emit(index)

    def _update_button_icons(self, active_index: int) -> None:
        for idx, (btn, (_, icon_name)) in enumerate(
            zip(self._nav_buttons, NAV_ITEMS, strict=False)
        ):
            color = TOKENS.TEXT_PRIMARY if idx == active_index else TOKENS.TEXT_SECONDARY
            btn.setIcon(icon(icon_name, color, size=20))

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
        self._lbl_tagline.setText(t("brand.tagline"))
