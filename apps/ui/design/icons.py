from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from apps.ui.design.tokens import TOKENS

_ICON_CACHE: dict[tuple[str, str, int], QIcon] = {}


def asset_path(name: str) -> Path:
    """Resolve an asset path for development and frozen runtime."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate = Path(meipass) / "apps" / "ui" / "assets" / name
            if candidate.is_file():
                return candidate
        exe_dir = Path(sys.executable).resolve().parent
        candidate = exe_dir / "apps" / "ui" / "assets" / name
        if candidate.is_file():
            return candidate
        candidate = exe_dir / "assets" / name
        if candidate.is_file():
            return candidate

    dev_path = Path(__file__).resolve().parent.parent / "assets" / name
    return dev_path


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    """Load an SVG icon, replace currentColor with token color, and return QIcon.

    Results are cached in memory by (name, color, size).
    """
    effective_color = color if color is not None else TOKENS.TEXT_SECONDARY
    cache_key = (name, effective_color, size)
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached

    filename = name if name.endswith((".svg", ".ico", ".png")) else f"{name}.svg"
    path = asset_path(filename)
    if not path.is_file():
        empty_icon = QIcon()
        _ICON_CACHE[cache_key] = empty_icon
        return empty_icon

    if not filename.endswith(".svg"):
        loaded_icon = QIcon(str(path))
        _ICON_CACHE[cache_key] = loaded_icon
        return loaded_icon

    svg_text = path.read_text(encoding="utf-8")
    if "currentColor" in svg_text:
        svg_text = svg_text.replace("currentColor", effective_color)
    svg_bytes = svg_text.encode("utf-8")

    app = QGuiApplication.instance()
    dpr = 1.0
    if app is not None and isinstance(app, QGuiApplication):
        screen = app.primaryScreen()
        if screen is not None:
            dpr = screen.devicePixelRatio()

    pixel_size = max(1, int(round(size * dpr)))
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)

    renderer = QSvgRenderer(QByteArray(svg_bytes))
    if renderer.isValid():
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()

    rendered_icon = QIcon(pixmap)
    _ICON_CACHE[cache_key] = rendered_icon
    return rendered_icon
