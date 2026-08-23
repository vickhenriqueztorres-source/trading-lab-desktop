from __future__ import annotations

from packages.brokers.deriv.models import DerivCandleEvent
from packages.domain.models import Broker
from packages.market_data import CandleIngress, CandleIngressResult, ClosedCandle


def _scaled_prices(
    values: tuple[str, ...], *, max_decimal_places: int
) -> tuple[int, tuple[int, ...]]:
    decimal_places = max(len(value.partition(".")[2]) for value in values)
    if decimal_places > max_decimal_places:
        raise ValueError("Deriv candle price precision exceeds adapter policy")
    scale = 10**decimal_places
    units: list[int] = []
    for value in values:
        whole, separator, fraction = value.partition(".")
        padded = fraction.ljust(decimal_places, "0") if separator else "0" * decimal_places
        units.append(int(whole) * scale + (int(padded) if padded else 0))
    return scale, tuple(units)


class DerivCandleAdapter:
    """Pure read-only Deriv schema mapper. It has no transport or execution dependency."""

    def __init__(
        self,
        allowed_symbols: frozenset[str],
        *,
        max_decimal_places: int = 12,
    ) -> None:
        if not allowed_symbols or any(not symbol.strip() for symbol in allowed_symbols):
            raise ValueError("Deriv adapter requires an explicit symbol allowlist")
        if max_decimal_places <= 0:
            raise ValueError("max_decimal_places must be positive")
        self._allowed_symbols = allowed_symbols
        self._max_decimal_places = max_decimal_places

    def convert(self, payload: object) -> ClosedCandle | None:
        event = DerivCandleEvent.from_external_payload(payload)
        if event.symbol not in self._allowed_symbols:
            raise ValueError("DERIV_CANDLE_SYMBOL_NOT_ALLOWED")
        if not event.is_closed:
            return None
        open_time_ms = event.epoch_seconds * 1_000
        close_time_ms = open_time_ms + event.granularity_seconds * 1_000
        if event.received_at_ms < close_time_ms:
            raise ValueError("DERIV_CANDLE_CLOSE_NOT_CONFIRMED")
        price_scale, prices = _scaled_prices(
            event.price_texts,
            max_decimal_places=self._max_decimal_places,
        )
        open_units, high_units, low_units, close_units = prices
        return ClosedCandle(
            broker=Broker.DERIV,
            symbol=event.symbol,
            timeframe_seconds=event.granularity_seconds,
            open_time_ms=open_time_ms,
            close_time_ms=close_time_ms,
            open_units=open_units,
            high_units=high_units,
            low_units=low_units,
            close_units=close_units,
            price_scale=price_scale,
            source="DERIV_CANDLES_READ_ONLY",
            source_event_id=event.source_event_id,
            source_timestamp_ms=close_time_ms,
            received_timestamp_ms=event.received_at_ms,
        )


class DerivCandleIngressBridge:
    """Optional local bridge created only after adapter contract validation."""

    def __init__(self, adapter: DerivCandleAdapter, ingress: CandleIngress) -> None:
        self._adapter = adapter
        self._ingress = ingress

    def ingest(self, payload: object) -> CandleIngressResult | None:
        candle = self._adapter.convert(payload)
        return None if candle is None else self._ingress.ingest(candle)
