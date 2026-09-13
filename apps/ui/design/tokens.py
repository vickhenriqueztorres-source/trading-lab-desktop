from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tokens:
    BG_ROOT: str = "#0A0F14"
    BG_CARD: str = "#101820"
    BG_SURFACE: str = "#16212B"
    BG_ELEVATED: str = "#1C2A36"

    BORDER_COLOR: str = "#1F2D3A"
    BORDER_ACCENT: str = "#2C3E4E"
    BORDER_HOVER: str = "#3A8FA3"

    ACCENT_PRIMARY: str = "#3AA7B8"
    ACCENT_GREEN: str = "#1FB57A"
    ACCENT_RED: str = "#E5484D"
    ACCENT_AMBER: str = "#D9A21B"

    TEXT_PRIMARY: str = "#E8EEF2"
    TEXT_SECONDARY: str = "#93A4B3"
    TEXT_MUTED: str = "#5F7080"

    FONT_MAIN: str = '"Segoe UI"'
    FONT_MONO: str = '"Consolas"'

    RADIUS_SM: int = 6
    RADIUS_MD: int = 10
    RADIUS_LG: int = 14

    SPACE_1: int = 8
    SPACE_2: int = 16
    SPACE_3: int = 24
    SPACE_4: int = 32


TOKENS = Tokens()
