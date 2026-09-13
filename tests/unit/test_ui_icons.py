from __future__ import annotations

import os
import sys

# Offscreen Qt platform for testing
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # type: ignore[import-untyped]
from PySide6.QtGui import QGuiApplication

from apps.ui.design import TOKENS, asset_path, icon


def _get_qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv)
    assert isinstance(app, QGuiApplication)
    return app


def test_asset_path_resolution() -> None:
    mark_path = asset_path("logo-mark.svg")
    assert mark_path.is_file()
    assert mark_path.name == "logo-mark.svg"

    wordmark_path = asset_path("logo-wordmark.svg")
    assert wordmark_path.is_file()

    ico_path = asset_path("app.ico")
    assert ico_path.is_file()


def test_brand_assets_content() -> None:
    mark_content = asset_path("logo-mark.svg").read_text(encoding="utf-8")
    assert "#3AA7B8" in mark_content
    assert "viewBox" in mark_content
    assert "round" in mark_content

    wordmark_content = asset_path("logo-wordmark.svg").read_text(encoding="utf-8")
    assert "TRADING LAB" in wordmark_content
    assert "#E8EEF2" in wordmark_content
    assert "Segoe UI" in wordmark_content


def test_all_17_navigation_and_status_icons_exist() -> None:
    expected_icons = [
        "nav-overview.svg",
        "nav-deriv.svg",
        "nav-iqoption.svg",
        "nav-activity.svg",
        "nav-account.svg",
        "nav-settings.svg",
        "status-dot.svg",
        "icon-play.svg",
        "icon-stop.svg",
        "icon-search.svg",
        "icon-mail.svg",
        "icon-shield.svg",
        "icon-clock.svg",
        "icon-wifi.svg",
        "icon-check.svg",
        "icon-alert.svg",
        "icon-chevron-down.svg",
    ]
    for icon_name in expected_icons:
        p = asset_path(icon_name)
        assert p.is_file(), f"Missing icon asset: {icon_name}"
        content = p.read_text(encoding="utf-8")
        assert 'viewBox="0 0 24 24"' in content
        assert "currentColor" in content


def test_app_ico_multi_resolution() -> None:
    ico_path = asset_path("app.ico")
    assert ico_path.is_file()

    with Image.open(ico_path) as img:
        assert (256, 256) in img.info.get("sizes", set()) or img.size == (256, 256)
        sizes = img.info.get("sizes", {img.size})
        assert {(16, 16), (32, 32), (48, 48), (256, 256)}.issubset(sizes)


def test_icon_loader_and_cache() -> None:
    _get_qapp()

    ico1 = icon("nav-overview")
    assert not ico1.isNull()

    # Cache hit
    ico2 = icon("nav-overview")
    assert ico1 is ico2

    # Different color or size creates different cache entry
    ico3 = icon("nav-overview", color=TOKENS.ACCENT_PRIMARY, size=24)
    assert not ico3.isNull()
    assert ico1 is not ico3

    # Direct .ico loading
    ico_app = icon("app.ico")
    assert not ico_app.isNull()

    # Non-existent icon returns empty QIcon
    missing = icon("non-existent-icon-xyz")
    assert missing.isNull()
