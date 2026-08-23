from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from packages.market_data import ClosedCandle
from packages.market_pipeline.health import MarketHealthGate
from packages.market_pipeline.models import (
    ExecutionMode,
    MarketPipelineMetrics,
    MarketSeriesId,
)
from packages.observability import EventSink, NullEventSink
from packages.persistence.candle_repository import CandleRepository
from packages.replay.models import ReplayResult


class ExecutionCapabilityError(RuntimeError):
    reason_code = "CAPABILITY_DENIED"


@dataclass(frozen=True, slots=True)
class ExecutionCapabilityGate:
    can_submit_orders: bool = False
    mode: ExecutionMode = ExecutionMode.DECISION_ONLY

    reason_code = "CAPABILITY_DENIED"

    def ensure(self, *, dispatch: bool) -> None:
        if dispatch or self.mode is not ExecutionMode.DECISION_ONLY or self.can_submit_orders:
            raise ExecutionCapabilityError(self.reason_code)


class DecisionOnlyPipeline(Protocol):
    def process_candle(self, candle: ClosedCandle, *, dispatch: bool) -> int: ...


class ReplaySessionPort(Protocol):
    def process(self, candle: ClosedCandle, *, dispatch: bool = False) -> None: ...

    def result(self) -> ReplayResult: ...


class ReplaySessionDecisionPipeline:
    def __init__(self, session: ReplaySessionPort) -> None:
        self._session = session

    def process_candle(self, candle: ClosedCandle, *, dispatch: bool) -> int:
        before = len(self._session.result().risk_decisions)
        self._session.process(candle, dispatch=dispatch)
        return len(self._session.result().risk_decisions) - before

    def fingerprint(self) -> ShadowDecisionFingerprint:
        result = self._session.result()
        return ShadowDecisionFingerprint(
            final_hash=result.final_hash,
            signal_count=len(result.signal_ids),
            decision_count=len(result.risk_decisions),
        )


@dataclass(frozen=True, slots=True)
class ShadowDecisionFingerprint:
    final_hash: str
    signal_count: int
    decision_count: int

    def __post_init__(self) -> None:
        if len(self.final_hash) != 64:
            raise ValueError("shadow fingerprint requires a SHA-256 hash")
        if self.signal_count < 0 or self.decision_count < 0:
            raise ValueError("shadow fingerprint counts cannot be negative")


class AcceptedCandleDispatcher:
    def __init__(
        self,
        repository: CandleRepository,
        health: MarketHealthGate,
        pipeline: DecisionOnlyPipeline,
        *,
        capability: ExecutionCapabilityGate | None = None,
        events: EventSink | None = None,
        metrics: MarketPipelineMetrics | None = None,
    ) -> None:
        self._repository = repository
        self._health = health
        self._pipeline = pipeline
        self._capability = capability or ExecutionCapabilityGate()
        self._events = events or NullEventSink()
        self.metrics = metrics or MarketPipelineMetrics()

    def dispatch(self, series_id: MarketSeriesId, candle: ClosedCandle) -> int:
        snapshot = self._health.snapshot(series_id)
        if not snapshot.dispatch_allowed:
            raise RuntimeError(f"MARKET_HEALTH_BLOCKED:{snapshot.reason.value}")
        if (
            candle.broker is not series_id.broker
            or candle.symbol != series_id.broker_symbol
            or candle.timeframe_seconds != series_id.timeframe_seconds
        ):
            raise ValueError("accepted candle does not match dispatcher series")
        persisted = self._repository.get(candle.candle_id)
        if persisted != candle:
            raise RuntimeError("CANDLE_NOT_DURABLE")
        self._capability.ensure(dispatch=False)
        decisions = self._pipeline.process_candle(candle, dispatch=False)
        self.metrics.shadow_candles_dispatched += 1
        self.metrics.shadow_decisions += decisions
        self._events.emit(
            "shadow_candle_dispatched",
            series_id=series_id.key,
            candle_id=candle.candle_id,
            close_epoch=candle.close_time_ms,
        )
        if decisions:
            self._events.emit(
                "shadow_decision_committed",
                series_id=series_id.key,
                candle_id=candle.candle_id,
                decision_count=decisions,
            )
        return decisions
