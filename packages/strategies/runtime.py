from __future__ import annotations

import hashlib
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from packages.domain.canonical import canonical_bytes
from packages.domain.market import MarketCandle
from packages.strategies.models import (
    RuntimeContext,
    StrategyEvaluation,
    StrategyEvaluationReason,
    StrategySignal,
)
from packages.strategy_catalog.models import DataRequirement

if TYPE_CHECKING:
    from packages.strategy_catalog.catalog import CatalogEntry, StrategyCatalog


class _StrategyRuntimeInstance:
    def __init__(self, context: RuntimeContext, entry: CatalogEntry) -> None:
        self.context = context
        self.entry = entry
        self._candles: deque[MarketCandle] = deque(maxlen=entry.manifest.warmup_candles)
        self._last_close_time: datetime | None = None
        self._candles_seen = 0

    def evaluate(self, candle: MarketCandle) -> StrategyEvaluation:
        if (
            candle.broker is not self.context.broker
            or candle.broker_symbol != self.context.symbol
            or candle.timeframe_seconds != self.context.timeframe_seconds
        ):
            return StrategyEvaluation(self.context, StrategyEvaluationReason.CONTEXT_MISMATCH, None)
        if not candle.is_closed:
            return StrategyEvaluation(
                self.context, StrategyEvaluationReason.CANDLE_NOT_CLOSED, None
            )
        if self._last_close_time is not None:
            if candle.close_time == self._last_close_time:
                return StrategyEvaluation(
                    self.context, StrategyEvaluationReason.DUPLICATE_CANDLE, None
                )
            if candle.close_time < self._last_close_time:
                return StrategyEvaluation(
                    self.context, StrategyEvaluationReason.OUT_OF_ORDER_CANDLE, None
                )
        self._candles.append(candle)
        self._last_close_time = candle.close_time
        self._candles_seen += 1
        if len(self._candles) < self.entry.manifest.warmup_candles:
            return StrategyEvaluation(self.context, StrategyEvaluationReason.WARMING_UP, None)
        direction = self.entry.implementation.evaluate(tuple(self._candles), self.context)
        if direction is None:
            return StrategyEvaluation(self.context, StrategyEvaluationReason.NO_SIGNAL, None)
        identity = {
            "candle_close_time": candle.close_time.isoformat(),
            "configuration_version": self.context.configuration_version,
            "direction": direction.value,
            "strategy_id": self.context.strategy_id,
            "strategy_version": self.context.strategy_version,
            "runtime_context": [str(item) for item in self.context.key],
        }
        digest = hashlib.sha256(canonical_bytes(identity)).hexdigest()
        signal = StrategySignal(
            signal_id=digest,
            correlation_id=f"STRATEGY-{digest}",
            context=self.context,
            direction=direction,
            created_at=candle.close_time,
            valid_until=candle.close_time + timedelta(seconds=self.context.timeframe_seconds),
            candle_close_time=candle.close_time,
            evidence=(
                ("artifact_hash", self.entry.artifact_hash),
                ("candle_close_time", candle.close_time.isoformat()),
                ("source", "CLOSED_CANDLE"),
            ),
        )
        return StrategyEvaluation(self.context, StrategyEvaluationReason.SIGNAL, signal)

    def restore(self, candles: tuple[MarketCandle, ...], *, candles_seen: int) -> None:
        if not candles or len(candles) > self.entry.manifest.warmup_candles:
            raise ValueError("runtime restore candle window is invalid")
        if candles_seen < len(candles):
            raise ValueError("runtime restore candle count is invalid")
        previous: datetime | None = None
        for candle in candles:
            if (
                candle.broker is not self.context.broker
                or candle.broker_symbol != self.context.symbol
                or candle.timeframe_seconds != self.context.timeframe_seconds
                or not candle.is_closed
            ):
                raise ValueError("runtime restore candle context is invalid")
            if previous is not None and candle.close_time <= previous:
                raise ValueError("runtime restore candles are not ordered")
            previous = candle.close_time
        self._candles.extend(candles)
        self._last_close_time = candles[-1].close_time
        self._candles_seen = candles_seen


class StrategyRuntimeManager:
    def __init__(self, catalog: StrategyCatalog, *, max_instances: int = 256) -> None:
        if max_instances <= 0:
            raise ValueError("max_instances must be positive")
        self._catalog = catalog
        self._max_instances = max_instances
        self._lock = threading.Lock()
        self._instances: dict[tuple[object, ...], _StrategyRuntimeInstance] = {}
        self._configurations: dict[tuple[str, str, str], tuple[tuple[str, str], ...]] = {}

    @property
    def instance_count(self) -> int:
        with self._lock:
            return len(self._instances)

    def evaluate(
        self,
        context: RuntimeContext,
        candle: MarketCandle,
        *,
        entitled_packs: frozenset[str],
        available_data: frozenset[DataRequirement] = frozenset({DataRequirement.CLOSED_CANDLES}),
    ) -> StrategyEvaluation:
        entry = self._catalog.activate(
            context,
            entitled_packs=entitled_packs,
            available_data=available_data,
        )
        with self._lock:
            configuration_key = (
                context.strategy_id,
                context.strategy_version,
                context.configuration_version,
            )
            known_parameters = self._configurations.get(configuration_key)
            if known_parameters is not None and known_parameters != context.parameters:
                raise RuntimeError("strategy configuration version is immutable")
            self._configurations[configuration_key] = context.parameters
            instance = self._instances.get(context.key)
            if instance is None:
                if len(self._instances) >= self._max_instances:
                    raise RuntimeError("strategy runtime instance limit reached")
                instance = _StrategyRuntimeInstance(context, entry)
                self._instances[context.key] = instance
            return instance.evaluate(candle)

    def restore(
        self,
        context: RuntimeContext,
        candles: tuple[MarketCandle, ...],
        *,
        candles_seen: int,
        entitled_packs: frozenset[str],
        available_data: frozenset[DataRequirement] = frozenset({DataRequirement.CLOSED_CANDLES}),
    ) -> None:
        entry = self._catalog.activate(
            context,
            entitled_packs=entitled_packs,
            available_data=available_data,
        )
        with self._lock:
            if context.key in self._instances:
                raise RuntimeError("strategy runtime instance already exists")
            if len(self._instances) >= self._max_instances:
                raise RuntimeError("strategy runtime instance limit reached")
            instance = _StrategyRuntimeInstance(context, entry)
            instance.restore(candles, candles_seen=candles_seen)
            self._instances[context.key] = instance
            self._configurations[
                (context.strategy_id, context.strategy_version, context.configuration_version)
            ] = context.parameters
