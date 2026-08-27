from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.deriv_worker.schema import (
    DerivErrorCategory,
    DerivWorkerError,
    require_decimal,
    require_int,
    require_list,
    require_mapping,
    require_str,
    validate_response,
)
from packages.domain.market import (
    BrokerAccountBalance,
    BrokerClockSnapshot,
    ContractMetadata,
    MarketCandle,
    MarketSymbol,
    MarketTick,
)
from packages.domain.models import Broker


def map_active_symbols(
    payload: Mapping[str, object], source_timestamp: datetime
) -> tuple[MarketSymbol, ...]:
    validate_response(payload, "active_symbols")
    symbols: list[MarketSymbol] = []
    for raw in require_list(payload, "active_symbols"):
        if not isinstance(raw, dict):
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        suspended = raw.get("is_trading_suspended")
        exchange_open = raw.get("exchange_is_open")
        if suspended not in (0, 1) or exchange_open not in (0, 1):
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        pip_value = raw.get("pip_size")
        pip = require_decimal(raw, "pip_size") if pip_value is not None else None
        submarket = raw.get("submarket")
        if submarket is not None and not isinstance(submarket, str):
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        raw_symbol_type = raw.get("underlying_symbol_type")
        if not isinstance(raw_symbol_type, str) or not raw_symbol_type.strip():
            raw_symbol_type = raw.get("subgroup") or submarket or raw.get("market")
        if not isinstance(raw_symbol_type, str) or not raw_symbol_type.strip():
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        symbols.append(
            MarketSymbol(
                broker=Broker.DERIV,
                broker_symbol=require_str(raw, "underlying_symbol"),
                canonical_symbol=None,
                display_name=require_str(raw, "underlying_symbol_name"),
                market=require_str(raw, "market"),
                submarket=submarket,
                symbol_type=raw_symbol_type.strip(),
                pip_size=pip,
                is_trading=exchange_open == 1 and suspended == 0,
                source_timestamp=source_timestamp,
            )
        )
    return tuple(symbols)


def map_contracts(
    payload: Mapping[str, object], expected_symbol: str
) -> tuple[ContractMetadata, ...]:
    validate_response(payload, "contracts_for")
    root = require_mapping(payload, "contracts_for")
    contracts: list[ContractMetadata] = []
    for raw in require_list(root, "available"):
        if not isinstance(raw, dict):
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        symbol = require_str(raw, "underlying_symbol")
        if symbol != expected_symbol:
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        contracts.append(
            ContractMetadata(
                broker=Broker.DERIV,
                broker_symbol=symbol,
                contract_type=require_str(raw, "contract_type"),
                duration_units=(),
                min_duration=None,
                max_duration=None,
                is_available=True,
            )
        )
    return tuple(contracts)


def map_tick(payload: Mapping[str, object], received_at: datetime) -> MarketTick:
    validate_response(payload, "tick")
    raw = require_mapping(payload, "tick")
    subscription = require_mapping(payload, "subscription")
    return MarketTick(
        broker=Broker.DERIV,
        broker_symbol=require_str(raw, "symbol"),
        epoch=require_int(raw, "epoch"),
        quote=require_decimal(raw, "quote"),
        received_at=received_at,
        subscription_id=require_str(subscription, "id"),
        source="DERIV_TICKS",
    )


def map_tick_history(
    payload: Mapping[str, object], symbol: str, received_at: datetime
) -> tuple[MarketTick, ...]:
    validate_response(payload, "history")
    history = require_mapping(payload, "history")
    prices = require_list(history, "prices")
    times = require_list(history, "times")
    if len(prices) != len(times):
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )
    result: list[MarketTick] = []
    for index, (price, epoch) in enumerate(zip(prices, times, strict=True)):
        raw = {"quote": price, "epoch": epoch}
        result.append(
            MarketTick(
                broker=Broker.DERIV,
                broker_symbol=symbol,
                epoch=require_int(raw, "epoch"),
                quote=require_decimal(raw, "quote"),
                received_at=received_at,
                subscription_id=f"history-{index}",
                source="DERIV_TICKS_HISTORY",
            )
        )
    return tuple(result)


def map_candle_history(
    payload: Mapping[str, object], symbol: str, timeframe_seconds: int, received_at: datetime
) -> tuple[MarketCandle, ...]:
    validate_response(payload, "candles")
    if timeframe_seconds <= 0:
        raise ValueError("timeframe must be positive")
    result: list[MarketCandle] = []
    for item in require_list(payload, "candles"):
        if not isinstance(item, dict):
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        epoch = require_int(item, "epoch")
        opened = datetime.fromtimestamp(epoch, UTC)
        closed = opened + timedelta(seconds=timeframe_seconds)
        result.append(
            MarketCandle(
                broker=Broker.DERIV,
                broker_symbol=symbol,
                timeframe_seconds=timeframe_seconds,
                open_time=opened,
                close_time=closed,
                open=require_decimal(item, "open"),
                high=require_decimal(item, "high"),
                low=require_decimal(item, "low"),
                close=require_decimal(item, "close"),
                is_closed=closed <= received_at,
            )
        )
    return tuple(result)


def map_clock(
    payload: Mapping[str, object], received_at: datetime, round_trip_seconds: float
) -> BrokerClockSnapshot:
    validate_response(payload, "time")
    server_epoch = require_int(payload, "time")
    midpoint = Decimal(str(received_at.timestamp())) - Decimal(str(round_trip_seconds / 2))
    return BrokerClockSnapshot(
        server_epoch=server_epoch,
        local_received_at=received_at,
        round_trip_seconds=round_trip_seconds,
        estimated_offset_seconds=Decimal(server_epoch) - midpoint,
    )


def map_account_balance(
    payload: Mapping[str, object], observed_at: datetime, account_type: str = "demo"
) -> BrokerAccountBalance:
    validate_response(payload, "balance")
    raw = require_mapping(payload, "balance")
    amount = require_decimal(raw, "balance")
    scaled = amount * Decimal(100)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_BALANCE_PRECISION_UNSUPPORTED",
        )
    return BrokerAccountBalance(
        balance_minor_units=int(integral),
        currency=require_str(raw, "currency"),
        account_type=account_type.upper(),
        observed_at_utc=observed_at,
    )


def map_demo_balance(payload: Mapping[str, object], observed_at: datetime) -> BrokerAccountBalance:
    return map_account_balance(payload, observed_at, "demo")
