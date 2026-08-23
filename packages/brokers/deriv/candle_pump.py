from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from packages.brokers.deriv.candle_adapter import DerivCandleIngressBridge
from packages.brokers.deriv.contracts import DerivCandleHistorySource
from packages.domain.canonical import canonical_bytes
from packages.domain.market import MarketCandle
from packages.domain.models import Broker
from packages.market_data import CandleIngressResult, CandleIngressStatus

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class DerivCandlePumpError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class DerivCandlePumpReport:
    response_message_id: str
    correlation_id: str
    causation_id: str
    symbol: str
    timeframe_seconds: int
    requested_count: int
    received_count: int
    partial_count: int
    ingress_results: tuple[CandleIngressResult, ...]

    @property
    def accepted_count(self) -> int:
        return sum(result.status is CandleIngressStatus.ACCEPTED for result in self.ingress_results)

    @property
    def duplicate_count(self) -> int:
        return sum(
            result.status is CandleIngressStatus.DUPLICATE for result in self.ingress_results
        )

    @property
    def has_quality_failure(self) -> bool:
        return any(
            result.status in {CandleIngressStatus.OUT_OF_ORDER, CandleIngressStatus.INVALID}
            for result in self.ingress_results
        )


def _epoch_ms(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DerivCandlePumpError("DERIV_CANDLE_TIMESTAMP_INVALID")
    delta = value.astimezone(UTC) - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


class DerivCandleHistoryPump:
    """Bounded read-only IPC history pump. It cannot evaluate strategy or dispatch orders."""

    def __init__(
        self,
        source: DerivCandleHistorySource,
        bridge: DerivCandleIngressBridge,
        *,
        max_batch_size: int = 500,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_batch_size <= 0:
            raise ValueError("Deriv candle pump batch limit must be positive")
        self._source = source
        self._bridge = bridge
        self._max_batch_size = max_batch_size
        self._now = now

    def backfill(
        self,
        symbol: str,
        timeframe_seconds: int,
        *,
        count: int,
        end_epoch: int | None = None,
    ) -> DerivCandlePumpReport:
        if not symbol.strip() or timeframe_seconds <= 0:
            raise ValueError("Deriv candle stream identity is required")
        if count <= 0 or count > self._max_batch_size:
            raise DerivCandlePumpError("DERIV_CANDLE_BACKPRESSURE")
        if end_epoch is not None and end_epoch <= 0:
            raise ValueError("Deriv candle history end epoch must be positive")
        batch = self._source.market_history_batch(
            symbol,
            style="candles",
            count=count,
            timeframe_seconds=timeframe_seconds,
            end_epoch=end_epoch,
        )
        if batch.ticks:
            raise DerivCandlePumpError("DERIV_CANDLE_HISTORY_MIXED_PAYLOAD")
        candles = batch.candles
        if len(candles) > count or len(candles) > self._max_batch_size:
            raise DerivCandlePumpError("DERIV_CANDLE_BATCH_OVERFLOW")
        received_at_ms = _epoch_ms(self._now())
        partial_count = 0
        results: list[CandleIngressResult] = []
        for candle in candles:
            payload = self._adapter_payload(
                candle,
                expected_symbol=symbol,
                expected_timeframe=timeframe_seconds,
                received_at_ms=received_at_ms,
                response_message_id=batch.response_message_id,
                correlation_id=batch.correlation_id,
                causation_id=batch.causation_id,
            )
            try:
                result = self._bridge.ingest(payload)
            except (TypeError, ValueError) as exc:
                raise DerivCandlePumpError("DERIV_CANDLE_INVALID") from exc
            if result is None:
                partial_count += 1
            else:
                results.append(result)
        return DerivCandlePumpReport(
            response_message_id=batch.response_message_id,
            correlation_id=batch.correlation_id,
            causation_id=batch.causation_id,
            symbol=symbol,
            timeframe_seconds=timeframe_seconds,
            requested_count=count,
            received_count=len(candles),
            partial_count=partial_count,
            ingress_results=tuple(results),
        )

    @staticmethod
    def _adapter_payload(
        candle: MarketCandle,
        *,
        expected_symbol: str,
        expected_timeframe: int,
        received_at_ms: int,
        response_message_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> dict[str, object]:
        if not isinstance(candle, MarketCandle):
            raise DerivCandlePumpError("DERIV_CANDLE_HISTORY_ITEM_INVALID")
        if (
            candle.broker is not Broker.DERIV
            or candle.broker_symbol != expected_symbol
            or candle.timeframe_seconds != expected_timeframe
        ):
            raise DerivCandlePumpError("DERIV_CANDLE_HISTORY_SCOPE_MISMATCH")
        open_time_ms = _epoch_ms(candle.open_time)
        close_time_ms = _epoch_ms(candle.close_time)
        if open_time_ms % 1_000 != 0 or close_time_ms != (
            open_time_ms + expected_timeframe * 1_000
        ):
            raise DerivCandlePumpError("DERIV_CANDLE_TIMESTAMP_INVALID")
        identity = {
            "broker": candle.broker.value,
            "close": format(candle.close, "f"),
            "epoch": open_time_ms // 1_000,
            "granularity": candle.timeframe_seconds,
            "high": format(candle.high, "f"),
            "is_closed": candle.is_closed,
            "low": format(candle.low, "f"),
            "open": format(candle.open, "f"),
            "symbol": candle.broker_symbol,
        }
        candle_digest = hashlib.sha256(canonical_bytes(identity)).hexdigest()
        source_event_id = "|".join(
            (response_message_id, correlation_id, causation_id, candle_digest)
        )
        return {
            "symbol": candle.broker_symbol,
            "granularity": candle.timeframe_seconds,
            "epoch": open_time_ms // 1_000,
            "open": format(candle.open, "f"),
            "high": format(candle.high, "f"),
            "low": format(candle.low, "f"),
            "close": format(candle.close, "f"),
            "is_closed": candle.is_closed,
            "source_event_id": source_event_id,
            "received_at_ms": received_at_ms,
        }
