from __future__ import annotations

from typing import Protocol

from packages.domain.market import MarketHistoryBatch
from packages.market_data import ClosedCandle


class DerivClosedCandlePort(Protocol):
    def convert(self, payload: object) -> ClosedCandle | None: ...


class DerivCandleHistorySource(Protocol):
    def market_history_batch(
        self,
        symbol: str,
        *,
        style: str,
        count: int = 100,
        timeframe_seconds: int | None = None,
        end_epoch: int | None = None,
    ) -> MarketHistoryBatch: ...
