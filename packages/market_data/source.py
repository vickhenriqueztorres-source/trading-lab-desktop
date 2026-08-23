from __future__ import annotations

from typing import Protocol

from packages.market_data.models import CandleEnvelope


class CandleSource(Protocol):
    def read(self) -> tuple[CandleEnvelope, ...]: ...


class FakeCandleSource:
    def __init__(self, candles: tuple[CandleEnvelope, ...], *, max_candles: int = 100_000) -> None:
        if len(candles) > max_candles:
            raise ValueError("fake candle source capacity exceeded")
        self._candles = candles

    def read(self) -> tuple[CandleEnvelope, ...]:
        return self._candles
