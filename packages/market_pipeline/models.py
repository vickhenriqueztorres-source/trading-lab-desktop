from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from packages.domain.models import Broker


class ExecutionMode(StrEnum):
    DECISION_ONLY = "DECISION_ONLY"
    SIMULATED_EXECUTION = "SIMULATED_EXECUTION"
    BROKER_EXECUTION = "BROKER_EXECUTION"


class MarketSeriesHealth(StrEnum):
    INITIALIZING = "INITIALIZING"
    WARMING_UP = "WARMING_UP"
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    GAPPED = "GAPPED"
    BACKPRESSURED = "BACKPRESSURED"
    RECONNECTING = "RECONNECTING"
    CLOCK_UNTRUSTED = "CLOCK_UNTRUSTED"
    INCOMPATIBLE = "INCOMPATIBLE"
    FAILED = "FAILED"


class MarketHealthReason(StrEnum):
    HEALTHY = "MD_HEALTHY"
    INITIAL_WARMUP = "MD_INITIAL_WARMUP"
    SOURCE_STALE = "MD_SOURCE_STALE"
    GAP_DETECTED = "MD_GAP_DETECTED"
    BACKPRESSURE = "MD_BACKPRESSURE"
    RECONNECT_REQUIRED = "MD_RECONNECT_REQUIRED"
    CLOCK_UNTRUSTED = "MD_CLOCK_UNTRUSTED"
    SCHEMA_INCOMPATIBLE = "MD_SCHEMA_INCOMPATIBLE"
    STORAGE_FAILED = "MD_STORAGE_FAILED"
    SCOPE_MISMATCH = "MD_SCOPE_MISMATCH"
    HISTORY_EXHAUSTED = "MD_HISTORY_EXHAUSTED"
    STALE_CONNECTION_RESPONSE = "MD_STALE_CONNECTION_RESPONSE"
    CONTINUITY_UNPROVEN = "MD_CONTINUITY_UNPROVEN"
    SHADOW_DIVERGENCE = "MD_SHADOW_DIVERGENCE"


@dataclass(frozen=True, slots=True)
class MarketSeriesId:
    broker: Broker
    broker_symbol: str
    canonical_symbol: str
    product: str
    timeframe_seconds: int
    context: str = "PUBLIC_MARKET"

    def __post_init__(self) -> None:
        for value in (
            self.broker_symbol,
            self.canonical_symbol,
            self.product,
            self.context,
        ):
            if not value.strip():
                raise ValueError("market series identity cannot be blank")
        if self.timeframe_seconds <= 0:
            raise ValueError("market series timeframe must be positive")

    @property
    def key(self) -> str:
        return ":".join(
            (
                self.broker.value,
                self.broker_symbol,
                self.canonical_symbol,
                self.product,
                str(self.timeframe_seconds),
                self.context,
            )
        )


@dataclass(frozen=True, slots=True)
class TrustedClosedHorizon:
    source_epoch_seconds: int
    close_epoch_ms: int
    observed_monotonic: float

    def __post_init__(self) -> None:
        if self.source_epoch_seconds <= 0 or self.close_epoch_ms < 0:
            raise ValueError("trusted closed horizon is invalid")
        if self.observed_monotonic < 0:
            raise ValueError("trusted horizon monotonic time cannot be negative")


@dataclass(frozen=True, slots=True)
class MarketSeriesScheduleState:
    series_id: MarketSeriesId
    last_durable_close_epoch: int | None
    last_attempt_monotonic: float | None
    next_due_monotonic: float
    failure_count: int
    reconnect_generation: int
    health: MarketSeriesHealth
    recovery_required: bool
    reason: MarketHealthReason


@dataclass(frozen=True, slots=True)
class MarketPipelineHealthSnapshot:
    series_id: MarketSeriesId
    health: MarketSeriesHealth
    reason: MarketHealthReason
    last_durable_close: int | None
    last_source_event: str | None
    gap_count: int
    backpressure_active: bool
    reconnect_generation: int
    warmup_progress: int
    warmup_required: int
    dispatch_allowed: bool


@dataclass(frozen=True, slots=True)
class BrokerMarketHealth:
    broker: Broker
    health: MarketSeriesHealth
    active_series: int
    blocked_series: int


@dataclass(slots=True)
class MarketPipelineMetrics:
    backfill_requests: int = 0
    backfill_retries: int = 0
    backfill_failures: int = 0
    backfill_candles_received: int = 0
    backfill_duplicates: int = 0
    gap_count: int = 0
    gap_recovery_count: int = 0
    backpressure_count: int = 0
    reconnect_count: int = 0
    warmup_candles_loaded: int = 0
    shadow_candles_dispatched: int = 0
    shadow_decisions: int = 0
    partial_candles_received: int = 0
    partial_candles_persisted: int = 0
    partial_candles_dispatched: int = 0
    strategy_decisions_from_partial: int = 0
    live_ticks_received: int = 0
    live_ticks_duplicate: int = 0
    live_ticks_out_of_order: int = 0
    live_candles_closed: int = 0
    live_candles_duplicate: int = 0
    live_gap_count: int = 0
    live_poll_timeouts: int = 0
    live_dispatch_lag_ms_total: int = 0
    live_dispatch_lag_ms_max: int = 0
    live_replay_comparisons: int = 0
    live_replay_divergences: int = 0
    subscription_restores: int = 0
