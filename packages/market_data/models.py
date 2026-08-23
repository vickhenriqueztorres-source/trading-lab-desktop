from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from packages.domain.canonical import canonical_bytes
from packages.domain.models import Broker


def _required_int(payload: dict[str, object], field_name: str) -> int:
    value = payload.get(field_name)
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    return value


def _required_str(payload: dict[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class CandleEnvelope:
    """Strictly validated transport-neutral input before it becomes a closed candle."""

    broker: Broker
    symbol: str
    timeframe_seconds: int
    open_time_ms: int
    close_time_ms: int
    open_units: int
    high_units: int
    low_units: int
    close_units: int
    price_scale: int
    is_closed: bool
    source: str
    source_event_id: str
    source_timestamp_ms: int
    received_timestamp_ms: int

    @classmethod
    def from_closed_candle(cls, candle: ClosedCandle) -> Self:
        return cls(
            broker=candle.broker,
            symbol=candle.symbol,
            timeframe_seconds=candle.timeframe_seconds,
            open_time_ms=candle.open_time_ms,
            close_time_ms=candle.close_time_ms,
            open_units=candle.open_units,
            high_units=candle.high_units,
            low_units=candle.low_units,
            close_units=candle.close_units,
            price_scale=candle.price_scale,
            is_closed=True,
            source=candle.source,
            source_event_id=candle.source_event_id,
            source_timestamp_ms=candle.source_timestamp_ms,
            received_timestamp_ms=candle.received_timestamp_ms,
        )

    @classmethod
    def from_external_payload(cls, raw: object) -> Self:
        if not isinstance(raw, dict):
            raise ValueError("candle payload must be an object")
        payload: dict[str, object] = raw
        required = {
            "broker",
            "symbol",
            "timeframe_seconds",
            "open_time_ms",
            "close_time_ms",
            "open_units",
            "high_units",
            "low_units",
            "close_units",
            "price_scale",
            "is_closed",
            "source",
            "source_event_id",
            "source_timestamp_ms",
            "received_timestamp_ms",
        }
        if set(payload) != required:
            raise ValueError("candle payload schema is invalid")
        closed = payload.get("is_closed")
        if type(closed) is not bool:
            raise ValueError("is_closed must be a boolean")
        return cls(
            broker=Broker(_required_str(payload, "broker")),
            symbol=_required_str(payload, "symbol"),
            timeframe_seconds=_required_int(payload, "timeframe_seconds"),
            open_time_ms=_required_int(payload, "open_time_ms"),
            close_time_ms=_required_int(payload, "close_time_ms"),
            open_units=_required_int(payload, "open_units"),
            high_units=_required_int(payload, "high_units"),
            low_units=_required_int(payload, "low_units"),
            close_units=_required_int(payload, "close_units"),
            price_scale=_required_int(payload, "price_scale"),
            is_closed=closed,
            source=_required_str(payload, "source"),
            source_event_id=_required_str(payload, "source_event_id"),
            source_timestamp_ms=_required_int(payload, "source_timestamp_ms"),
            received_timestamp_ms=_required_int(payload, "received_timestamp_ms"),
        )


@dataclass(frozen=True, slots=True)
class ClosedCandle:
    broker: Broker
    symbol: str
    timeframe_seconds: int
    open_time_ms: int
    close_time_ms: int
    open_units: int
    high_units: int
    low_units: int
    close_units: int
    price_scale: int
    source: str
    source_event_id: str
    source_timestamp_ms: int
    received_timestamp_ms: int
    candle_id: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in ("symbol", "source", "source_event_id"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty")
        if self.timeframe_seconds <= 0 or self.price_scale <= 0:
            raise ValueError("timeframe and price scale must be positive")
        if self.open_time_ms < 0 or self.close_time_ms <= self.open_time_ms:
            raise ValueError("candle timestamps are invalid")
        if self.close_time_ms - self.open_time_ms != self.timeframe_seconds * 1_000:
            raise ValueError("candle duration does not match timeframe")
        if self.source_timestamp_ms < 0 or self.received_timestamp_ms < 0:
            raise ValueError("source and received timestamps cannot be negative")
        prices = (self.open_units, self.high_units, self.low_units, self.close_units)
        if any(type(price) is not int or price <= 0 for price in prices):
            raise ValueError("candle prices must be positive integers")
        if not (
            self.low_units <= self.open_units <= self.high_units
            and self.low_units <= self.close_units <= self.high_units
        ):
            raise ValueError("candle OHLC range is inconsistent")
        object.__setattr__(self, "candle_id", self.compute_candle_id())

    @property
    def series_key(self) -> tuple[Broker, str, int]:
        return (self.broker, self.symbol, self.timeframe_seconds)

    def compute_candle_id(self) -> str:
        identity: dict[str, object] = {
            "broker": self.broker.value,
            "close_time_ms": self.close_time_ms,
            "close_units": self.close_units,
            "high_units": self.high_units,
            "low_units": self.low_units,
            "open_time_ms": self.open_time_ms,
            "open_units": self.open_units,
            "price_scale": self.price_scale,
            "symbol": self.symbol,
            "timeframe_seconds": self.timeframe_seconds,
        }
        return hashlib.sha256(canonical_bytes(identity)).hexdigest()

    def decimal_prices(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        scale = Decimal(self.price_scale)
        return (
            Decimal(self.open_units) / scale,
            Decimal(self.high_units) / scale,
            Decimal(self.low_units) / scale,
            Decimal(self.close_units) / scale,
        )

    @property
    def price_units(self) -> tuple[int, int, int, int]:
        return (self.open_units, self.high_units, self.low_units, self.close_units)

    @classmethod
    def from_envelope(cls, envelope: CandleEnvelope) -> Self:
        if not envelope.is_closed:
            raise ValueError("open candle cannot enter the closed candle domain")
        return cls(
            broker=envelope.broker,
            symbol=envelope.symbol,
            timeframe_seconds=envelope.timeframe_seconds,
            open_time_ms=envelope.open_time_ms,
            close_time_ms=envelope.close_time_ms,
            open_units=envelope.open_units,
            high_units=envelope.high_units,
            low_units=envelope.low_units,
            close_units=envelope.close_units,
            price_scale=envelope.price_scale,
            source=envelope.source,
            source_event_id=envelope.source_event_id,
            source_timestamp_ms=envelope.source_timestamp_ms,
            received_timestamp_ms=envelope.received_timestamp_ms,
        )
