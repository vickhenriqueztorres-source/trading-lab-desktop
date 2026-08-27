from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from packages.domain.models import Broker, require_aware_utc


class BrokerConnectionMode(StrEnum):
    PUBLIC_READ_ONLY = "PUBLIC_READ_ONLY"
    DEMO_AUTH_READ_ONLY = "DEMO_AUTH_READ_ONLY"
    REAL_AUTH_READ_ONLY = "REAL_AUTH_READ_ONLY"


class MarketDataHealthState(StrEnum):
    HEALTHY = "HEALTHY"
    WARMING_UP = "WARMING_UP"
    STALE = "STALE"
    GAPPED = "GAPPED"
    DISCONNECTED = "DISCONNECTED"
    INCOMPATIBLE = "INCOMPATIBLE"


class BrokerClockHealth(StrEnum):
    UNSYNCHRONIZED = "UNSYNCHRONIZED"
    HEALTHY = "HEALTHY"
    STALE = "STALE"


def _required_str(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _required_int(payload: Mapping[str, object], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{name} must use a decimal string")
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise ValueError(f"{name} is not a decimal") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise ValueError(f"{name} is outside the valid decimal range")
    return parsed


@dataclass(frozen=True, slots=True)
class BrokerCapabilities:
    broker: Broker
    connection_mode: BrokerConnectionMode
    authenticated: bool
    can_trade: bool
    supports_ticks: bool
    supports_tick_history: bool
    supports_candles: bool
    supports_active_symbols: bool
    supports_contract_metadata: bool
    supports_server_time: bool
    supported_timeframes: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.can_trade:
            raise ValueError("Prompt 6 broker capabilities cannot authorize trading")
        if any(value <= 0 for value in self.supported_timeframes):
            raise ValueError("supported timeframes must be positive")
        if tuple(sorted(set(self.supported_timeframes))) != self.supported_timeframes:
            raise ValueError("supported timeframes must be sorted and unique")

    def to_payload(self) -> dict[str, object]:
        return {
            "broker": self.broker.value,
            "connection_mode": self.connection_mode.value,
            "authenticated": self.authenticated,
            "can_trade": self.can_trade,
            "supports_ticks": self.supports_ticks,
            "supports_tick_history": self.supports_tick_history,
            "supports_candles": self.supports_candles,
            "supports_active_symbols": self.supports_active_symbols,
            "supports_contract_metadata": self.supports_contract_metadata,
            "supports_server_time": self.supports_server_time,
            "supported_timeframes": list(self.supported_timeframes),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> BrokerCapabilities:
        timeframes = payload.get("supported_timeframes")
        flags = (
            "authenticated",
            "can_trade",
            "supports_ticks",
            "supports_tick_history",
            "supports_candles",
            "supports_active_symbols",
            "supports_contract_metadata",
            "supports_server_time",
        )
        if not isinstance(timeframes, list) or any(
            isinstance(value, bool) or not isinstance(value, int) for value in timeframes
        ):
            raise ValueError("supported_timeframes must be a list of integers")
        if any(not isinstance(payload.get(name), bool) for name in flags):
            raise ValueError("capability flags must be booleans")
        return cls(
            broker=Broker(_required_str(payload, "broker")),
            connection_mode=BrokerConnectionMode(_required_str(payload, "connection_mode")),
            authenticated=bool(payload["authenticated"]),
            can_trade=bool(payload["can_trade"]),
            supports_ticks=bool(payload["supports_ticks"]),
            supports_tick_history=bool(payload["supports_tick_history"]),
            supports_candles=bool(payload["supports_candles"]),
            supports_active_symbols=bool(payload["supports_active_symbols"]),
            supports_contract_metadata=bool(payload["supports_contract_metadata"]),
            supports_server_time=bool(payload["supports_server_time"]),
            supported_timeframes=tuple(int(value) for value in timeframes),
        )


@dataclass(frozen=True, slots=True)
class MarketSymbol:
    broker: Broker
    broker_symbol: str
    canonical_symbol: str | None
    display_name: str
    market: str
    submarket: str | None
    symbol_type: str
    pip_size: Decimal | None
    is_trading: bool
    source_timestamp: datetime

    def __post_init__(self) -> None:
        require_aware_utc(self.source_timestamp, "source_timestamp")
        for value in (self.broker_symbol, self.display_name, self.market, self.symbol_type):
            if not value.strip():
                raise ValueError("market symbol identity fields cannot be blank")
        if self.pip_size is not None and (not self.pip_size.is_finite() or self.pip_size <= 0):
            raise ValueError("pip_size must be a positive finite decimal")

    def to_payload(self) -> dict[str, object]:
        return {
            "broker": self.broker.value,
            "broker_symbol": self.broker_symbol,
            "canonical_symbol": self.canonical_symbol,
            "display_name": self.display_name,
            "market": self.market,
            "submarket": self.submarket,
            "symbol_type": self.symbol_type,
            "pip_size": str(self.pip_size) if self.pip_size is not None else None,
            "is_trading": self.is_trading,
            "source_timestamp": self.source_timestamp.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> MarketSymbol:
        canonical = payload.get("canonical_symbol")
        submarket = payload.get("submarket")
        pip = payload.get("pip_size")
        is_trading = payload.get("is_trading")
        if canonical is not None and not isinstance(canonical, str):
            raise ValueError("canonical_symbol must be a string or null")
        if submarket is not None and not isinstance(submarket, str):
            raise ValueError("submarket must be a string or null")
        if pip is not None and not isinstance(pip, str):
            raise ValueError("pip_size must be a decimal string or null")
        if not isinstance(is_trading, bool):
            raise ValueError("is_trading must be a boolean")
        return cls(
            broker=Broker(_required_str(payload, "broker")),
            broker_symbol=_required_str(payload, "broker_symbol"),
            canonical_symbol=canonical,
            display_name=_required_str(payload, "display_name"),
            market=_required_str(payload, "market"),
            submarket=submarket,
            symbol_type=_required_str(payload, "symbol_type"),
            pip_size=Decimal(pip) if pip is not None else None,
            is_trading=is_trading,
            source_timestamp=datetime.fromisoformat(_required_str(payload, "source_timestamp")),
        )


@dataclass(frozen=True, slots=True)
class ContractMetadata:
    broker: Broker
    broker_symbol: str
    contract_type: str
    duration_units: tuple[str, ...]
    min_duration: int | None
    max_duration: int | None
    is_available: bool

    def __post_init__(self) -> None:
        if not self.broker_symbol or not self.contract_type:
            raise ValueError("contract identity is required")
        if any(not item for item in self.duration_units):
            raise ValueError("duration units cannot be blank")
        if self.min_duration is not None and self.min_duration <= 0:
            raise ValueError("min_duration must be positive")
        if self.max_duration is not None and self.max_duration <= 0:
            raise ValueError("max_duration must be positive")
        if (
            self.min_duration is not None
            and self.max_duration is not None
            and self.min_duration > self.max_duration
        ):
            raise ValueError("contract duration range is inverted")

    def to_payload(self) -> dict[str, object]:
        return {
            "broker": self.broker.value,
            "broker_symbol": self.broker_symbol,
            "contract_type": self.contract_type,
            "duration_units": list(self.duration_units),
            "min_duration": self.min_duration,
            "max_duration": self.max_duration,
            "is_available": self.is_available,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ContractMetadata:
        units = payload.get("duration_units")
        minimum = payload.get("min_duration")
        maximum = payload.get("max_duration")
        available = payload.get("is_available")
        if not isinstance(units, list) or not all(isinstance(item, str) for item in units):
            raise ValueError("duration_units must be a string list")
        if minimum is not None and (isinstance(minimum, bool) or not isinstance(minimum, int)):
            raise ValueError("min_duration must be an integer or null")
        if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int)):
            raise ValueError("max_duration must be an integer or null")
        if not isinstance(available, bool):
            raise ValueError("is_available must be a boolean")
        return cls(
            broker=Broker(_required_str(payload, "broker")),
            broker_symbol=_required_str(payload, "broker_symbol"),
            contract_type=_required_str(payload, "contract_type"),
            duration_units=tuple(units),
            min_duration=minimum,
            max_duration=maximum,
            is_available=available,
        )


@dataclass(frozen=True, slots=True)
class MarketTick:
    broker: Broker
    broker_symbol: str
    epoch: int
    quote: Decimal
    received_at: datetime
    subscription_id: str
    source: str

    def __post_init__(self) -> None:
        require_aware_utc(self.received_at, "received_at")
        if self.epoch <= 0 or not self.broker_symbol or not self.subscription_id:
            raise ValueError("tick identity and timestamp are required")
        if not self.quote.is_finite() or self.quote <= 0:
            raise ValueError("tick quote must be a positive finite decimal")

    @property
    def identity(self) -> tuple[Broker, str, int, Decimal, str]:
        return (self.broker, self.broker_symbol, self.epoch, self.quote, self.source)

    def to_payload(self) -> dict[str, object]:
        return {
            "broker": self.broker.value,
            "broker_symbol": self.broker_symbol,
            "epoch": self.epoch,
            "quote": str(self.quote),
            "received_at": self.received_at.isoformat(),
            "subscription_id": self.subscription_id,
            "source": self.source,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> MarketTick:
        return cls(
            broker=Broker(_required_str(payload, "broker")),
            broker_symbol=_required_str(payload, "broker_symbol"),
            epoch=_required_int(payload, "epoch"),
            quote=_decimal(payload.get("quote"), "quote", positive=True),
            received_at=datetime.fromisoformat(_required_str(payload, "received_at")),
            subscription_id=_required_str(payload, "subscription_id"),
            source=_required_str(payload, "source"),
        )


@dataclass(frozen=True, slots=True)
class MarketCandle:
    broker: Broker
    broker_symbol: str
    timeframe_seconds: int
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    is_closed: bool

    def __post_init__(self) -> None:
        require_aware_utc(self.open_time, "open_time")
        require_aware_utc(self.close_time, "close_time")
        if not self.broker_symbol or self.timeframe_seconds <= 0:
            raise ValueError("candle identity and timeframe are required")
        if self.close_time <= self.open_time:
            raise ValueError("candle close_time must follow open_time")
        prices = (self.open, self.high, self.low, self.close)
        if any(not price.is_finite() or price <= 0 for price in prices):
            raise ValueError("candle prices must be positive finite decimals")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError("candle OHLC range is inconsistent")

    def to_payload(self) -> dict[str, object]:
        return {
            "broker": self.broker.value,
            "broker_symbol": self.broker_symbol,
            "timeframe_seconds": self.timeframe_seconds,
            "open_time": self.open_time.isoformat(),
            "close_time": self.close_time.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "is_closed": self.is_closed,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> MarketCandle:
        closed = payload.get("is_closed")
        if not isinstance(closed, bool):
            raise ValueError("is_closed must be a boolean")
        return cls(
            broker=Broker(_required_str(payload, "broker")),
            broker_symbol=_required_str(payload, "broker_symbol"),
            timeframe_seconds=_required_int(payload, "timeframe_seconds"),
            open_time=datetime.fromisoformat(_required_str(payload, "open_time")),
            close_time=datetime.fromisoformat(_required_str(payload, "close_time")),
            open=_decimal(payload.get("open"), "open", positive=True),
            high=_decimal(payload.get("high"), "high", positive=True),
            low=_decimal(payload.get("low"), "low", positive=True),
            close=_decimal(payload.get("close"), "close", positive=True),
            is_closed=closed,
        )


@dataclass(frozen=True, slots=True)
class MarketHistoryBatch:
    response_message_id: str
    correlation_id: str
    causation_id: str
    ticks: tuple[MarketTick, ...]
    candles: tuple[MarketCandle, ...]

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.response_message_id,
                self.correlation_id,
                self.causation_id,
            )
        ):
            raise ValueError("market history batch routing identity is required")


@dataclass(frozen=True, slots=True)
class BrokerClockSnapshot:
    server_epoch: int
    local_received_at: datetime
    round_trip_seconds: float
    estimated_offset_seconds: Decimal

    def __post_init__(self) -> None:
        require_aware_utc(self.local_received_at, "local_received_at")
        if self.server_epoch <= 0 or self.round_trip_seconds < 0:
            raise ValueError("clock snapshot values are invalid")
        if not self.estimated_offset_seconds.is_finite():
            raise ValueError("clock offset must be finite")

    def to_payload(self) -> dict[str, object]:
        return {
            "server_epoch": self.server_epoch,
            "server_time_utc": datetime.fromtimestamp(
                self.server_epoch, tz=self.local_received_at.tzinfo
            ).isoformat(),
            "local_received_at": self.local_received_at.isoformat(),
            "round_trip_seconds": self.round_trip_seconds,
            "round_trip_milliseconds": self.round_trip_milliseconds,
            "estimated_offset_seconds": str(self.estimated_offset_seconds),
            "offset_milliseconds": self.offset_milliseconds,
            "is_synced": self.is_synced,
        }

    @property
    def round_trip_milliseconds(self) -> int:
        return max(0, int(Decimal(str(self.round_trip_seconds)) * 1_000))

    @property
    def offset_milliseconds(self) -> int:
        return int(self.estimated_offset_seconds * 1_000)

    @property
    def is_synced(self) -> bool:
        return self.is_trusted(max_round_trip_ms=1_000, max_absolute_offset_ms=2_000)

    def is_trusted(self, *, max_round_trip_ms: int, max_absolute_offset_ms: int) -> bool:
        if min(max_round_trip_ms, max_absolute_offset_ms) <= 0:
            raise ValueError("clock trust thresholds must be positive")
        return (
            self.round_trip_milliseconds <= max_round_trip_ms
            and abs(self.offset_milliseconds) <= max_absolute_offset_ms
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> BrokerClockSnapshot:
        duration = payload.get("round_trip_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("round_trip_seconds must be numeric")
        return cls(
            server_epoch=_required_int(payload, "server_epoch"),
            local_received_at=datetime.fromisoformat(_required_str(payload, "local_received_at")),
            round_trip_seconds=float(duration),
            estimated_offset_seconds=_decimal(
                payload.get("estimated_offset_seconds"), "estimated_offset_seconds"
            ),
        )


@dataclass(frozen=True, slots=True)
class BrokerAccountBalance:
    balance_minor_units: int
    currency: str
    account_type: str
    observed_at_utc: datetime

    def __post_init__(self) -> None:
        require_aware_utc(self.observed_at_utc, "observed_at_utc")
        if type(self.balance_minor_units) is not int:
            raise TypeError("balance must use integer minor units")
        normalized_currency = self.currency.strip().upper()
        if len(normalized_currency) != 3 or not normalized_currency.isascii():
            raise ValueError("balance currency is invalid")
        if self.account_type not in {"DEMO", "REAL"}:
            raise ValueError("account type must be DEMO or REAL")
        object.__setattr__(self, "currency", normalized_currency)

    def to_payload(self) -> dict[str, object]:
        return {
            "account_type": self.account_type,
            "balance_minor_units": self.balance_minor_units,
            "currency": self.currency,
            "observed_at_utc": self.observed_at_utc.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> BrokerAccountBalance:
        return cls(
            balance_minor_units=_required_int(payload, "balance_minor_units"),
            currency=_required_str(payload, "currency"),
            account_type=_required_str(payload, "account_type"),
            observed_at_utc=datetime.fromisoformat(_required_str(payload, "observed_at_utc")),
        )
