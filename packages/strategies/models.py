from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction, require_aware_utc

ArbitrationKey = tuple[Broker, str, str, str, int]


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    strategy_id: str
    strategy_version: str
    broker: Broker
    account_id: str
    product: str
    symbol: str
    timeframe_seconds: int
    configuration_version: str
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for field in (
            "strategy_id",
            "strategy_version",
            "account_id",
            "product",
            "symbol",
            "configuration_version",
        ):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} cannot be empty")
        if self.timeframe_seconds <= 0:
            raise ValueError("timeframe_seconds must be positive")
        names = tuple(name for name, _ in self.parameters)
        if any(not name.strip() for name in names) or len(set(names)) != len(names):
            raise ValueError("parameter names must be unique and non-empty")

    @property
    def key(self) -> tuple[object, ...]:
        return (
            self.strategy_id,
            self.strategy_version,
            self.broker,
            self.account_id,
            self.product,
            self.symbol,
            self.timeframe_seconds,
            self.configuration_version,
            self.parameters,
        )

    @property
    def arbitration_key(self) -> ArbitrationKey:
        return (
            self.broker,
            self.account_id,
            self.product,
            self.symbol,
            self.timeframe_seconds,
        )


@dataclass(frozen=True, slots=True)
class StrategySignal:
    signal_id: str
    correlation_id: str
    context: RuntimeContext
    direction: Direction
    created_at: datetime
    valid_until: datetime
    candle_close_time: datetime
    evidence: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        require_aware_utc(self.created_at, "created_at")
        require_aware_utc(self.valid_until, "valid_until")
        require_aware_utc(self.candle_close_time, "candle_close_time")
        if not self.signal_id.strip() or not self.correlation_id.strip():
            raise ValueError("signal identity cannot be empty")
        if self.valid_until <= self.created_at:
            raise ValueError("signal validity must follow creation")
        evidence_names = tuple(name for name, _ in self.evidence)
        if not self.evidence or len(set(evidence_names)) != len(evidence_names):
            raise ValueError("signal evidence must be non-empty and uniquely named")


class StrategyEvaluationReason(StrEnum):
    SIGNAL = "SIGNAL"
    NO_SIGNAL = "NO_SIGNAL"
    WARMING_UP = "WARMING_UP"
    CANDLE_NOT_CLOSED = "CANDLE_NOT_CLOSED"
    DUPLICATE_CANDLE = "DUPLICATE_CANDLE"
    OUT_OF_ORDER_CANDLE = "OUT_OF_ORDER_CANDLE"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    context: RuntimeContext
    reason: StrategyEvaluationReason
    signal: StrategySignal | None

    def __post_init__(self) -> None:
        if (self.reason is StrategyEvaluationReason.SIGNAL) != (self.signal is not None):
            raise ValueError("strategy evaluation reason/signal mismatch")


class StrategyImplementation(Protocol):
    @property
    def artifact_bytes(self) -> bytes: ...

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None: ...
