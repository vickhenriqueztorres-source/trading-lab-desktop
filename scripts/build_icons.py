"""Build multi-resolution Windows app.ico from brand SVG asset."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure offscreen Qt platform for headless execution
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # type: ignore[import-untyped]
from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer


def build_app_icon() -> Path:
    project_root = Path(__file__).resolve().parent.parent
    assets_dir = project_root / "apps" / "ui" / "assets"
    svg_path = assets_dir / "logo-mark.svg"
    output_path = assets_dir / "app.ico"

    if not svg_path.is_file():
        raise FileNotFoundError(f"Source SVG not found: {svg_path}")

    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv)

    svg_data = QByteArray(svg_path.read_bytes())
    renderer = QSvgRenderer(svg_data)
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG asset: {svg_path}")

    sizes = [16, 32, 48, 256]
    pil_images: list[Image.Image] = []

    for size in sizes:
        qimage = QImage(size, size, QImage.Format.Format_ARGB32)
        qimage.fill(0)  # Transparent

        painter = QPainter(qimage)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # Draw dark rounded squircle background as in design system APP ICON
        radius = max(2.0, size * 0.20)
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
        painter.fillPath(bg_path, QColor("#070B14"))

        # Render logo mark with 15% padding
        pad = max(1.0, size * 0.14)
        mark_rect = QRectF(pad, pad, size - (2 * pad), size - (2 * pad))
        renderer.render(painter, mark_rect)
        painter.end()

        # Convert QImage to PIL Image
        ptr = qimage.constBits()
        bytes_per_line = qimage.bytesPerLine()
        buf = bytes(ptr[0 : bytes_per_line * size])
        pil_img = Image.frombuffer(
            "RGBA",
            (size, size),
            buf,
            "raw",
            "BGRA",
            bytes_per_line,
            1,
        )
        pil_images.append(pil_img)

    # Save multi-resolution ICO (256x256 base + 16, 32, 48 appended)
    base_img = pil_images[-1]
    extra_imgs = pil_images[:-1]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_img.save(
        output_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=extra_imgs,
    )
    print(f"Generated {output_path} with sizes: {sizes}")
    return output_path


if __name__ == "__main__":
    build_app_icon()
