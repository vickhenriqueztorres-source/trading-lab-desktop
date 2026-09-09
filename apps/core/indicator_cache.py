"""Incremental, shared indicator cache for IQ Option candle series.

This component implements only the ``series -> indicator`` layer of the local
execution graph.  It is deliberately data-only: it owns no risk state, exposes
no submit/buy API, and cannot enable a financial entry.  CAT-10 is responsible
for wiring indicator outputs into candidate admission.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from apps.core.families.primitives.base import Candle, Indicator, Output, ParamRange
from apps.core.families.primitives.registry import REGISTRY
from apps.core.iqoption_series_hub import IQOptionSeriesKey, IQOptionSeriesSnapshot
from packages.domain.market import MarketCandle

INDICATOR_PRIMITIVES_VERSION = "1.0.0"
INDICATOR_EXECUTION_SEMANTICS_VERSION = "tl.candle-close.v2"
INDICATOR_BOOTSTRAP_IDENTITY = "cat04.full-bootstrap.close-time-aligned.v1"


class IndicatorNodeState(StrEnum):
    WARMING_UP = "WARMING_UP"
    READY = "READY"
    INVALID = "INVALID"


class IndicatorCacheReason(StrEnum):
    OK = "OK"
    WARMUP_INCOMPLETE = "WARMUP_INCOMPLETE"
    EMPTY_SERIES = "EMPTY_SERIES"
    SERIES_MISMATCH = "SERIES_MISMATCH"
    PARTIAL_CANDLE = "PARTIAL_CANDLE"
    TIMEFRAME_MISMATCH = "TIMEFRAME_MISMATCH"
    NON_MONOTONIC_SERIES = "NON_MONOTONIC_SERIES"
    SERIES_GAP = "SERIES_GAP"
    HISTORICAL_CORRECTION = "HISTORICAL_CORRECTION"
    TICK_VOLUME_UNAVAILABLE = "TICK_VOLUME_UNAVAILABLE"
    UNKNOWN_INDICATOR = "UNKNOWN_INDICATOR"
    PARAMETER_INVALID = "PARAMETER_INVALID"
    CACHE_CAPACITY_EXCEEDED = "CACHE_CAPACITY_EXCEEDED"
    SHADOW_MISMATCH = "SHADOW_MISMATCH"


class ShadowComparisonStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    WARMING_UP = "WARMING_UP"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class IndicatorKey:
    """Exact identity of one indicator node.

    The series key carries broker/account/product/generation/asset/timeframe
    identity from CAT-08.  Indicator params are canonical decimal strings so
    equivalent recipes share work, while different semantics/bootstrap/auxiliary
    inputs never share state by accident.
    """

    series_key: IQOptionSeriesKey
    name: str
    params: tuple[tuple[str, str], ...]
    primitives_version: str = INDICATOR_PRIMITIVES_VERSION
    execution_semantics_version: str = INDICATOR_EXECUTION_SEMANTICS_VERSION
    bootstrap_identity: str = INDICATOR_BOOTSTRAP_IDENTITY
    auxiliary_identity: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("indicator name is required")
        if not self.primitives_version.strip():
            raise ValueError("primitives version is required")
        if not self.execution_semantics_version.strip():
            raise ValueError("execution semantics version is required")
        if not self.bootstrap_identity.strip():
            raise ValueError("bootstrap identity is required")
        if tuple(sorted(self.params)) != self.params:
            raise ValueError("indicator params must be canonical and sorted")
        if tuple(sorted(self.auxiliary_identity)) != self.auxiliary_identity:
            raise ValueError("auxiliary identity must be canonical and sorted")


@dataclass(frozen=True, slots=True)
class IndicatorOutput:
    key: IndicatorKey
    close_epoch: int
    state: IndicatorNodeState
    reason: IndicatorCacheReason
    direction: str
    value: Decimal | None
    meta: tuple[tuple[str, Decimal], ...]
    candles_seen: int
    warmup_required: int


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    key: IndicatorKey
    close_epoch: int
    status: ShadowComparisonStatus
    cached: IndicatorOutput | None
    reference: IndicatorOutput | None
    reason: IndicatorCacheReason


@dataclass(frozen=True, slots=True)
class IndicatorCacheStats:
    nodes_created: int = 0
    nodes_evicted: int = 0
    reference_adds: int = 0
    reference_releases: int = 0
    indicator_calculations: int = 0
    dedup_hits: int = 0
    invalidations: int = 0
    shadow_compares: int = 0
    shadow_mismatches: int = 0
    active_nodes: int = 0


@dataclass(slots=True)
class _IndicatorNode:
    key: IndicatorKey
    indicator: Indicator
    warmup_required: int
    refs: set[str]
    outputs: dict[int, IndicatorOutput]
    fingerprints: dict[int, tuple[object, ...]]
    last_epoch: int | None = None
    state: IndicatorNodeState = IndicatorNodeState.WARMING_UP
    invalid_reason: IndicatorCacheReason | None = None


def canonical_indicator_params(params: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((name, _canonical_value(value)) for name, value in params.items()))


def canonical_auxiliary_identity(params: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return canonical_indicator_params(params)


class IndicatorCache:
    """Bounded shared cache for deterministic incremental indicator nodes."""

    def __init__(self, *, max_nodes: int = 512, max_outputs_per_node: int = 2_048) -> None:
        if max_nodes <= 0:
            raise ValueError("indicator cache max_nodes must be positive")
        if max_outputs_per_node <= 0:
            raise ValueError("indicator cache max_outputs_per_node must be positive")
        self._max_nodes = max_nodes
        self._max_outputs_per_node = max_outputs_per_node
        self._nodes: dict[IndicatorKey, _IndicatorNode] = {}
        self._recipes: dict[str, set[IndicatorKey]] = {}
        self._stats = IndicatorCacheStats()

    @property
    def stats(self) -> IndicatorCacheStats:
        return replace(self._stats, active_nodes=len(self._nodes))

    def acquire(self, key: IndicatorKey, *, recipe_id: str) -> None:
        if not recipe_id.strip():
            raise ValueError("recipe_id is required")
        node = self._nodes.get(key)
        if node is None:
            if len(self._nodes) >= self._max_nodes:
                self._increment(invalidations=1)
                raise RuntimeError(IndicatorCacheReason.CACHE_CAPACITY_EXCEEDED.value)
            node = _IndicatorNode(
                key=key,
                indicator=_instantiate_indicator(key),
                warmup_required=_indicator_warmup_required(key),
                refs=set(),
                outputs={},
                fingerprints={},
            )
            self._nodes[key] = node
            self._increment(nodes_created=1)
        if recipe_id not in node.refs:
            node.refs.add(recipe_id)
            self._recipes.setdefault(recipe_id, set()).add(key)
            self._increment(reference_adds=1)

    def release(self, key: IndicatorKey, *, recipe_id: str) -> None:
        node = self._nodes.get(key)
        if node is None or recipe_id not in node.refs:
            return
        node.refs.remove(recipe_id)
        keys = self._recipes.get(recipe_id)
        if keys is not None:
            keys.discard(key)
            if not keys:
                self._recipes.pop(recipe_id, None)
        self._increment(reference_releases=1)
        if not node.refs:
            self._nodes.pop(key, None)
            self._increment(nodes_evicted=1)

    def release_recipe(self, recipe_id: str) -> None:
        for key in tuple(self._recipes.get(recipe_id, ())):
            self.release(key, recipe_id=recipe_id)

    def node_ref_count(self, key: IndicatorKey) -> int:
        node = self._nodes.get(key)
        return 0 if node is None else len(node.refs)

    def invalidate_series(
        self,
        series_key: IQOptionSeriesKey,
        *,
        reason: IndicatorCacheReason = IndicatorCacheReason.SERIES_MISMATCH,
    ) -> None:
        for key in tuple(self._nodes):
            if key.series_key == series_key:
                self.invalidate_key(key, reason=reason)

    def invalidate_all(self, *, reason: IndicatorCacheReason) -> None:
        if reason is IndicatorCacheReason.OK:
            raise ValueError("invalidating with OK reason is not allowed")
        removed = len(self._nodes)
        self._nodes.clear()
        self._recipes.clear()
        self._increment(nodes_evicted=removed, invalidations=removed)

    def invalidate_key(self, key: IndicatorKey, *, reason: IndicatorCacheReason) -> None:
        if reason is IndicatorCacheReason.OK:
            raise ValueError("invalidating with OK reason is not allowed")
        node = self._nodes.get(key)
        if node is None:
            return
        node.state = IndicatorNodeState.INVALID
        node.invalid_reason = reason
        self._increment(invalidations=1)

    def update(self, key: IndicatorKey, candles: Sequence[MarketCandle]) -> IndicatorOutput:
        node = self._require_node(key)
        invalid = self._validate_series(node, candles)
        if invalid is not None:
            return self._mark_invalid(node, invalid, candles)
        if not candles:
            return self._output_for_empty(node)

        newest_epoch = _candle_close_epoch(candles[-1])
        cached = node.outputs.get(newest_epoch)
        if cached is not None and node.state is not IndicatorNodeState.INVALID:
            self._increment(dedup_hits=1)
            return cached

        for candle in candles:
            epoch = _candle_close_epoch(candle)
            if epoch in node.outputs:
                continue
            output = node.indicator.update(_to_primitive_candle(candle))
            self._increment(indicator_calculations=1)
            if output is None:
                emitted = IndicatorOutput(
                    key=key,
                    close_epoch=epoch,
                    state=IndicatorNodeState.WARMING_UP,
                    reason=IndicatorCacheReason.WARMUP_INCOMPLETE,
                    direction="none",
                    value=None,
                    meta=(),
                    candles_seen=len(node.outputs) + 1,
                    warmup_required=node.warmup_required,
                )
            else:
                emitted = IndicatorOutput(
                    key=key,
                    close_epoch=epoch,
                    state=IndicatorNodeState.READY,
                    reason=IndicatorCacheReason.OK,
                    direction=output.direction,
                    value=output.value,
                    meta=tuple(sorted(output.meta.items())),
                    candles_seen=len(node.outputs) + 1,
                    warmup_required=node.warmup_required,
                )
            node.outputs[epoch] = emitted
            node.fingerprints[epoch] = _candle_fingerprint(candle)
            node.last_epoch = epoch
            node.state = emitted.state
            self._trim_outputs(node)
        latest = node.outputs[newest_epoch]
        if latest.state is IndicatorNodeState.WARMING_UP and len(candles) >= node.warmup_required:
            return self._mark_invalid(node, IndicatorCacheReason.PARAMETER_INVALID, candles)
        return latest

    def update_from_snapshot(
        self, snapshot: IQOptionSeriesSnapshot, key: IndicatorKey
    ) -> IndicatorOutput:
        if snapshot.key != key.series_key:
            raise ValueError("snapshot series identity does not match indicator key")
        return self.update(key, snapshot.candles)

    def shadow_compare(
        self,
        key: IndicatorKey,
        candles: Sequence[MarketCandle],
    ) -> ShadowComparison:
        cached = self.update(key, candles)
        self._increment(shadow_compares=1)
        if cached.state is IndicatorNodeState.INVALID:
            return ShadowComparison(
                key=key,
                close_epoch=cached.close_epoch,
                status=ShadowComparisonStatus.INVALID,
                cached=cached,
                reference=None,
                reason=cached.reason,
            )
        reference = _reference_output(key, candles)
        if reference is None:
            return ShadowComparison(
                key=key,
                close_epoch=cached.close_epoch,
                status=ShadowComparisonStatus.WARMING_UP,
                cached=cached,
                reference=None,
                reason=IndicatorCacheReason.WARMUP_INCOMPLETE,
            )
        if _same_indicator_output(cached, reference):
            return ShadowComparison(
                key=key,
                close_epoch=cached.close_epoch,
                status=ShadowComparisonStatus.MATCH,
                cached=cached,
                reference=reference,
                reason=IndicatorCacheReason.OK,
            )
        self.invalidate_key(key, reason=IndicatorCacheReason.SHADOW_MISMATCH)
        self._increment(shadow_mismatches=1)
        return ShadowComparison(
            key=key,
            close_epoch=cached.close_epoch,
            status=ShadowComparisonStatus.MISMATCH,
            cached=cached,
            reference=reference,
            reason=IndicatorCacheReason.SHADOW_MISMATCH,
        )

    def _require_node(self, key: IndicatorKey) -> _IndicatorNode:
        node = self._nodes.get(key)
        if node is None:
            self.acquire(key, recipe_id=f"implicit:{key.name}")
            node = self._nodes[key]
        return node

    def _validate_series(
        self,
        node: _IndicatorNode,
        candles: Sequence[MarketCandle],
    ) -> IndicatorCacheReason | None:
        if node.state is IndicatorNodeState.INVALID:
            return node.invalid_reason or IndicatorCacheReason.PARAMETER_INVALID
        previous_epoch: int | None = None
        for candle in candles:
            if not candle.is_closed:
                return IndicatorCacheReason.PARTIAL_CANDLE
            if (
                candle.broker is not node.key.series_key.broker
                or candle.broker_symbol != node.key.series_key.asset
                or candle.timeframe_seconds != node.key.series_key.timeframe_seconds
            ):
                return IndicatorCacheReason.SERIES_MISMATCH
            epoch = _candle_close_epoch(candle)
            if epoch % node.key.series_key.timeframe_seconds != 0:
                return IndicatorCacheReason.TIMEFRAME_MISMATCH
            if previous_epoch is not None:
                delta = epoch - previous_epoch
                if delta <= 0:
                    return IndicatorCacheReason.NON_MONOTONIC_SERIES
                if delta != node.key.series_key.timeframe_seconds:
                    return IndicatorCacheReason.SERIES_GAP
            fingerprint = _candle_fingerprint(candle)
            if epoch in node.fingerprints and node.fingerprints[epoch] != fingerprint:
                return IndicatorCacheReason.HISTORICAL_CORRECTION
            previous_epoch = epoch
        if node.indicator.requires_tick_volume and any(c.tick_volume is None for c in candles):
            return IndicatorCacheReason.TICK_VOLUME_UNAVAILABLE
        return None

    def _mark_invalid(
        self,
        node: _IndicatorNode,
        reason: IndicatorCacheReason,
        candles: Sequence[MarketCandle],
    ) -> IndicatorOutput:
        epoch = _candle_close_epoch(candles[-1]) if candles else -1
        node.state = IndicatorNodeState.INVALID
        node.invalid_reason = reason
        emitted = IndicatorOutput(
            key=node.key,
            close_epoch=epoch,
            state=IndicatorNodeState.INVALID,
            reason=reason,
            direction="none",
            value=None,
            meta=(),
            candles_seen=len(node.outputs),
            warmup_required=node.warmup_required,
        )
        if epoch >= 0:
            node.outputs[epoch] = emitted
        self._increment(invalidations=1)
        return emitted

    def _output_for_empty(self, node: _IndicatorNode) -> IndicatorOutput:
        return IndicatorOutput(
            key=node.key,
            close_epoch=-1,
            state=IndicatorNodeState.WARMING_UP,
            reason=IndicatorCacheReason.EMPTY_SERIES,
            direction="none",
            value=None,
            meta=(),
            candles_seen=0,
            warmup_required=node.warmup_required,
        )

    def _trim_outputs(self, node: _IndicatorNode) -> None:
        while len(node.outputs) > self._max_outputs_per_node:
            epoch = next(iter(node.outputs))
            node.outputs.pop(epoch, None)
            node.fingerprints.pop(epoch, None)

    def _increment(
        self,
        *,
        nodes_created: int = 0,
        nodes_evicted: int = 0,
        reference_adds: int = 0,
        reference_releases: int = 0,
        indicator_calculations: int = 0,
        dedup_hits: int = 0,
        invalidations: int = 0,
        shadow_compares: int = 0,
        shadow_mismatches: int = 0,
    ) -> None:
        self._stats = IndicatorCacheStats(
            nodes_created=self._stats.nodes_created + nodes_created,
            nodes_evicted=self._stats.nodes_evicted + nodes_evicted,
            reference_adds=self._stats.reference_adds + reference_adds,
            reference_releases=self._stats.reference_releases + reference_releases,
            indicator_calculations=self._stats.indicator_calculations + indicator_calculations,
            dedup_hits=self._stats.dedup_hits + dedup_hits,
            invalidations=self._stats.invalidations + invalidations,
            shadow_compares=self._stats.shadow_compares + shadow_compares,
            shadow_mismatches=self._stats.shadow_mismatches + shadow_mismatches,
            active_nodes=len(self._nodes),
        )


def _instantiate_indicator(key: IndicatorKey) -> Indicator:
    indicator_type = REGISTRY.get(key.name)
    if indicator_type is None:
        raise ValueError(IndicatorCacheReason.UNKNOWN_INDICATOR.value)
    return indicator_type(**_decode_params(key.params, indicator_type.param_spec))


def _indicator_warmup_required(key: IndicatorKey) -> int:
    indicator = _instantiate_indicator(key)
    return indicator.warmup_required


def _decode_params(
    params: tuple[tuple[str, str], ...],
    spec: Mapping[str, ParamRange],
) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for name, value in params:
        param_range = spec.get(name)
        if param_range is None:
            raise ValueError(IndicatorCacheReason.PARAMETER_INVALID.value)
        if param_range.kind == "int":
            parsed_decimal = _parse_decimal(value)
            if parsed_decimal != parsed_decimal.to_integral_exact():
                raise ValueError(IndicatorCacheReason.PARAMETER_INVALID.value)
            parsed_int = int(parsed_decimal)
            if not isinstance(param_range.min, int) or not isinstance(param_range.max, int):
                raise ValueError(IndicatorCacheReason.PARAMETER_INVALID.value)
            if parsed_int < param_range.min or parsed_int > param_range.max:
                raise ValueError(IndicatorCacheReason.PARAMETER_INVALID.value)
            decoded[name] = parsed_int
        else:
            parsed_decimal = _parse_decimal(value)
            min_value = Decimal(param_range.min)
            max_value = Decimal(param_range.max)
            if parsed_decimal < min_value or parsed_decimal > max_value:
                raise ValueError(IndicatorCacheReason.PARAMETER_INVALID.value)
            decoded[name] = parsed_decimal
    return decoded


def _reference_output(
    key: IndicatorKey,
    candles: Sequence[MarketCandle],
) -> IndicatorOutput | None:
    indicator = _instantiate_indicator(key)
    emitted: Output | None = None
    for candle in candles:
        emitted = indicator.update(_to_primitive_candle(candle))
    if emitted is None or not candles:
        return None
    return IndicatorOutput(
        key=key,
        close_epoch=_candle_close_epoch(candles[-1]),
        state=IndicatorNodeState.READY,
        reason=IndicatorCacheReason.OK,
        direction=emitted.direction,
        value=emitted.value,
        meta=tuple(sorted(emitted.meta.items())),
        candles_seen=len(candles),
        warmup_required=indicator.warmup_required,
    )


def _same_indicator_output(left: IndicatorOutput, right: IndicatorOutput) -> bool:
    return (
        left.close_epoch == right.close_epoch
        and left.state == right.state
        and left.reason == right.reason
        and left.direction == right.direction
        and left.value == right.value
        and left.meta == right.meta
    )


def _to_primitive_candle(candle: MarketCandle) -> Candle:
    ts = _candle_close_epoch(candle)
    return Candle(
        ts=ts - (ts % 60),
        o=candle.open,
        h=candle.high,
        l=candle.low,
        c=candle.close,
        tick_vol=candle.tick_volume,
    )


def _candle_close_epoch(candle: MarketCandle) -> int:
    value = candle.close_time.timestamp()
    if not math.isfinite(value) or value < 0:
        raise ValueError("indicator candle close_time is invalid")
    return int(value)


def _candle_fingerprint(candle: MarketCandle) -> tuple[object, ...]:
    return (
        candle.broker,
        candle.broker_symbol,
        candle.timeframe_seconds,
        candle.open_time,
        candle.close_time,
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.tick_volume,
    )


def _canonical_value(value: object) -> str:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid indicator params")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("indicator Decimal params must be finite")
        return format(value, "f")
    if isinstance(value, str):
        return format(_parse_decimal(value), "f")
    raise TypeError(f"unsupported indicator param type: {type(value).__name__}")


def _parse_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("indicator params must be decimal strings") from exc
    if not parsed.is_finite():
        raise ValueError("indicator params must be finite")
    return parsed


__all__ = [
    "INDICATOR_BOOTSTRAP_IDENTITY",
    "INDICATOR_EXECUTION_SEMANTICS_VERSION",
    "INDICATOR_PRIMITIVES_VERSION",
    "IndicatorCache",
    "IndicatorCacheReason",
    "IndicatorCacheStats",
    "IndicatorKey",
    "IndicatorNodeState",
    "IndicatorOutput",
    "ShadowComparison",
    "ShadowComparisonStatus",
    "canonical_auxiliary_identity",
    "canonical_indicator_params",
]
