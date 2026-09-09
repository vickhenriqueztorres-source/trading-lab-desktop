"""Market session calendar used by gap classification (R-COL-7)."""

from __future__ import annotations


def minute_of_day(ts: int) -> int:
    return (ts % 86400) // 60


def weekday_utc(ts: int) -> int:
    return (ts // 86400 + 3) % 7


def in_session(asset: str, ts: int) -> bool:
    weekday = weekday_utc(ts)
    minute = minute_of_day(ts)
    if asset == "EURUSD-OTC":
        # Broker api_option_init_all schedule observed 2026-09-08 for the
        # current EURUSD-OTC instrument: daily, excluding 08:00–08:30 UTC.
        # Other OTC symbols retain the conservative seed until independently
        # observed; one symbol's calendar must never be generalized silently.
        return minute < 8 * 60 or minute >= 8 * 60 + 30
    if asset.endswith("-OTC"):
        return weekday in {5, 6}
    if weekday in {0, 1, 2, 3}:
        return True
    if weekday == 4:
        return minute < 21 * 60
    return False
