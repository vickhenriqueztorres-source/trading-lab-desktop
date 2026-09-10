"""Core-owned IQ Option RSI execution using broker candles and durable orders."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from apps.core.execution_state import ExecutionState, TransportSupervisor
from apps.core.families.base import EvalResult
from apps.core.indicator_cache import (
    IndicatorCache,
    IndicatorCacheReason,
    IndicatorKey,
    IndicatorNodeState,
    ShadowComparison,
    ShadowComparisonStatus,
    canonical_indicator_params,
)
from apps.core.iqoption_candidates import (
    CandidateSignal,
    arbitrate,
    next_open_utc,
    resolve_candidates,
)
from apps.core.iqoption_connection_safety import (
    IQOptionMessageBudget,
    IQOptionMessageBudgetDecision,
)
from apps.core.iqoption_failures import IQFailurePolicy, ScopedFailure
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.iqoption_series_hub import (
    IQOPTION_SERIES_PRODUCT,
    IQOptionSeriesHub,
    IQOptionSeriesKey,
    IQOptionSeriesReason,
)
from apps.core.live_monitor import LiveMonitor
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSupervisor
from apps.core.runtime import CoreRuntime
from apps.core.worker_client import WorkerDispatchError
from packages.domain.market import (
    BrokerAccountBalance,
    BrokerClockSnapshot,
    BrokerInstrument,
    BrokerInstrumentAvailability,
    BrokerInstrumentCatalog,
    BrokerInstrumentProduct,
    MarketCandle,
)
from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    Direction,
    Money,
    OrderRequest,
    OrderState,
)
from packages.persistence.writer import (
    AccountBusyError,
    BrokerEventApplyResult,
    BrokerEventApplyStatus,
    RiskLimitExceededError,
)
from packages.protocol.ui_messages import UiIqOptionAssetRank, UiIqOptionExecutionMetrics
from packages.strategies.iqoption_rsi import (
    IQOptionRsiDemoStrategy,
    calculate_wilder_rsi,
)
from packages.strategies.models import RuntimeContext

logger = logging.getLogger("core.iqoption_auto_trader")

# Compatibility fixture for older unit doubles that do not implement catalogue
# discovery. Production uses the session catalogue and never this list.
IQOPTION_RADAR_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("EURUSD-OTC", "EUR/USD OTC"),
    ("GBPUSD-OTC", "GBP/USD OTC"),
    ("USDJPY-OTC", "USD/JPY OTC"),
    ("EURJPY-OTC", "EUR/JPY OTC"),
    ("GBPJPY-OTC", "GBP/JPY OTC"),
    ("AUDCAD-OTC", "AUD/CAD OTC"),
    ("NZDUSD-OTC", "NZD/USD OTC"),
    ("USDCHF-OTC", "USD/CHF OTC"),
    ("EURUSD", "EUR/USD"),
    ("GBPUSD", "GBP/USD"),
    ("USDJPY", "USD/JPY"),
    ("EURJPY", "EUR/JPY"),
    ("USDCHF", "USD/CHF"),
    ("AUDCAD", "AUD/CAD"),
    ("NZDUSD", "NZD/USD"),
    ("AUDUSD", "AUD/USD"),
)

IQOPTION_PRACTICE_ACCOUNT_ID = "IQOPTION_PRACTICE"
IQOPTION_ACTIVE_SUSPENSION_COOLDOWN_SECONDS = 5 * 60
IQOPTION_TELEMETRY_CACHE_TTL_SECONDS = 10.0
IQOPTION_INSTRUMENT_CATALOG_TTL_SECONDS = 60.0
IQOPTION_INSTRUMENT_CATALOG_MAX_STALE_SECONDS = 180.0
IQOPTION_PAYOUT_TICKET_MAX_AGE_SECONDS = 8.0
IQOPTION_CLOCK_MAX_SKEW_SECONDS = Decimal("120")


@dataclass(frozen=True, slots=True)
class _DispatchResult:
    order_id: str | None
    state: OrderState | None
    reason_code: str
    admission_blocked: bool = False

    @property
    def financially_accepted(self) -> bool:
        return self.state in {OrderState.ACCEPTED, OrderState.OPEN, OrderState.SETTLED}


@dataclass(frozen=True, slots=True)
class IqOptionExecutionFlags:
    """Independent entry-engine switches.

    The default keeps the audited legacy path active and the incremental engine
    disabled.  Shadow may observe, but it cannot submit orders.
    """

    legacy_entries_enabled: bool = True
    indicator_shadow_enabled: bool = True
    incremental_entries_enabled: bool = False

    def __post_init__(self) -> None:
        for value in (
            self.legacy_entries_enabled,
            self.indicator_shadow_enabled,
            self.incremental_entries_enabled,
        ):
            if type(value) is not bool:
                raise TypeError("IQ Option execution flags must be booleans")

    @property
    def fingerprint(self) -> tuple[bool, bool, bool]:
        return (
            self.legacy_entries_enabled,
            self.indicator_shadow_enabled,
            self.incremental_entries_enabled,
        )


class _EntryAdmissionBlocked(RuntimeError):
    """Raised exclusively by validation before the Core admission transaction."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class IqOptionAutoTrader:
    """Evaluate real closed candles and submit through the Core financial pipeline."""

    def __init__(
        self,
        supervisor_provider: Callable[[], ReadOnlyWorkerSupervisor | None],
        runtime_provider: Callable[[], CoreRuntime | None],
        risk_config_provider: Callable[[], IqOptionRiskConfig],
        operator_armed: Callable[[], bool],
        *,
        catalog_provider: Callable[[], Any] | None = None,
        account_type_provider: Callable[[], str] = lambda: "PRACTICE",
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        evaluation_interval_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        message_budget: IQOptionMessageBudget | None = None,
        monitor_provider: Callable[[], LiveMonitor | None] | None = None,
        execution_flags_provider: Callable[[], IqOptionExecutionFlags] | None = None,
        recovery_notifier: Callable[[str], None] | None = None,
        transport_supervisor: TransportSupervisor | None = None,
        reconcile_orders: Callable[[], None] | None = None,
    ) -> None:
        if evaluation_interval_seconds <= 0:
            raise ValueError("IQ Option evaluation interval must be positive")
        self._supervisor_provider = supervisor_provider
        self._runtime_provider = runtime_provider
        self._risk_config_provider = risk_config_provider
        self._operator_armed = operator_armed
        self._catalog_provider = catalog_provider
        self._account_type_provider = account_type_provider
        self._monitor_provider = monitor_provider
        self._execution_flags_provider = execution_flags_provider or IqOptionExecutionFlags
        self._recovery_notifier = recovery_notifier
        if transport_supervisor is None:
            # A standalone trader is constructed around an already supplied
            # supervisor. Lifecycle injects its persisted, initially degraded
            # controller while it is still reconnecting.
            transport_supervisor = TransportSupervisor(initially_armed=bool(operator_armed()))
            if transport_supervisor.armed_intent:
                transport_supervisor.mark_up()
        self._transport_supervisor = transport_supervisor
        self._reconcile_orders = reconcile_orders
        self._recovery_notified_generation: str | None = None
        self._observed_client: object | None = None
        # Negative availability only, never cached permission to submit.
        # Bounded by supported symbols; expiry requires a fresh payout probe.
        self._unavailable_assets: dict[str, tuple[float, str]] = {}
        self._execution_ticket: tuple[str, str, str | None, object, float, Decimal] | None = None
        self._utc_clock = utc_clock
        self._decision_epochs: dict[tuple[str, str, int, str, str], None] = {}
        self._last_payout_gate: dict[str, str | int | bool | None] | None = None
        self._timeframe_override_reported = False
        self._candidate_details: dict[str, str] = {}
        self._evaluation_interval = evaluation_interval_seconds
        self._monotonic = monotonic
        self._message_budget = message_budget or IQOptionMessageBudget()
        self._message_budget_pressure_reported = False
        self._operational_budget_pressure_reported = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._status_reason = "IQOPTION_BOT_DISARMED"
        self._failures = IQFailurePolicy()
        self._state_runtime: object | None = None
        self._pending_dispatch: dict[str, Any] | None = None
        self._last_dispatch_reasons: dict[str, str] = {}
        self._last_evaluated_epochs: dict[str, int] = {}
        self._armed_after_epoch: int | None = None
        self._warmup_cache_fingerprint: tuple[object, ...] | None = None
        self._series_hub = IQOptionSeriesHub(
            message_budget=self._message_budget,
            monotonic=self._monotonic,
            utc_clock=self._utc_clock,
        )
        self._indicator_cache = IndicatorCache()
        self._daily_trades_count = 0
        self._daily_profit_loss = Decimal(0)
        self._consecutive_losses = 0
        self._cooldown_until = 0.0
        self._last_rsi_value: Decimal | None = None
        self._scan_cursor = 0
        self._asset_ranking_by_symbol = {
            symbol: UiIqOptionAssetRank(
                symbol=symbol,
                display_name=display_name,
                rsi="--",
                condition="WAITING_DATA",
                status="WAITING_DATA",
            )
            for symbol, display_name in IQOPTION_RADAR_SYMBOLS
        }
        self._asset_ranking = self._ordered_ranking()
        self._strategy = IQOptionRsiDemoStrategy()
        self._latest_clock: BrokerClockSnapshot | None = None
        self._latest_balance: BrokerAccountBalance | None = None
        self._last_telemetry_probe = 0.0
        self._instrument_catalog: BrokerInstrumentCatalog | None = None
        self._catalog_discovery_supported = False
        self._instrument_catalog_received_mono = float("-inf")
        self._last_instrument_catalog_probe = float("-inf")
        self._cycle_started_mono: float | None = None
        self._decision_latencies_ms: list[int] = []
        self._waiting_since_mono: float | None = None
        self._fencing_discards = 0

    @property
    def latest_clock(self) -> BrokerClockSnapshot | None:
        with self._lock:
            return self._latest_clock

    @property
    def latest_balance(self) -> BrokerAccountBalance | None:
        with self._lock:
            return self._latest_balance

    @property
    def status_reason(self) -> str:
        with self._lock:
            return self._status_reason

    @property
    def execution_state(self) -> ExecutionState:
        return self._transport_supervisor.state

    @property
    def last_rsi(self) -> Decimal | None:
        with self._lock:
            return self._last_rsi_value

    @property
    def asset_ranking(self) -> tuple[UiIqOptionAssetRank, ...]:
        with self._lock:
            return self._asset_ranking

    def execution_metrics(self) -> UiIqOptionExecutionMetrics:
        """Return a bounded local snapshot; it never queries the database or broker."""
        with self._lock:
            samples = tuple(self._decision_latencies_ms[-512:])
            waiting = (
                None
                if self._waiting_since_mono is None
                else max(0, int(self._monotonic() - self._waiting_since_mono))
            )
            ranking = tuple(self._asset_ranking_by_symbol.values())
        flags = self._execution_flags_provider()
        mode = (
            "INCREMENTAL"
            if flags.incremental_entries_enabled
            else "SHADOW"
            if flags.indicator_shadow_enabled
            else "LEGACY"
        )
        stats = self._series_hub.stats
        cache = self._indicator_cache.stats
        catalog = self._catalog_provider() if self._catalog_provider is not None else None
        active = () if catalog is None else tuple(catalog.active_strategies.values())
        revision = None
        evidence_n = None
        evidence_oos = None
        if active:
            revision = str(active[0].entry.key)
            evidence_n = sum(int(item.entry.validated.ops_per_day) for item in active)
            evidence_oos = len(active)

        def percentile(percent: int) -> int:
            if not samples:
                return 0
            ordered = sorted(samples)
            index = min(len(ordered) - 1, (len(ordered) * percent + 99) // 100 - 1)
            return ordered[max(0, index)]

        has_market_evidence = any(
            item.rsi != "--" and item.status not in {"WAITING_DATA", "NO_EVIDENCE"}
            for item in ranking
        )
        return UiIqOptionExecutionMetrics(
            source=(
                "IQOPTION_BROKER_CLOSED_CANDLES" if has_market_evidence else "NO_MARKET_EVIDENCE"
            ),
            mode=mode,
            manifest_revision=revision,
            manifest_version=None if catalog is None else catalog.manifest_version,
            strategy_count=len(active),
            series_count=self._series_hub.active_series_count,
            unique_indicator_nodes=cache.active_nodes,
            cache_reuse_hits=cache.dedup_hits,
            fetches=stats.fetches_sent,
            indicator_updates=cache.indicator_calculations,
            decisions=len(samples),
            decision_p50_ms=percentile(50),
            decision_p95_ms=percentile(95),
            decision_p99_ms=percentile(99),
            queue_depth=stats.queue_full,
            close_delay_ms=percentile(95),
            cache_entries=cache.active_nodes,
            fencing_discards=self._fencing_discards,
            waiting_reason=self.status_reason if waiting is not None else None,
            waiting_seconds=0 if waiting is None else waiting,
            catalog_status="SIGNED" if active else "UNAVAILABLE",
            evidence_n=evidence_n,
            evidence_oos=evidence_oos,
            evidence_validity="SEALED" if active else "UNAVAILABLE",
        )

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="iqoption-auto-trader",
                daemon=True,
            )
            self._thread.start()

    def begin_new_run(self) -> None:
        """ARM never erases consumed signals, broker failures or financial evidence."""

        with self._lock:
            self._transport_supervisor.arm()
            self._execution_ticket = None
            self._armed_after_epoch = int(self._utc_clock().timestamp())
            self._status_reason = "IQOPTION_BOT_ARMED"

    def on_transport_down(self, reason_code: str) -> None:
        """Degrade transport without revoking the operator's arm intent."""

        state = self._transport_supervisor.mark_down(reason_code)
        with self._lock:
            self._execution_ticket = None
            self._status_reason = (
                "TRANSPORT_DOWN"
                if state is ExecutionState.ARMED_DEGRADED
                else (self._status_reason)
            )
        runtime = self._runtime_provider()
        if runtime is not None:
            runtime.event_sink.emit(
                "transport_down",
                broker=Broker.IQ_OPTION.value,
                execution_state=state.value,
                reason_code=reason_code,
            )

    def on_transport_up(self) -> None:
        """Restore transport state and schedule reconciliation before fresh evaluation."""

        previous = self._transport_supervisor.state
        state = self._transport_supervisor.mark_up()
        self._recovery_notified_generation = None
        if previous is ExecutionState.ARMED_DEGRADED and state is ExecutionState.ARMED:
            with self._lock:
                self._execution_ticket = None
                self._armed_after_epoch = int(self._utc_clock().timestamp())
                self._status_reason = "IQOPTION_BOT_ARMED"
        runtime = self._runtime_provider()
        if runtime is not None:
            runtime.event_sink.emit(
                "transport_up",
                broker=Broker.IQ_OPTION.value,
                execution_state=state.value,
            )
        if self._reconcile_orders is not None:
            self._reconcile_orders()

    def invalidate_entry_authority(self, reason: str) -> None:
        """Drop only ephemeral admission authority; monitoring and settlement continue."""
        with self._lock:
            self._execution_ticket = None
            self._armed_after_epoch = None
            self._status_reason = reason

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        with self._lock:
            self._status_reason = "IQOPTION_BOT_DISARMED"

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._evaluate_cycle()
            except Exception as exc:
                logger.warning("IQ Option evaluation failed: %s", type(exc).__name__)
                self._set_status("IQOPTION_EVALUATION_FAILED")
            self._stop.wait(self._evaluation_interval)

    def _evaluate_cycle(self) -> None:
        self._cycle_started_mono = self._monotonic()
        if self._transport_supervisor.state is ExecutionState.ARMED_DEGRADED:
            runtime = self._runtime_provider()
            logger.info(
                "IQ Option evaluation skipped",
                extra={
                    "broker": Broker.IQ_OPTION.value,
                    "reason_code": "TRANSPORT_DOWN",
                    "execution_state": ExecutionState.ARMED_DEGRADED.value,
                },
            )
            if runtime is not None:
                runtime.event_sink.emit(
                    "iqoption_evaluation_skipped",
                    broker=Broker.IQ_OPTION.value,
                    reason_code="TRANSPORT_DOWN",
                    execution_state=ExecutionState.ARMED_DEGRADED.value,
                )
            self._set_status("TRANSPORT_DOWN")
            return
        risk_config = self._risk_config_provider()
        selected_symbol = risk_config.symbol or "AUTO"
        automatic = selected_symbol == "AUTO"
        supervisor = self._supervisor_provider()
        runtime = self._runtime_provider()
        if supervisor is None or supervisor.client is None or runtime is None:
            self._set_status("IQOPTION_CONNECTION_REQUIRED")
            return
        if supervisor.client is not self._observed_client:
            self._observed_client = supervisor.client
            self._unavailable_assets.clear()
            self._series_hub.invalidate(reason="worker_replaced")
            self._indicator_cache.invalidate_all(reason=IndicatorCacheReason.SERIES_MISMATCH)
            self._recovery_notified_generation = None
            self._last_telemetry_probe = float("-inf")
            self._instrument_catalog = None
            self._catalog_discovery_supported = callable(
                getattr(supervisor.client, "iqoption_instrument_catalog", None)
            )
            self._instrument_catalog_received_mono = float("-inf")
            self._last_instrument_catalog_probe = float("-inf")
            with self._lock:
                self._latest_balance = None
                self._latest_clock = None
        now_mono = self._monotonic()
        if now_mono - self._last_telemetry_probe >= IQOPTION_TELEMETRY_CACHE_TTL_SECONDS:
            self._last_telemetry_probe = now_mono
            clock_fn = getattr(supervisor.client, "broker_clock", None)
            if callable(clock_fn):
                clock_budget = self._message_budget.try_acquire_operational(self._monotonic())
                self._report_operational_budget(runtime, clock_budget)
            if callable(clock_fn) and clock_budget.allowed:
                try:
                    clock = clock_fn()
                    with self._lock:
                        self._latest_clock = clock
                except WorkerDispatchError as exc:
                    with self._lock:
                        self._latest_clock = None
                    self._notify_session_failure(supervisor.client, exc.code.value)
                    return
                except Exception:
                    with self._lock:
                        self._latest_clock = None
            balance_fn = getattr(supervisor.client, "broker_balance", None)
            if callable(balance_fn):
                balance_budget = self._message_budget.try_acquire_operational(self._monotonic())
                self._report_operational_budget(runtime, balance_budget)
            if callable(balance_fn) and balance_budget.allowed:
                try:
                    balance = balance_fn()
                    with self._lock:
                        self._latest_balance = balance
                except WorkerDispatchError as exc:
                    self._notify_session_failure(supervisor.client, exc.code.value)
                    return
                except Exception:
                    with self._lock:
                        self._latest_balance = None
        if callable(getattr(supervisor.client, "broker_clock", None)):
            clock = self.latest_clock
            clock_fresh = clock is not None and (
                0 <= (self._utc_clock() - clock.local_received_at).total_seconds() <= 30
            )
            if not clock_fresh or clock is None:
                self._notify_session_failure(
                    supervisor.client,
                    "IQOPTION_CLOCK_UNAVAILABLE",
                )
                return
            if clock.round_trip_milliseconds > 1_000:
                runtime.event_sink.emit(
                    "iqoption_clock_rtt_observed",
                    broker=Broker.IQ_OPTION.value,
                    round_trip_milliseconds=clock.round_trip_milliseconds,
                )
            if abs(clock.estimated_offset_seconds) > IQOPTION_CLOCK_MAX_SKEW_SECONDS:
                runtime.health_gate.block_scope(
                    Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID, "MD_CLOCK_UNTRUSTED"
                )
                self._set_status("MD_CLOCK_UNTRUSTED")
                return
            runtime.health_gate.clear_scope(
                Broker.IQ_OPTION.value, IQOPTION_PRACTICE_ACCOUNT_ID, "MD_CLOCK_UNTRUSTED"
            )
        self._refresh_instrument_catalog(supervisor, runtime)
        try:
            self._restore_execution_state(runtime)
        except Exception:
            self._set_status("IQOPTION_EXECUTION_STATE_UNAVAILABLE")
            return
        flags = self._execution_flags_provider()
        if not flags.legacy_entries_enabled and not flags.incremental_entries_enabled:
            self._set_status("IQOPTION_ENTRY_ENGINE_DISABLED")
            return

        symbols = self._symbols_for_cycle(selected_symbol)
        if not symbols:
            self._set_status(
                "IQOPTION_INSTRUMENT_CATALOG_UNAVAILABLE"
                if self._catalog_discovery_supported and self._instrument_catalog is None
                else "IQOPTION_SYMBOL_UNSUPPORTED"
            )
            return

        catalog = self._catalog_provider() if self._catalog_provider is not None else None
        account_type = self._account_type_provider()
        if account_type.upper() not in {"DEMO", "PRACTICE"}:
            self._set_status("IQOPTION_PRACTICE_ACCOUNT_REQUIRED")
            return
        now_utc = self._utc_clock()
        # Manifest replacement invalidates the old warmup/history projection.
        fingerprint = (
            None if catalog is None else catalog.manifest_version,
            risk_config.active_strategy_key,
            flags.fingerprint,
            tuple(
                (
                    key,
                    info.entry.asset,
                    info.entry.timeframe,
                    info.status,
                    info.instance.warmup_required,
                )
                for key, info in sorted(
                    ({} if catalog is None else catalog.active_strategies).items()
                )
            ),
        )
        if fingerprint != self._warmup_cache_fingerprint:
            self._warmup_cache_fingerprint = fingerprint
            self._series_hub.invalidate(reason="manifest_or_strategy_changed")
            self._indicator_cache.invalidate_all(reason=IndicatorCacheReason.SERIES_MISMATCH)

        signals: list[CandidateSignal] = []
        candidate: tuple[str, str, Direction, Decimal, int, str] | None = None
        evaluation_waiting = False
        for symbol, display_name in symbols:
            candidates, rejected = resolve_candidates(
                catalog=catalog,
                symbol=symbol,
                mode="AUTO" if automatic else "SINGLE",
                active_strategy_key=risk_config.active_strategy_key,
                account_type=account_type,
                now_utc=now_utc,
            )
            mismatch_count = sum(reason == "ASSET_MISMATCH" for reason in rejected.values())
            if mismatch_count:
                self._record_decision(
                    runtime,
                    symbol,
                    "",
                    0,
                    int(now_utc.timestamp()) // 60 * 60,
                    "ASSET_MISMATCH",
                    phase="CANDIDATE_RESOLUTION_SUMMARY",
                    rejected_count=mismatch_count,
                )
            for key, reason in rejected.items():
                if reason == "ASSET_MISMATCH":
                    continue
                info = None if catalog is None else catalog.active_strategies.get(key)
                self._record_decision(
                    runtime,
                    symbol,
                    key,
                    0 if info is None else self._timeframe_seconds(info.entry.timeframe),
                    int(now_utc.timestamp()) // 60 * 60,
                    reason,
                    phase="CANDIDATE_RESOLUTION",
                    next_open=(
                        next_open_utc(info.entry.hours_utc, now_utc).isoformat()
                        if reason == "OUTSIDE_HOURS" and info is not None
                        else None
                    ),
                )
            self._candidate_details[symbol] = "; ".join(
                f"{key}: {reason}"
                + (
                    " · próxima abertura "
                    + next_open_utc(
                        catalog.active_strategies[key].entry.hours_utc, now_utc
                    ).isoformat()
                    if reason == "OUTSIDE_HOURS" and catalog is not None
                    else ""
                )
                for key, reason in rejected.items()
                if reason != "ASSET_MISMATCH" or not candidates
            )[:2048]
            if not candidates:
                evaluation_waiting = True
                reason = (
                    "OUTSIDE_HOURS"
                    if "OUTSIDE_HOURS" in rejected.values()
                    else "ASSET_MISMATCH"
                    if "ASSET_MISMATCH" in rejected.values()
                    else next(iter(rejected.values()), "NO_CANDIDATE")
                )
                self._record_decision(
                    runtime,
                    symbol,
                    "",
                    0,
                    int(now_utc.timestamp()) // 60 * 60,
                    "NO_CANDIDATE",
                    phase="CANDIDATE_SUMMARY",
                )
                self._update_rank(
                    symbol,
                    display_name,
                    rsi="--",
                    condition=reason,
                    selected=not automatic,
                    status=reason,
                )
                self._set_status(f"IQOPTION_{reason}")
                continue

            failure = self._failures.blocked(symbol, risk_config, self._monotonic())
            if failure is not None:
                evaluation_waiting = True
                self._render_failure(symbol, display_name, failure)
                continue
            warmups: dict[int, int] = {}
            for item in candidates:
                warmups[item.timeframe_seconds] = max(
                    warmups.get(item.timeframe_seconds, 0), item.warmup_required
                )
            details: list[str] = []
            for item in candidates:
                if (
                    item.timeframe_seconds != risk_config.timeframe_seconds
                    and item.entry.status != "demo_only"
                    and not self._timeframe_override_reported
                ):
                    runtime.event_sink.emit(
                        "TIMEFRAME_OVERRIDDEN_BY_MANIFEST",
                        strategy_key=item.key,
                        configured_timeframe=risk_config.timeframe_seconds,
                        timeframe=item.timeframe_seconds,
                    )
                    self._timeframe_override_reported = True
                try:
                    candles = self._candles_for_closed_interval(
                        supervisor=supervisor,
                        runtime=runtime,
                        symbol=symbol,
                        timeframe=item.timeframe_seconds,
                        warmup_need=warmups[item.timeframe_seconds],
                        strategy_key=item.key,
                    )
                except Exception as exc:
                    evaluation_waiting = True
                    logger.info("IQ candle request failed: %s", type(exc).__name__)
                    runtime.health_gate.block_scope(
                        Broker.IQ_OPTION.value, "market-data", "HG_MARKET_DATA_DISCONNECTED"
                    )
                    self._notify_session_failure(
                        supervisor.client,
                        exc.code.value
                        if isinstance(exc, WorkerDispatchError)
                        else "IQOPTION_BROKER_SESSION_UNAVAILABLE",
                    )
                    self._set_status("IQOPTION_MARKET_DATA_UNAVAILABLE")
                    self._update_rank(
                        symbol,
                        display_name,
                        rsi="--",
                        condition="DATA_UNAVAILABLE",
                        selected=not automatic,
                        status="DATA_UNAVAILABLE",
                    )
                    continue
                if candles is None:
                    self._update_rank(
                        symbol,
                        display_name,
                        rsi="--",
                        condition="MESSAGE_BUDGET_EXHAUSTED",
                        selected=not automatic,
                        status="WAITING_BUDGET",
                    )
                    self._set_status("IQOPTION_MESSAGE_BUDGET_EXHAUSTED")
                    return
                if len(candles) < item.warmup_required:
                    evaluation_waiting = True
                    self._render_eval_waiting(
                        symbol,
                        display_name,
                        EvalResult(
                            None, "WARMING_UP", len(candles), item.warmup_required, None, None, None
                        ),
                        selected=not automatic,
                    )
                    continue
                runtime.health_gate.clear_scope(
                    Broker.IQ_OPTION.value, "market-data", "HG_MARKET_DATA_DISCONNECTED"
                )
                self._recovery_notified_generation = None
                context = RuntimeContext(
                    strategy_id=item.key,
                    strategy_version="1.0.0",
                    broker=Broker.IQ_OPTION,
                    account_id=IQOPTION_PRACTICE_ACCOUNT_ID,
                    product="BINARY_OPTION",
                    symbol=symbol,
                    timeframe_seconds=item.timeframe_seconds,
                    configuration_version="1.0.0",
                )
                rsi = Decimal("50")
                try:
                    if item.entry.status == "demo_only":
                        direction, rsi, stage = self._evaluate_local_rsi_candidate(
                            supervisor=supervisor,
                            runtime=runtime,
                            symbol=symbol,
                            timeframe=item.timeframe_seconds,
                            candles=candles,
                            context=context,
                            flags=flags,
                            strategy_key=item.key,
                        )
                    elif not flags.legacy_entries_enabled:
                        direction, stage = None, "INCREMENTAL_ENGINE_UNSUPPORTED"
                    else:
                        assert catalog is not None  # admitted by resolver, never a fallback
                        result = catalog.active_strategies[item.key].instance.evaluate_detailed(
                            candles, context
                        )
                        stage, direction = result.stage, result.direction
                        if stage in {"WARMING_UP", "TICK_VOLUME_UNAVAILABLE"}:
                            evaluation_waiting = True
                            self._render_eval_waiting(
                                symbol, display_name, result, selected=not automatic
                            )
                            continue
                        if len(candles) >= 15:
                            rsi = calculate_wilder_rsi([c.close for c in candles])
                except (TypeError, ValueError):
                    direction, stage = None, "INVALID_DATA"
                epoch = int(candles[-1].close_time.timestamp())
                self._record_decision(
                    runtime,
                    symbol,
                    item.key,
                    item.timeframe_seconds,
                    epoch,
                    stage,
                    phase="EVALUATION",
                )
                details.append(f"{item.entry.display_name_pt} [{item.entry.timeframe}]: {stage}")
                self._update_rank(
                    symbol,
                    display_name,
                    rsi=f"{rsi:.1f}",
                    condition=(
                        "OVERSOLD"
                        if direction is Direction.CALL
                        else "OVERBOUGHT"
                        if direction is Direction.PUT
                        else stage
                    ),
                    selected=not automatic or direction is not None,
                    status="TRIGGERED" if direction is not None else "MONITORING",
                    direction=None if direction is None else direction.value,
                    revision=item.key,
                    readiness="READY",
                    signal_observed=direction is not None,
                    candidate_eligible=direction is not None,
                )
                if direction is not None:
                    unavailable = self._unavailable_assets.get(symbol)
                    if unavailable is not None and self._monotonic() < unavailable[0]:
                        evaluation_waiting = True
                        details.append(unavailable[1])
                        self._record_decision(
                            runtime,
                            symbol,
                            item.key,
                            item.timeframe_seconds,
                            epoch,
                            unavailable[1],
                            phase="AVAILABILITY_GATE",
                        )
                        self._update_rank(
                            symbol,
                            display_name,
                            rsi=f"{rsi:.1f}",
                            condition=unavailable[1],
                            selected=False,
                            status="MARKET_UNAVAILABLE",
                            signal_observed=True,
                            candidate_eligible=False,
                        )
                    else:
                        self._unavailable_assets.pop(symbol, None)
                        signals.append(CandidateSignal(item, direction, rsi, epoch))
            if details:
                self._candidate_details[symbol] = "; ".join(
                    details + [self._candidate_details.get(symbol, "")]
                )[:2048]
                with self._lock:
                    rank = self._asset_ranking_by_symbol[symbol]
                    self._asset_ranking_by_symbol[symbol] = replace(
                        rank, candidate_details=self._candidate_details[symbol]
                    )
                    self._asset_ranking = self._ordered_ranking()

        winner = arbitrate(signals)
        if signals and winner is None:
            evaluation_waiting = True
            self._set_status("IQOPTION_SIGNAL_CONFLICT")
            for signal in signals:
                asset = signal.candidate.entry.asset
                self._record_decision(
                    runtime,
                    asset,
                    signal.candidate.key,
                    signal.candidate.timeframe_seconds,
                    signal.epoch,
                    "IQOPTION_SIGNAL_CONFLICT",
                    phase="ARBITRATION",
                )
                self._update_rank(
                    asset,
                    self._display_name_for(asset),
                    rsi="--",
                    condition="SIGNAL_CONFLICT",
                    selected=False,
                    status="SIGNAL_CONFLICT",
                )
        if winner is not None:
            symbol = winner.candidate.entry.asset
            display_name = self._display_name_for(symbol)
            candidate = (
                symbol,
                display_name,
                winner.direction,
                winner.rsi,
                winner.epoch,
                winner.candidate.key,
            )

        if not self._operator_armed():
            if candidate is not None:
                self._record_decision(
                    runtime,
                    candidate[0],
                    candidate[5],
                    winner.candidate.timeframe_seconds if winner is not None else 60,
                    candidate[4],
                    "IQOPTION_BOT_DISARMED",
                    phase="ADMISSION",
                )
            self._set_status("IQOPTION_BOT_DISARMED")
            return
        if candidate is None:
            if evaluation_waiting:
                return
            if automatic:
                self._set_status(f"AUTO_SCAN_REAL_DATA ({len(self._executable_symbols())} ASSETS)")
            else:
                self._set_status(f"IQOPTION_WAITING_RSI_SIGNAL ({selected_symbol})")
            return

        symbol, display_name, direction, rsi, candle_epoch, strat_key = candidate
        with self._lock:
            self._last_rsi_value = rsi
        if self._last_evaluated_epochs.get(symbol, -1) >= candle_epoch:
            self._record_decision(
                runtime,
                symbol,
                strat_key,
                winner.candidate.timeframe_seconds if winner is not None else 60,
                candle_epoch,
                "IQOPTION_SIGNAL_ALREADY_CONSUMED",
                phase="ADMISSION",
            )
            self._set_status(
                self._last_dispatch_reasons.get(symbol)
                or f"SINAL_CONSUMIDO: {display_name} {direction.value} @ RSI={rsi:.1f}"
            )
            return
        if self._has_nonterminal_iq_order(runtime):
            self._record_decision(
                runtime,
                symbol,
                strat_key,
                winner.candidate.timeframe_seconds if winner is not None else 60,
                candle_epoch,
                "IQOPTION_ORDER_IN_FLIGHT",
                phase="ADMISSION",
            )
            self._set_status("IQOPTION_ORDER_IN_FLIGHT")
            return
        if self._armed_after_epoch is not None and candle_epoch <= self._armed_after_epoch:
            self._record_decision(
                runtime,
                symbol,
                strat_key,
                winner.candidate.timeframe_seconds if winner is not None else 60,
                candle_epoch,
                "IQOPTION_NEW_SIGNAL_REQUIRED_AFTER_ARM",
                phase="ADMISSION",
            )
            self._set_status("IQOPTION_NEW_SIGNAL_REQUIRED_AFTER_ARM")
            return

        risk_reason = self._risk_block_reason(risk_config)
        if risk_reason is not None:
            self._record_decision(
                runtime,
                symbol,
                strat_key,
                winner.candidate.timeframe_seconds if winner is not None else 60,
                candle_epoch,
                risk_reason,
                phase="RISK_GATE",
            )
            self._set_status(risk_reason)
            return

        try:
            manifest_context = self._prepare_execution(symbol, strat_key, supervisor.client)
        except Exception as exc:
            reason = (
                exc.code.value
                if isinstance(exc, WorkerDispatchError)
                else str(exc)
                if isinstance(exc, RuntimeError)
                else "IQOPTION_PAYOUT_UNAVAILABLE"
            )
            if not reason or not all(c.isupper() or c == "_" for c in reason):
                reason = "IQOPTION_PAYOUT_UNAVAILABLE"
            payout, payout_min, payout_age_ms, payout_allowed = self._payout_event_fields(
                symbol, strat_key
            )
            self._record_decision(
                runtime,
                symbol,
                strat_key,
                winner.candidate.timeframe_seconds if winner else 60,
                candle_epoch,
                reason,
                phase="PAYOUT_GATE",
                payout=payout,
                payout_min=payout_min,
                payout_age_ms=payout_age_ms,
                payout_allowed=payout_allowed,
            )
            self._set_status(reason)
            if reason in {"IQOPTION_ACTIVE_SUSPENDED", "IQOPTION_ACTIVE_UNAVAILABLE"}:
                self._unavailable_assets[symbol] = (self._monotonic() + 60, reason)
                self._update_rank(
                    symbol,
                    display_name,
                    rsi=f"{rsi:.1f}",
                    condition=reason,
                    selected=False,
                    status="MARKET_UNAVAILABLE",
                    signal_observed=True,
                    candidate_eligible=False,
                )
            if reason in {
                "IQOPTION_WEBSOCKET_UNAVAILABLE",
                "IQOPTION_REQUEST_TIMEOUT",
                "IQOPTION_RESPONSE_TOO_LARGE",
                "IQOPTION_AUTH_FAILED",
                "IPC_CONNECTION_LOST",
                "WORKER_CRASHED",
            }:
                self._notify_session_failure(supervisor.client, reason)
            failure = self._failures.current(symbol, risk_config)
            if failure is not None:
                self._failures.probe_failed(failure, self._monotonic())
                self._save_execution_state(runtime)
            return

        # Consume before dispatch. A rejection or ambiguous response must never
        # turn the same market signal into an automatic financial retry.
        previous_epoch = self._last_evaluated_epochs.get(symbol)
        self._last_evaluated_epochs[symbol] = candle_epoch
        correlation_id = str(uuid4())
        self._pending_dispatch = {
            "correlation_id": correlation_id,
            "symbol": symbol,
            "config": asdict(risk_config),
        }
        # Crash before/after submit retains the consumed signal. The pending
        # correlation is resolved against durable order/outbox evidence on startup.
        self._save_execution_state(runtime)
        dispatch = self._dispatch_order(
            runtime,
            symbol=symbol,
            direction=direction,
            risk_config=risk_config,
            strategy_id=strat_key or risk_config.strategy_id,
            manifest_context=manifest_context,
            correlation_id=correlation_id,
        )
        payout, payout_min, payout_age_ms, payout_allowed = self._payout_event_fields(
            symbol, strat_key
        )
        self._record_decision(
            runtime,
            symbol,
            strat_key,
            winner.candidate.timeframe_seconds if winner is not None else 60,
            candle_epoch,
            dispatch.reason_code,
            phase="ADMISSION" if dispatch.order_id is None else "SUBMISSION",
            correlation_id=correlation_id,
            order_id=dispatch.order_id,
            payout=payout,
            payout_min=payout_min,
            payout_age_ms=payout_age_ms,
            payout_allowed=payout_allowed,
        )
        self._pending_dispatch = None
        self._last_dispatch_reasons.pop(symbol, None)
        if dispatch.reason_code == "MANIFEST_MONITOR_PENDING":
            # Writer proves zero new intent/reservation/outbox and no send occurred.
            # This is a pre-admission gate, not a retry of an uncertain submission.
            if previous_epoch is None:
                self._last_evaluated_epochs.pop(symbol, None)
            else:
                self._last_evaluated_epochs[symbol] = previous_epoch
            self._save_execution_state(runtime)
            self._set_status(dispatch.reason_code)
            return
        if dispatch.financially_accepted:
            self._mark_rank_execution(
                symbol, submitted=True, accepted=True, terminal=False, reason=dispatch.reason_code
            )
            if symbol in self._failures.failures or "*" in self._failures.failures:
                runtime.event_sink.emit(
                    "iqoption_execution_recovered",
                    symbol=symbol,
                    evidence="NEW_SIGNAL_ACCEPTED_AFTER_READ_ONLY_PROBE",
                )
            self._failures.accepted(symbol)
            self._daily_trades_count += 1
            stake = Decimal(risk_config.stake_minor_units) / Decimal(100)
            self._set_status(
                f"ORDEM_ACEITA: {display_name} {direction.value} @ RSI={rsi:.1f} (USD {stake:.2f})"
            )
        elif dispatch.state in {
            OrderState.UNKNOWN,
            OrderState.RECONCILING,
            OrderState.SETTLEMENT_UNKNOWN,
        }:
            self._mark_rank_execution(
                symbol, submitted=True, accepted=False, terminal=False, reason=dispatch.reason_code
            )
            self._daily_trades_count += 1
            self._set_status("IQOPTION_ORDER_UNKNOWN_RECONCILIATION_REQUIRED")
        else:
            self._mark_rank_execution(
                symbol, submitted=True, accepted=False, terminal=True, reason=dispatch.reason_code
            )
            self._last_dispatch_reasons[symbol] = dispatch.reason_code
            # The authoritative HealthGate is reevaluated by the Core; do not
            # clone its blockers into an unrelated permanent global latch.
            if not dispatch.admission_blocked and not dispatch.reason_code.startswith(
                ("HG_", "DB_")
            ):
                failure = self._failures.record(
                    dispatch.reason_code,
                    symbol,
                    risk_config,
                    self._monotonic(),
                    confirmed_rejection=dispatch.state
                    in {OrderState.REJECTED, OrderState.SEND_BLOCKED},
                )
                self._render_failure(symbol, display_name, failure)
                runtime.event_sink.emit(
                    "iqoption_execution_failure",
                    reason_code=failure.reason,
                    scope=failure.symbol,
                    recovery=failure.mode.value,
                    attempts=failure.attempts,
                )
            self._set_status(dispatch.reason_code)
        self._save_execution_state(runtime)

    def _restore_execution_state(self, runtime: CoreRuntime) -> None:
        if self._state_runtime is runtime:
            return
        writer = getattr(runtime, "writer", None)
        payload = None if writer is None else writer.load_iqoption_execution_state()
        if payload is not None:
            if payload.get("version") != 1:
                raise ValueError("IQOPTION_EXECUTION_STATE_INVALID")
            signals = payload["signals"]
            if (
                not isinstance(signals, dict)
                or len(signals) > 64
                or any(type(v) is not int or v < 0 for v in signals.values())
            ):
                raise ValueError("IQOPTION_EXECUTION_STATE_INVALID")
            self._failures.restore(payload["policy"], self._monotonic(), self._utc_clock())
            self._last_evaluated_epochs = dict(signals)
            pending = payload.get("pending")
            evidence = payload.get("pending_evidence")
            if pending is not None and evidence is not None:
                state = OrderState(evidence["state"])
                if state in {OrderState.REJECTED, OrderState.SEND_BLOCKED}:
                    self._failures.record(
                        self._stable_rejection_reason(evidence["state_reason"]),
                        pending["symbol"],
                        IqOptionRiskConfig(**pending["config"]),
                        self._monotonic(),
                        confirmed_rejection=True,
                    )
                # Nonterminal evidence remains owned by recovery/HealthGate.
                # No matching intent means the crash preceded durable admission.
            self._pending_dispatch = None
            self._save_execution_state(runtime)
        self._state_runtime = runtime

    def _save_execution_state(self, runtime: CoreRuntime) -> None:
        writer = getattr(runtime, "writer", None)
        if writer is not None:
            writer.save_iqoption_execution_state(
                {
                    "version": 1,
                    "signals": self._last_evaluated_epochs,
                    "policy": self._failures.dump(self._monotonic(), self._utc_clock()),
                    "pending": self._pending_dispatch,
                }
            )

    def _render_failure(self, symbol: str, display_name: str, failure: ScopedFailure) -> None:
        self._candidate_details[symbol] = failure.detail(self._monotonic())
        self._update_rank(
            symbol,
            display_name,
            rsi="--",
            condition=failure.reason,
            selected=True,
            status=failure.mode.value,
        )
        self._set_status(failure.reason)

    def _notify_session_failure(self, client: object, reason: str) -> None:
        current = self._supervisor_provider()
        if current is None or current.client is not client:
            return
        self.on_transport_down(reason)
        generation = self._series_generation(client)
        if self._recovery_notifier is not None and self._recovery_notified_generation != generation:
            self._recovery_notified_generation = generation
            self._recovery_notifier(reason)

    def _prepare_execution(self, symbol: str, key: str, client: Any) -> str | None:
        self._execution_ticket = None
        self._last_payout_gate = None
        if self._account_type_provider().upper() not in {"DEMO", "PRACTICE"}:
            raise RuntimeError("IQOPTION_REAL_ACCOUNT_FORBIDDEN")
        if not self._operator_armed():
            raise RuntimeError("IQOPTION_BOT_DISARMED")
        started = self._monotonic()
        has_payout_probe = callable(getattr(client, "iqoption_binary_payout", None))
        if key == "iqoption-rsi-demo" and not has_payout_probe:
            payout = Decimal("0")
        else:
            budget = self._message_budget.try_acquire(self._monotonic())
            if budget.pressure and not self._message_budget_pressure_reported:
                self._message_budget_pressure_reported = True
                runtime = self._runtime_provider()
                if runtime is not None:
                    runtime.event_sink.emit(
                        "iqoption_message_budget_pressure",
                        used_in_window=budget.used_in_window,
                        limit=budget.limit,
                    )
            elif not budget.pressure:
                self._message_budget_pressure_reported = False
            if not budget.allowed:
                raise RuntimeError("IQOPTION_MESSAGE_BUDGET_EXHAUSTED")
            payout = client.iqoption_binary_payout(symbol)
        if (key != "iqoption-rsi-demo" or has_payout_probe) and (
            not isinstance(payout, Decimal) or not payout.is_finite() or not 0 < payout <= 1
        ):
            raise RuntimeError("IQOPTION_PAYOUT_UNAVAILABLE")
        self._last_payout_gate = {
            "symbol": symbol,
            "strategy_key": key,
            "payout": str(payout),
            "payout_min": None,
            "payout_age_ms": max(0, int((self._monotonic() - started) * 1000)),
            "payout_allowed": True,
        }
        context = self._check_manifest_execution(symbol, key, payout)
        self._execution_ticket = (symbol, key, context, client, started, payout)
        self._validate_execution_ticket(symbol, key, context, current_client=client)
        return context

    def _check_manifest_execution(self, symbol: str, key: str, payout: Decimal) -> str | None:
        account = self._account_type_provider().upper()
        if account not in {"DEMO", "PRACTICE"}:
            raise RuntimeError("IQOPTION_REAL_ACCOUNT_FORBIDDEN")
        if key == "iqoption-rsi-demo":
            config = self._risk_config_provider()
            if config.symbol != symbol or config.active_strategy_key != key:
                raise RuntimeError("NO_CANDIDATE")
            # Explicit unvalidated Practice recipe has no fabricated Wilson/SPRT.
            return None
        catalog = None if self._catalog_provider is None else self._catalog_provider()
        if catalog is None:
            raise RuntimeError("STRATEGY_NOT_FOUND")
        allowed, reason, payout_result = catalog.is_eligible(
            key, account_type=account, current_payout=payout, now_utc=self._utc_clock()
        )
        if payout_result is not None:
            ticket = self._last_payout_gate
            self._last_payout_gate = {
                "symbol": symbol,
                "strategy_key": key,
                "payout": str(payout_result.payout),
                "payout_min": str(payout_result.payout_min),
                "payout_age_ms": (0 if ticket is None else int(ticket.get("payout_age_ms") or 0)),
                "payout_allowed": payout_result.allowed,
            }
        if not allowed:
            raise RuntimeError(reason)
        info = catalog.get_strategy(key)
        if info is None or info.entry.asset != symbol:
            raise RuntimeError("ASSET_MISMATCH")
        return json.dumps(LiveMonitor.binding(info.entry), sort_keys=True, separators=(",", ":"))

    def _validate_execution_ticket(
        self,
        symbol: str,
        key: str,
        context: str | None,
        *,
        current_client: object | None = None,
    ) -> None:
        ticket = self._execution_ticket
        supervisor = None if current_client is not None else self._supervisor_provider()
        active_client = (
            current_client
            if current_client is not None
            else (None if supervisor is None else supervisor.client)
        )
        if (
            ticket is None
            or active_client is not ticket[3]
            or (symbol, key, context) != ticket[:3]
            or not 0 <= self._monotonic() - ticket[4] < IQOPTION_PAYOUT_TICKET_MAX_AGE_SECONDS
        ):
            raise RuntimeError("IQOPTION_PAYOUT_STALE")
        if not self._operator_armed():
            raise RuntimeError("IQOPTION_BOT_DISARMED")
        if self._check_manifest_execution(symbol, key, ticket[5]) != context:
            raise RuntimeError("MANIFEST_CHANGED_DURING_ENTRY")

    def validate_runtime_entry(self, request: OrderRequest) -> None:
        """Core boundary: no bypass through a direct runtime submission."""
        if request.broker is Broker.IQ_OPTION:
            try:
                self._validate_iq_admission(request)
            except RuntimeError as exc:
                raise _EntryAdmissionBlocked(str(exc)) from exc

    def _validate_iq_admission(self, request: OrderRequest) -> None:
        self._validate_execution_ticket(
            request.symbol, request.strategy_id, request.manifest_context
        )
        if (
            request.account_id != IQOPTION_PRACTICE_ACCOUNT_ID
            or request.product != "BINARY_OPTION"
            or request.duration != 1
            or request.duration_unit != "m"
        ):
            raise RuntimeError("IQOPTION_EXECUTION_CONTEXT_MISMATCH")
        self._execution_ticket = None

    def _dispatch_order(
        self,
        runtime: CoreRuntime,
        *,
        symbol: str,
        direction: Direction,
        risk_config: IqOptionRiskConfig,
        strategy_id: str | None = None,
        manifest_context: str | None = None,
        correlation_id: str | None = None,
    ) -> _DispatchResult:
        try:
            dispatch_budget = self._message_budget.try_acquire_operational(self._monotonic())
            self._report_operational_budget(runtime, dispatch_budget)
            if not dispatch_budget.allowed:
                raise _EntryAdmissionBlocked("IQOPTION_OPERATIONAL_MESSAGE_BUDGET_EXHAUSTED")
            persisted = runtime.submit(
                OrderRequest(
                    correlation_id=correlation_id or str(uuid4()),
                    broker=Broker.IQ_OPTION,
                    account_id=IQOPTION_PRACTICE_ACCOUNT_ID,
                    product="BINARY_OPTION",
                    symbol=symbol,
                    direction=direction,
                    amount=Money(risk_config.stake_minor_units, risk_config.currency),
                    strategy_id=strategy_id or risk_config.strategy_id,
                    strategy_version="1.0.0",
                    deadline_at=self._utc_clock() + timedelta(seconds=15),
                    duration=1,
                    duration_unit="m",
                    manifest_context=manifest_context,
                )
            )
            row = runtime.reader.one("orders", "order_id", persisted.order_id)
            if row is None:
                return _DispatchResult(
                    persisted.order_id,
                    None,
                    "IQOPTION_ORDER_PROJECTION_MISSING",
                )
            state = OrderState(str(row["state"]))
            if state in {OrderState.REJECTED, OrderState.SEND_BLOCKED}:
                outbox = runtime.reader.outbox_for_intent(persisted.intent_id)
                reason = None if outbox is None else outbox.get("state_reason")
                return _DispatchResult(
                    persisted.order_id,
                    state,
                    self._stable_rejection_reason(reason),
                )
            return _DispatchResult(
                persisted.order_id,
                state,
                f"IQOPTION_ORDER_{state.value}",
            )
        except Exception as exc:
            logger.warning("IQ Option Core submission failed: %s", type(exc).__name__)
            reason_code = str(getattr(exc, "reason_code", "")).strip()
            if not reason_code and isinstance(exc, RuntimeError):
                marker = "Health Gate blocked: "
                message = str(exc)
                if message.startswith(marker):
                    reason_code = message[len(marker) :].strip()
            return _DispatchResult(
                None,
                None,
                self._stable_rejection_reason(reason_code)
                if reason_code
                else "IQOPTION_ORDER_SUBMISSION_FAILED",
                admission_blocked=isinstance(
                    exc, (_EntryAdmissionBlocked, AccountBusyError, RiskLimitExceededError)
                ),
            )

    @staticmethod
    def _stable_rejection_reason(reason: object) -> str:
        raw = "" if reason is None else str(reason).strip()
        normalized = raw.lower()
        if "investment amount is smaller" in normalized or "allowed minimum" in normalized:
            return "IQOPTION_STAKE_BELOW_BROKER_MINIMUM"
        if normalized in {"purchase time is over", "purchase time has expired"}:
            return "IQOPTION_PURCHASE_TIME_EXPIRED"
        if normalized in {"too many requests", "rate limit exceeded"}:
            return "IQOPTION_RATE_LIMITED"
        if any(
            marker in normalized
            for marker in (
                "active is suspended",
                "active suspended",
                "asset is suspended",
                "asset is not available",
                "active is not available",
                "instrument is not available",
                "instrument is closed",
                "market is closed",
            )
        ):
            return "IQOPTION_ACTIVE_SUSPENDED"
        if (
            raw
            and len(raw) <= 64
            and all(
                character.isupper() or character.isdigit() or character == "_" for character in raw
            )
        ):
            return raw
        return "IQOPTION_ORDER_REJECTED_REMOTE"

    def _record_decision(
        self,
        runtime: CoreRuntime,
        symbol: str,
        key: str,
        timeframe: int,
        epoch: int,
        reason: str,
        *,
        phase: str = "EVALUATION",
        next_open: str | None = None,
        correlation_id: str | None = None,
        order_id: str | None = None,
        payout: str | None = None,
        payout_min: str | None = None,
        payout_age_ms: int | None = None,
        payout_allowed: bool | None = None,
        rejected_count: int | None = None,
    ) -> None:
        identity = (symbol, key, epoch, phase, reason)
        if identity in self._decision_epochs:
            return
        # Bounded deduplication, no database reads in candidate routing.
        self._decision_epochs[identity] = None
        if self._cycle_started_mono is not None:
            elapsed = max(0, int((self._monotonic() - self._cycle_started_mono) * 1000))
            with self._lock:
                self._decision_latencies_ms.append(elapsed)
                if len(self._decision_latencies_ms) > 512:
                    del self._decision_latencies_ms[:-512]
                self._waiting_since_mono = (
                    self._monotonic() if reason != "OK" and phase != "SUBMISSION" else None
                )
        if len(self._decision_epochs) > 4096:
            self._decision_epochs.pop(next(iter(self._decision_epochs)))
        decision_material = (
            f"IQ_OPTION|{IQOPTION_PRACTICE_ACCOUNT_ID}|{symbol}|{key}|{timeframe}|{epoch}"
        )
        decision_id = hashlib.sha256(decision_material.encode("utf-8")).hexdigest()[:24]
        supervisor = self._supervisor_provider()
        generation = "UNAVAILABLE"
        if supervisor is not None:
            with suppress(RuntimeError):
                generation = self._series_generation(supervisor.client)
                # Diagnostics cannot turn a concurrent disconnect into a trader
                # crash or claim evidence from a missing client generation.
        runtime.event_sink.emit(
            "iqoption_decision",
            decision_id=decision_id,
            phase=phase,
            broker=Broker.IQ_OPTION.value,
            account_scope=IQOPTION_PRACTICE_ACCOUNT_ID,
            generation=generation,
            symbol=symbol,
            strategy_key=key,
            timeframe=timeframe,
            epoch=epoch,
            stage_rejected=reason,
            next_open_utc=next_open,
            correlation_id=correlation_id,
            order_id=order_id,
            payout=payout,
            payout_min=payout_min,
            payout_age_ms=payout_age_ms,
            payout_allowed=payout_allowed,
            rejected_count=rejected_count,
        )

    def _payout_event_fields(
        self,
        symbol: str,
        key: str,
    ) -> tuple[str | None, str | None, int | None, bool | None]:
        fields = self._last_payout_gate
        if fields is None or fields.get("symbol") != symbol or fields.get("strategy_key") != key:
            return None, None, None, None
        payout = fields.get("payout")
        payout_min = fields.get("payout_min")
        payout_age_ms = fields.get("payout_age_ms")
        payout_allowed = fields.get("payout_allowed")
        return (
            payout if isinstance(payout, str) else None,
            payout_min if isinstance(payout_min, str) else None,
            payout_age_ms if type(payout_age_ms) is int else None,
            payout_allowed if type(payout_allowed) is bool else None,
        )

    def _report_operational_budget(
        self,
        runtime: CoreRuntime,
        decision: IQOptionMessageBudgetDecision,
    ) -> None:
        if decision.pressure and not self._operational_budget_pressure_reported:
            self._operational_budget_pressure_reported = True
            runtime.event_sink.emit(
                "iqoption_operational_budget_pressure",
                used_in_window=decision.used_in_window,
                limit=decision.limit,
            )
        elif not decision.pressure:
            self._operational_budget_pressure_reported = False

    @staticmethod
    def _timeframe_seconds(timeframe: str) -> int:
        return {"M1": 60, "M5": 300, "M15": 900}.get(timeframe, 0)

    def _evaluate_local_rsi_candidate(
        self,
        *,
        supervisor: ReadOnlyWorkerSupervisor,
        runtime: CoreRuntime,
        symbol: str,
        timeframe: int,
        candles: list[MarketCandle],
        context: RuntimeContext,
        flags: IqOptionExecutionFlags,
        strategy_key: str,
    ) -> tuple[Direction | None, Decimal, str]:
        if flags.incremental_entries_enabled:
            shadow = self._shadow_rsi14(
                supervisor=supervisor,
                symbol=symbol,
                timeframe=timeframe,
                candles=candles,
            )
            if (
                shadow.status is not ShadowComparisonStatus.MATCH
                or shadow.cached is None
                or shadow.cached.state is not IndicatorNodeState.READY
                or shadow.cached.value is None
            ):
                runtime.event_sink.emit(
                    "iqoption_incremental_engine_blocked",
                    strategy_key=strategy_key,
                    symbol=symbol,
                    timeframe=timeframe,
                    epoch=shadow.close_epoch,
                    reason=shadow.reason.value,
                    status=shadow.status.value,
                )
                return None, Decimal("50"), shadow.reason.value
            return (
                self._direction_from_indicator(shadow.cached.direction),
                shadow.cached.value,
                "OK" if shadow.cached.direction in {"call", "put"} else "NO_SIGNAL",
            )

        if flags.indicator_shadow_enabled:
            shadow = self._shadow_rsi14(
                supervisor=supervisor,
                symbol=symbol,
                timeframe=timeframe,
                candles=candles,
            )
            if (
                shadow.status is ShadowComparisonStatus.INVALID
                or shadow.status is ShadowComparisonStatus.MISMATCH
                or shadow.cached is None
                or shadow.cached.state is IndicatorNodeState.INVALID
            ):
                runtime.event_sink.emit(
                    "iqoption_indicator_shadow_mismatch",
                    strategy_key=strategy_key,
                    symbol=symbol,
                    timeframe=timeframe,
                    epoch=shadow.close_epoch,
                    reason=shadow.reason.value,
                    status=shadow.status.value,
                )

        if not flags.legacy_entries_enabled:
            return None, Decimal("50"), "IQOPTION_ENTRY_ENGINE_DISABLED"

        # Only the pure resolver can admit the explicit SINGLE/Practice recipe.
        decision = self._strategy.evaluate_decision(candles, context)
        return decision.direction, decision.rsi, "NO_SIGNAL" if decision.direction is None else "OK"

    def _shadow_rsi14(
        self,
        *,
        supervisor: ReadOnlyWorkerSupervisor,
        symbol: str,
        timeframe: int,
        candles: list[MarketCandle],
    ) -> ShadowComparison:
        key = IndicatorKey(
            series_key=IQOptionSeriesKey(
                broker=Broker.IQ_OPTION,
                account_id=IQOPTION_PRACTICE_ACCOUNT_ID,
                product=IQOPTION_SERIES_PRODUCT,
                generation=self._series_generation(supervisor.client),
                asset=symbol,
                timeframe_seconds=timeframe,
            ),
            name="rsi_extreme",
            params=canonical_indicator_params(
                {"period": 14, "lower": Decimal("30"), "upper": Decimal("70")}
            ),
        )
        self._indicator_cache.acquire(
            key, recipe_id=f"shadow:iqoption-rsi-demo:{symbol}:{timeframe}"
        )
        return self._indicator_cache.shadow_compare(key, candles)

    @staticmethod
    def _direction_from_indicator(direction: str) -> Direction | None:
        if direction == "call":
            return Direction.CALL
        if direction == "put":
            return Direction.PUT
        return None

    def _candles_for_closed_interval(
        self,
        *,
        supervisor: ReadOnlyWorkerSupervisor,
        runtime: CoreRuntime,
        symbol: str,
        timeframe: int,
        warmup_need: int,
        strategy_key: str = "",
    ) -> list[MarketCandle] | None:
        key = IQOptionSeriesKey(
            broker=Broker.IQ_OPTION,
            account_id=IQOPTION_PRACTICE_ACCOUNT_ID,
            product=IQOPTION_SERIES_PRODUCT,
            generation=self._series_generation(supervisor.client),
            asset=symbol,
            timeframe_seconds=timeframe,
        )
        self._series_hub.replace_message_budget(self._message_budget)
        fetch_warmup_need = (
            max(warmup_need, 17)
            if strategy_key == "iqoption-rsi-demo"
            and not callable(getattr(supervisor.client, "iqoption_binary_payout", None))
            else warmup_need
        )
        outcome = self._series_hub.snapshot(
            client=supervisor.client,
            key=key,
            warmup_required=warmup_need,
            fetcher=lambda _client, series_key, count: self._fetch_candles(
                supervisor,
                series_key.asset,
                series_key.timeframe_seconds,
                warmup_need=fetch_warmup_need,
            ),
        )
        budget = outcome.budget
        if budget is not None and budget.pressure and not self._message_budget_pressure_reported:
            self._message_budget_pressure_reported = True
            runtime.event_sink.emit(
                "iqoption_message_budget_pressure",
                used_in_window=budget.used_in_window,
                limit=budget.limit,
            )
        elif budget is not None and not budget.pressure:
            self._message_budget_pressure_reported = False
        if outcome.reason is IQOptionSeriesReason.MESSAGE_BUDGET_EXHAUSTED:
            return None
        if outcome.snapshot is None:
            raise RuntimeError(f"IQOPTION_{outcome.reason.value}")
        return list(outcome.snapshot.candles)

    @staticmethod
    def _series_generation(client: object) -> str:
        for attribute in (
            "generation",
            "session_generation",
            "connection_generation",
            "worker_generation",
            "session_id",
        ):
            value = getattr(client, attribute, None)
            if value is not None:
                text = str(value)
                if text:
                    return f"{attribute}:{text}"
        return f"client:{id(client)}"

    @staticmethod
    def _fetch_candles(
        supervisor: ReadOnlyWorkerSupervisor,
        symbol: str,
        timeframe: int,
        *,
        warmup_need: int,
    ) -> list[MarketCandle]:
        _ticks, candles = supervisor.client.market_history(
            symbol,
            style="candles",
            count=warmup_need + 3,
            timeframe_seconds=timeframe,
        )
        return list(candles)

    def _render_eval_waiting(
        self,
        symbol: str,
        display_name: str,
        result: EvalResult,
        *,
        selected: bool,
    ) -> None:
        if result.stage == "TICK_VOLUME_UNAVAILABLE":
            condition = "VOLUME_INDISPONIVEL"
            status = "TICK_VOLUME_UNAVAILABLE"
            reason = "IQOPTION_TICK_VOLUME_UNAVAILABLE"
        else:
            condition = f"AQUECENDO {result.warmup_have}/{result.warmup_need}"
            status = "WARMING_UP"
            reason = condition
        self._update_rank(
            symbol,
            display_name,
            rsi="--",
            condition=condition,
            selected=selected,
            status=status,
        )
        self._set_status(reason)

    @staticmethod
    def _has_nonterminal_iq_order(runtime: CoreRuntime) -> bool:
        exposure_states = {
            OrderState.OUTBOXED.value,
            OrderState.DISPATCHING.value,
            OrderState.ACCEPTED.value,
            OrderState.OPEN.value,
            OrderState.UNKNOWN.value,
            OrderState.RECONCILING.value,
            OrderState.SETTLEMENT_UNKNOWN.value,
            OrderState.MANUAL_REVIEW.value,
        }
        return any(
            str(row.get("broker")) == Broker.IQ_OPTION.value
            and str(row.get("state")) in exposure_states
            for row in runtime.reader.list_nonterminal_orders()
        )

    def notify_order_event(
        self,
        event: BrokerOrderEvent,
        result: BrokerEventApplyResult,
    ) -> None:
        if (
            event.broker is not Broker.IQ_OPTION
            or result.status
            not in {BrokerEventApplyStatus.APPLIED, BrokerEventApplyStatus.APPLIED_WITH_GAP}
            or result.order_state is not OrderState.SETTLED
            or event.result_minor is None
        ):
            return
        config = self._risk_config_provider()
        pnl = Decimal(event.result_minor) / Decimal(100)
        with self._lock:
            self._daily_profit_loss += pnl
            if event.result_minor < 0:
                self._consecutive_losses += 1
                self._cooldown_until = self._monotonic() + config.cooldown_seconds_after_loss
                self._status_reason = "IQOPTION_ORDER_SETTLED_LOSS"
            else:
                self._consecutive_losses = 0
                self._cooldown_until = 0.0
                self._status_reason = "IQOPTION_ORDER_SETTLED_WIN"

    def _symbols_for_cycle(self, selected_symbol: str) -> tuple[tuple[str, str], ...]:
        available = self._executable_symbols()
        # Test doubles predating dynamic discovery intentionally keep the
        # bounded legacy list. The production SocketWorkerClient always
        # exposes iqoption_instrument_catalog and therefore never uses it.
        if self._instrument_catalog is None and not self._catalog_discovery_supported:
            available = IQOPTION_RADAR_SYMBOLS
        if selected_symbol != "AUTO":
            # No implicit substitution between spot and OTC after a rejection.
            return tuple(item for item in available if item[0] == selected_symbol)
        if not available:
            return ()
        item = available[self._scan_cursor % len(available)]
        self._scan_cursor = (self._scan_cursor + 1) % len(available)
        return (item,)

    def _refresh_instrument_catalog(
        self,
        supervisor: ReadOnlyWorkerSupervisor,
        runtime: CoreRuntime,
    ) -> None:
        now = self._monotonic()
        catalog_fn = getattr(supervisor.client, "iqoption_instrument_catalog", None)
        if not callable(catalog_fn):
            return
        if now - self._last_instrument_catalog_probe < IQOPTION_INSTRUMENT_CATALOG_TTL_SECONDS:
            if now - self._instrument_catalog_received_mono > (
                IQOPTION_INSTRUMENT_CATALOG_MAX_STALE_SECONDS
            ):
                self._instrument_catalog = None
            return
        self._last_instrument_catalog_probe = now
        # One catalogue refresh emits exactly two read-only broker messages:
        # initialization-data and digital underlying-list. Reserve both before
        # the worker call so the Core's sliding-window ceiling remains true.
        decisions = tuple(
            self._message_budget.try_acquire_operational(self._monotonic()) for _ in range(2)
        )
        for decision in decisions:
            self._report_operational_budget(runtime, decision)
        if not all(decision.allowed for decision in decisions):
            self._set_status("IQOPTION_MESSAGE_BUDGET_EXHAUSTED")
            return
        try:
            catalog = catalog_fn()
            if not isinstance(catalog, BrokerInstrumentCatalog):
                raise ValueError("invalid IQ Option instrument catalogue")
        except WorkerDispatchError as exc:
            if now - self._instrument_catalog_received_mono > (
                IQOPTION_INSTRUMENT_CATALOG_MAX_STALE_SECONDS
            ):
                self._instrument_catalog = None
            runtime.event_sink.emit(
                "iqoption_instrument_catalog_failed",
                reason_code=exc.code.value,
            )
            return
        except Exception:
            if now - self._instrument_catalog_received_mono > (
                IQOPTION_INSTRUMENT_CATALOG_MAX_STALE_SECONDS
            ):
                self._instrument_catalog = None
            runtime.event_sink.emit(
                "iqoption_instrument_catalog_failed",
                reason_code="IQOPTION_CATALOG_INVALID",
            )
            return
        self._instrument_catalog = catalog
        self._instrument_catalog_received_mono = now
        self._sync_catalog_ranking(catalog)
        counts = {
            product.value: sum(1 for item in catalog.instruments if item.product is product)
            for product in BrokerInstrumentProduct
        }
        runtime.event_sink.emit(
            "iqoption_instrument_catalog_refreshed",
            generation=catalog.generation,
            binary_count=counts[BrokerInstrumentProduct.BINARY.value],
            turbo_count=counts[BrokerInstrumentProduct.TURBO.value],
            digital_count=counts[BrokerInstrumentProduct.DIGITAL.value],
            executable_count=len(self._executable_symbols()),
            unavailable_products=",".join(item.value for item in catalog.unavailable_products)
            or None,
        )

    def _sync_catalog_ranking(self, catalog: BrokerInstrumentCatalog) -> None:
        manifest = self._catalog_provider() if self._catalog_provider is not None else None
        allowed_assets = {
            info.entry.asset
            for info in (() if manifest is None else manifest.active_strategies.values())
        }
        grouped: dict[str, list[BrokerInstrument]] = {}
        for item in catalog.instruments:
            # The broker catalogue also contains stocks, indices and legacy
            # products for which this client has no signed strategy.  They are
            # useful as transport evidence but must not flood the operator's
            # execution radar or asset selector.
            if self._catalog_provider is not None and item.broker_symbol not in allowed_assets:
                continue
            grouped.setdefault(item.broker_symbol, []).append(item)
        ranking: dict[str, UiIqOptionAssetRank] = {}
        for symbol, raw_items in sorted(grouped.items()):
            items = tuple(raw_items)
            products = "/".join(sorted({item.product.value for item in items}))
            base_name = items[0].display_name
            executable = any(item.executable for item in items)
            open_detected = any(
                item.availability is BrokerInstrumentAvailability.OPEN for item in items
            )
            status = "WAITING_DATA" if executable else "DISCOVERY_ONLY"
            condition = (
                "WAITING_DATA"
                if executable
                else "OPEN_READ_ONLY"
                if open_detected
                else "MARKET_CLOSED"
            )
            details = "; ".join(
                f"{item.product.value}: {item.availability.value}"
                + (" · executável" if item.executable else " · somente detecção")
                for item in items
            )
            existing = self._asset_ranking_by_symbol.get(symbol)
            if executable and existing is not None and existing.rsi != "--":
                ranking[symbol] = replace(
                    existing,
                    display_name=f"{base_name} · {products}",
                    candidate_details=f"{details}; {existing.candidate_details}"[:2048],
                )
            else:
                ranking[symbol] = UiIqOptionAssetRank(
                    symbol=symbol,
                    display_name=f"{base_name} · {products}",
                    rsi="--",
                    condition=condition,
                    status=status,
                    candidate_details=details,
                    source="IQOPTION_SESSION_CATALOG",
                    readiness="READY" if executable else "READ_ONLY",
                )
        with self._lock:
            self._asset_ranking_by_symbol = ranking
            self._asset_ranking = self._ordered_ranking()

    def _executable_symbols(self) -> tuple[tuple[str, str], ...]:
        catalog = self._instrument_catalog
        if catalog is None:
            return ()
        manifest = self._catalog_provider() if self._catalog_provider is not None else None
        allowed_assets = {
            info.entry.asset
            for info in (() if manifest is None else manifest.active_strategies.values())
        }
        symbols: dict[str, str] = {}
        for item in catalog.instruments:
            if (
                item.product is BrokerInstrumentProduct.TURBO
                and item.availability is BrokerInstrumentAvailability.OPEN
                and item.analyzable
                and item.executable
                and (self._catalog_provider is None or item.broker_symbol in allowed_assets)
            ):
                symbols[item.broker_symbol] = self._display_name_for(item.broker_symbol)
        return tuple(sorted(symbols.items()))

    def _display_name_for(self, symbol: str) -> str:
        rank = self._asset_ranking_by_symbol.get(symbol)
        if rank is not None:
            return rank.display_name
        return dict(IQOPTION_RADAR_SYMBOLS).get(symbol, symbol)

    def _risk_block_reason(self, config: IqOptionRiskConfig) -> str | None:
        if self._daily_trades_count >= config.max_daily_trades:
            return "IQOPTION_MAX_TRADES_REACHED"
        stop_loss = Decimal(config.daily_stop_loss_minor_units) / Decimal(100)
        if self._daily_profit_loss <= -stop_loss:
            return "IQOPTION_STOP_LOSS_REACHED"
        take_profit = Decimal(config.daily_take_profit_minor_units) / Decimal(100)
        if self._daily_profit_loss >= take_profit:
            return "IQOPTION_TAKE_PROFIT_REACHED"
        if self._consecutive_losses >= config.max_consecutive_losses:
            return "IQOPTION_CONSECUTIVE_LOSS_LIMIT_REACHED"
        remaining = self._cooldown_until - self._monotonic()
        if remaining > 0:
            return f"IQOPTION_LOSS_COOLDOWN ({int(remaining) + 1}s)"
        return None

    def _update_rank(
        self,
        symbol: str,
        display_name: str,
        *,
        rsi: str,
        condition: str,
        selected: bool,
        status: str,
        direction: str | None = None,
        source: str = "IQOPTION_BROKER_CLOSED_CANDLES",
        mode: str = "LEGACY",
        revision: str | None = None,
        readiness: str | None = None,
        signal_observed: bool | None = None,
        candidate_eligible: bool | None = None,
        order_submitted: bool = False,
        order_accepted: bool = False,
        terminal: bool = False,
        wait_reason: str | None = None,
        waiting_seconds: int = 0,
    ) -> None:
        existing = self._asset_ranking_by_symbol.get(symbol)
        if rsi == "--" and source == "IQOPTION_BROKER_CLOSED_CANDLES":
            source = "NO_MARKET_EVIDENCE"
        rank = UiIqOptionAssetRank(
            symbol=symbol,
            display_name=display_name,
            rsi=rsi,
            direction=direction,
            condition=condition,
            selected=selected,
            status=status,
            candidate_details=self._candidate_details.get(symbol, ""),
            source=source,
            mode=mode,
            revision=revision
            if revision is not None
            else (None if existing is None else existing.revision),
            readiness=readiness
            or ("READY" if status not in {"WAITING_DATA", "WARMING_UP"} else status),
            signal_observed=(direction is not None) if signal_observed is None else signal_observed,
            candidate_eligible=(direction is not None)
            if candidate_eligible is None
            else candidate_eligible,
            order_submitted=order_submitted,
            order_accepted=order_accepted,
            terminal=terminal,
            wait_reason=wait_reason,
            waiting_seconds=waiting_seconds,
        )
        with self._lock:
            self._asset_ranking_by_symbol[symbol] = rank
            self._asset_ranking = self._ordered_ranking()

    def _set_status(self, reason: str) -> None:
        with self._lock:
            self._status_reason = reason

    def _mark_rank_execution(
        self,
        symbol: str,
        *,
        submitted: bool,
        accepted: bool,
        terminal: bool,
        reason: str,
    ) -> None:
        """Annotate the last signal with authoritative dispatch evidence."""
        with self._lock:
            previous = self._asset_ranking_by_symbol.get(symbol)
        if previous is None:
            return
        self._update_rank(
            symbol,
            previous.display_name,
            rsi=previous.rsi,
            condition=previous.condition,
            selected=previous.selected,
            # Keep the signal lifecycle visible; dispatch evidence is carried by
            # the explicit flags below and is never conflated with a signal.
            status=previous.status,
            direction=previous.direction,
            source=previous.source,
            mode=previous.mode,
            readiness=previous.readiness,
            signal_observed=previous.signal_observed,
            candidate_eligible=previous.candidate_eligible,
            order_submitted=submitted,
            order_accepted=accepted,
            terminal=terminal,
            wait_reason=reason if not accepted else None,
        )

    def _ordered_ranking(self) -> tuple[UiIqOptionAssetRank, ...]:
        return tuple(self._asset_ranking_by_symbol.values())


__all__ = [
    "IQOPTION_ACTIVE_SUSPENSION_COOLDOWN_SECONDS",
    "IQOPTION_PRACTICE_ACCOUNT_ID",
    "IQOPTION_RADAR_SYMBOLS",
    "IqOptionAutoTrader",
    "IqOptionExecutionFlags",
]
