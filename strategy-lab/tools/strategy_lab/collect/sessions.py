"""Market session calendar used by gap classification (R-COL-7)."""

from __future__ import annotations


def minute_of_day(ts: int) -> int:
    return (ts % 86400) // 60


def weekday_utc(ts: int) -> int:
    return (ts // 86400 + 3) % 7


def in_session(asset: str, ts: int) -> bool:
    weekday = weekday_utc(ts)
    minute = minute_of_day(ts)
    if asset.endswith("-OTC"):
        return weekday in {5, 6}
    if weekday in {0, 1, 2, 3}:
        return True
    if weekday == 4:
        return minute < 21 * 60
    return False
