from __future__ import annotations

import threading
from collections import defaultdict
from enum import StrEnum
from typing import Protocol

from packages.domain.models import Broker
from packages.market_data.models import ClosedCandle

SeriesKey = tuple[Broker, str, int]


class CandleStoreOutcome(StrEnum):
    STORED = "STORED"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    GAPPED = "GAPPED"


class CandleStoreFullError(RuntimeError):
    pass


class CandleStore(Protocol):
    def append(self, candle: ClosedCandle) -> CandleStoreOutcome: ...

    def contains(self, candle_id: str) -> bool: ...

    def exists(self, candle_id: str) -> bool: ...

    def range(
        self,
        series_key: SeriesKey,
        *,
        start_close_ms: int | None = None,
        end_close_ms: int | None = None,
    ) -> tuple[ClosedCandle, ...]: ...


class InMemoryCandleStore:
    def __init__(self, *, max_candles: int = 100_000) -> None:
        if max_candles <= 0:
            raise ValueError("max_candles must be positive")
        self._max_candles = max_candles
        self._lock = threading.Lock()
        self._ids: set[str] = set()
        self._series: dict[SeriesKey, list[ClosedCandle]] = defaultdict(list)

    def append(self, candle: ClosedCandle) -> CandleStoreOutcome:
        with self._lock:
            if candle.candle_id in self._ids:
                return CandleStoreOutcome.DUPLICATE
            candles = self._series[candle.series_key]
            if candles:
                last = candles[-1]
                if (
                    candle.close_time_ms <= last.close_time_ms
                    or candle.open_time_ms < last.close_time_ms
                ):
                    return CandleStoreOutcome.OUT_OF_ORDER
                if candle.open_time_ms != last.close_time_ms:
                    return CandleStoreOutcome.GAPPED
            if len(self._ids) >= self._max_candles:
                raise CandleStoreFullError("candle store capacity reached")
            candles.append(candle)
            self._ids.add(candle.candle_id)
            return CandleStoreOutcome.STORED

    def contains(self, candle_id: str) -> bool:
        with self._lock:
            return candle_id in self._ids

    def exists(self, candle_id: str) -> bool:
        return self.contains(candle_id)

    def range(
        self,
        series_key: SeriesKey,
        *,
        start_close_ms: int | None = None,
        end_close_ms: int | None = None,
    ) -> tuple[ClosedCandle, ...]:
        with self._lock:
            candles = tuple(self._series.get(series_key, ()))
        return tuple(
            candle
            for candle in candles
            if (start_close_ms is None or candle.close_time_ms >= start_close_ms)
            and (end_close_ms is None or candle.close_time_ms <= end_close_ms)
        )
