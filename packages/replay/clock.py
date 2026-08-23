from __future__ import annotations

from datetime import datetime
from typing import Protocol

from packages.market_data import datetime_from_epoch_ms


class ReplayClockPort(Protocol):
    def now(self) -> datetime: ...

    def now_ms(self) -> int: ...

    def advance_to_ms(self, epoch_ms: int) -> datetime: ...


class ReplayClock:
    def __init__(self, initial_epoch_ms: int) -> None:
        self._epoch_ms = initial_epoch_ms
        datetime_from_epoch_ms(initial_epoch_ms)

    def now(self) -> datetime:
        return datetime_from_epoch_ms(self._epoch_ms)

    def now_ms(self) -> int:
        return self._epoch_ms

    def advance_to_ms(self, epoch_ms: int) -> datetime:
        if epoch_ms < self._epoch_ms:
            raise ValueError("replay clock cannot move backwards")
        self._epoch_ms = epoch_ms
        return self.now()
