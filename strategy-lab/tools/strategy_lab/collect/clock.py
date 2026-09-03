"""Injectable UTC clock and NTP preflight (R-COL-1, I-2)."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


def utc_now_ts() -> int:
    return int(datetime.now(UTC).timestamp())


class Clock:
    def __init__(self, now: Callable[[], int] = utc_now_ts) -> None:
        self._now = now

    def now_ts(self) -> int:
        value = self._now()
        if type(value) is not int or value < 0:
            raise ClockError("CLOCK_INVALID")
        return value

    def check_ntp(
        self,
        max_skew_s: int = 5,
        *,
        server: str = "pool.ntp.org",
        ntp_request: Callable[[str], int] | None = None,
    ) -> None:
        if type(max_skew_s) is not int or max_skew_s < 0:
            raise ClockError("CLOCK_INVALID_SKEW")
        remote = ntp_request(server) if ntp_request is not None else _ntp_epoch(server)
        if abs(self.now_ts() - remote) > max_skew_s:
            raise ClockError("CLOCK_NTP_SKEW")


class ClockError(RuntimeError):
    pass


def _ntp_epoch(server: str) -> int:
    try:
        ntplib: Any = importlib.import_module("ntplib")
        response = ntplib.NTPClient().request(server, version=3, timeout=5)
        return int(response.tx_time)
    except Exception:
        raise ClockError("CLOCK_NTP_UNAVAILABLE") from None
