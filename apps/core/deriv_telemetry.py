from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from apps.core.health import HealthGate
from apps.core.payout_routed_differs import PAYOUT_ROUTED_DIFFERS_STRATEGY_ID
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSupervisor
from apps.core.worker_client import SocketWorkerClient, WorkerDispatchError
from packages.domain.market import BrokerAccountBalance, BrokerClockSnapshot, MarketTick
from packages.market_data import DigitFrequencySnapshot
from packages.strategies.deriv_digits import (
    DerivDigitShadowEngine,
    DerivMultiAssetShadowRadar,
    DigitAssetShadowProjection,
    DigitEnginePoolMetrics,
    DigitStrategyProjection,
)

_SHADOW_SYMBOL_ALLOWLIST = frozenset(
    {
        "R_10",
        "R_25",
        "R_50",
        "R_75",
        "R_100",
    }
)
_SHADOW_DIGIT_CONTRACTS = frozenset(
    {"DIGITOVER", "DIGITUNDER", "DIGITDIFF", "DIGITEVEN", "DIGITODD"}
)


def _history_based_digit_projections(
    projections: tuple[DigitStrategyProjection, ...],
) -> tuple[DigitStrategyProjection, ...]:
    return tuple(
        item for item in projections if str(item.strategy_id) != PAYOUT_ROUTED_DIFFERS_STRATEGY_ID
    )


class DerivTelemetrySource(StrEnum):
    FAKE_SIMULATED = "FAKE_SIMULATED"
    PUBLIC_LIVE = "PUBLIC_LIVE"
    DEMO_LIVE = "DEMO_LIVE"
    REAL_LIVE = "REAL_LIVE"


@dataclass(frozen=True, slots=True)
class DerivTelemetrySnapshot:
    source: DerivTelemetrySource
    connection_mode: str
    connected: bool
    balance: BrokerAccountBalance | None
    clock: BrokerClockSnapshot | None
    reason_code: str | None
    digit_frequency: DigitFrequencySnapshot | None = None
    synthetic_strategies: tuple[DigitStrategyProjection, ...] = ()
    asset_ranking: tuple[DigitAssetShadowProjection, ...] = ()
    strategy_matrix: tuple[DigitStrategyProjection, ...] = ()
    multi_strategy_metrics: DigitEnginePoolMetrics | None = None


class DerivTelemetryMonitor:
    """Bounded Core-owned cache of read-only Deriv account/clock evidence."""

    _HEALTH_ACCOUNT = "market-data"

    def __init__(
        self,
        supervisor: ReadOnlyWorkerSupervisor,
        health_gate: HealthGate,
        source: DerivTelemetrySource,
        *,
        poll_interval_seconds: float = 5.0,
        symbol_provider: Callable[[], str] = lambda: "R_100",
        tick_notifier: Callable[[], None] | None = None,
        disconnect_notifier: Callable[[str], None] | None = None,
        reconciliation_notifier: Callable[[str], None] | None = None,
        synthetic_engine: DerivDigitShadowEngine | None = None,
        universe_refresh_seconds: float = 300.0,
        generation_is_current: Callable[[], bool] = lambda: True,
    ) -> None:
        if not 0.5 <= poll_interval_seconds <= 60:
            raise ValueError("Deriv telemetry poll interval is outside bounds")
        if not 30 <= universe_refresh_seconds <= 3600:
            raise ValueError("Deriv shadow universe refresh interval is outside bounds")
        self._supervisor = supervisor
        self._health_gate = health_gate
        self._source = source
        self._poll_interval = poll_interval_seconds
        self._symbol_provider = symbol_provider
        self._tick_notifier = tick_notifier
        self._disconnect_notifier = disconnect_notifier
        self._reconciliation_notifier = reconciliation_notifier
        self._generation_is_current = generation_is_current
        self._last_contract_events_overflow_total = 0
        selected_symbol = self._symbol_provider().strip()
        if synthetic_engine is None:
            self._radar = DerivMultiAssetShadowRadar((selected_symbol,) if selected_symbol else ())
        else:
            first_engine = synthetic_engine
            assigned = False

            def engine_factory() -> DerivDigitShadowEngine:
                nonlocal assigned
                if not assigned:
                    assigned = True
                    return first_engine
                return DerivDigitShadowEngine()

            self._radar = DerivMultiAssetShadowRadar(
                (selected_symbol,) if selected_symbol else (),
                engine_factory=engine_factory,
            )
        self._universe_refresh_seconds = universe_refresh_seconds
        self._next_universe_refresh = 0.0
        self._engine_lock = threading.RLock()
        self._lock = threading.Lock()
        self._snapshot = DerivTelemetrySnapshot(
            source,
            "UNKNOWN",
            False,
            None,
            None,
            "NOT_PROBED",
            synthetic_strategies=self._strategy_projections(),
            asset_ranking=self._radar.asset_ranking(),
            strategy_matrix=_history_based_digit_projections(
                self._radar.all_strategy_projections()
            ),
            multi_strategy_metrics=self._radar.metrics,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._subscriptions: dict[str, str] = {}
        self._subscription_retry_after: dict[str, float] = {}

    @property
    def snapshot(self) -> DerivTelemetrySnapshot:
        with self._lock:
            return self._snapshot

    def start(self) -> None:
        if self._thread is not None or not self._is_current():
            return
        self.probe_once()
        self._refresh_shadow_universe(force=True)
        self._ensure_tick_subscriptions()
        self._thread = threading.Thread(
            target=self._run,
            name="deriv-account-telemetry",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._unsubscribe_all()
        self._thread = None

    def set_tick_notifier(self, notifier: Callable[[], None] | None) -> None:
        with self._lock:
            self._tick_notifier = notifier

    def record_arbitration(
        self,
        winner_signal_id: str | None,
        rejected_signal_ids: tuple[str, ...],
    ) -> None:
        with self._engine_lock:
            self._radar.record_arbitration(winner_signal_id, rejected_signal_ids)
            matrix = _history_based_digit_projections(self._radar.all_strategy_projections())
        with self._lock:
            self._snapshot = replace(self._snapshot, strategy_matrix=matrix)

    def probe_once(self) -> DerivTelemetrySnapshot:
        if not self._is_current():
            return self.snapshot
        current_frequency = self.snapshot.digit_frequency
        with self._engine_lock:
            strategy_projections = self._strategy_projections()
            asset_ranking = self._radar.asset_ranking()
            strategy_matrix = _history_based_digit_projections(
                self._radar.all_strategy_projections()
            )
            multi_strategy_metrics = self._radar.metrics
        try:
            client = self._supervisor.client
            health_request = getattr(client, "request_health_snapshot", None)
            if callable(health_request):
                try:
                    transport_health = health_request()
                except (RuntimeError, WorkerDispatchError, ValueError):
                    transport_health = None
                if isinstance(transport_health, dict):
                    self._handle_transport_health(transport_health)
            clock = client.broker_clock()
            if not self._is_current():
                return self.snapshot
            self._health_gate.clear_scope(
                "DERIV",
                self._HEALTH_ACCOUNT,
                "HG_MARKET_DATA_DISCONNECTED",
            )
            trusted = clock.is_synced
            if trusted:
                self._health_gate.clear_scope("DERIV", self._HEALTH_ACCOUNT, "MD_CLOCK_UNTRUSTED")
            else:
                self._health_gate.block_scope("DERIV", self._HEALTH_ACCOUNT, "MD_CLOCK_UNTRUSTED")
            balance = (
                client.broker_balance()
                if client.capabilities.connection_mode
                in {"DEMO_AUTH_READ_ONLY", "REAL_AUTH_READ_ONLY", "DEMO", "REAL"}
                else None
            )
            snapshot = DerivTelemetrySnapshot(
                self._source,
                client.capabilities.connection_mode or "UNKNOWN",
                True,
                balance,
                clock,
                None if trusted else "MD_CLOCK_UNTRUSTED",
                current_frequency,
                strategy_projections,
                asset_ranking,
                strategy_matrix,
                multi_strategy_metrics,
            )
        except (RuntimeError, WorkerDispatchError, ValueError):
            if not self._is_current():
                return self.snapshot
            self._health_gate.block_scope("DERIV", self._HEALTH_ACCOUNT, "MD_CLOCK_UNTRUSTED")
            snapshot = DerivTelemetrySnapshot(
                self._source,
                "UNKNOWN",
                False,
                None,
                None,
                "DERIV_TELEMETRY_UNAVAILABLE",
                current_frequency,
                strategy_projections,
                asset_ranking,
                strategy_matrix,
                multi_strategy_metrics,
            )
        if not self._is_current():
            return self.snapshot
        with self._lock:
            self._snapshot = snapshot
        if not snapshot.connected:
            self._notify_disconnect("DERIV_TELEMETRY_UNAVAILABLE")
        return snapshot

    def _run(self) -> None:
        next_probe = time.monotonic() + self._poll_interval
        while not self._stop.is_set() and self._is_current():
            self._refresh_shadow_universe()
            self._ensure_tick_subscriptions()
            try:
                item = self._supervisor.client.receive_market_tick_snapshot(0.1)
            except (RuntimeError, WorkerDispatchError, ValueError):
                item = None
                self._notify_disconnect("DERIV_TICK_STREAM_DISCONNECTED")
            if item is not None:
                if not self._is_current():
                    return
                tick, frequency = item
                with self._engine_lock:
                    self._radar.ingest_tick(tick)
                    strategy_projections = self._strategy_projections()
                    asset_ranking = self._radar.asset_ranking()
                    strategy_matrix = _history_based_digit_projections(
                        self._radar.all_strategy_projections()
                    )
                    multi_strategy_metrics = self._radar.metrics
                digit_symbol = self._symbol_provider().strip()
                with self._lock:
                    current = self._snapshot
                    self._snapshot = DerivTelemetrySnapshot(
                        current.source,
                        current.connection_mode,
                        current.connected,
                        current.balance,
                        current.clock,
                        current.reason_code,
                        (
                            frequency
                            if frequency is not None and tick.broker_symbol == digit_symbol
                            else current.digit_frequency
                        ),
                        strategy_projections,
                        asset_ranking,
                        strategy_matrix,
                        multi_strategy_metrics,
                    )
                    notifier = self._tick_notifier
                if notifier is not None and tick.broker_symbol == digit_symbol:
                    notifier()
            if time.monotonic() >= next_probe:
                self.probe_once()
                next_probe = time.monotonic() + self._poll_interval

    def _ensure_tick_subscriptions(self) -> None:
        if not self._is_current():
            return
        digit_symbol = self._symbol_provider().strip()
        desired = set(self._radar.symbols)
        if digit_symbol:
            desired.add(digit_symbol)
        for symbol, subscription_id in tuple(self._subscriptions.items()):
            if symbol in desired:
                continue
            with contextlib.suppress(RuntimeError, WorkerDispatchError, ValueError):
                self._supervisor.client.unsubscribe_market_ticks(subscription_id)
            self._subscriptions.pop(symbol, None)
        for symbol in sorted(desired):
            if symbol in self._subscriptions:
                continue
            if time.monotonic() < self._subscription_retry_after.get(symbol, 0.0):
                continue
            new_subscription_id: str | None = None
            try:
                client = self._supervisor.client
                tick, frequency = client.subscribe_market_tick_snapshot(symbol)
                new_subscription_id = tick.subscription_id
                self._subscriptions[symbol] = new_subscription_id
                ticks = self._paged_tick_history(client, symbol, count=500)
                with self._engine_lock:
                    self._radar.ingest_history(symbol, ticks=ticks)
                    self._radar.ingest_tick(tick)
                    strategy_projections = self._strategy_projections()
                    asset_ranking = self._radar.asset_ranking()
                    strategy_matrix = _history_based_digit_projections(
                        self._radar.all_strategy_projections()
                    )
                    multi_strategy_metrics = self._radar.metrics
                with self._lock:
                    current = self._snapshot
                    self._snapshot = DerivTelemetrySnapshot(
                        current.source,
                        current.connection_mode,
                        current.connected,
                        current.balance,
                        current.clock,
                        current.reason_code,
                        frequency if symbol == digit_symbol else current.digit_frequency,
                        strategy_projections,
                        asset_ranking,
                        strategy_matrix,
                        multi_strategy_metrics,
                    )
            except (RuntimeError, WorkerDispatchError, ValueError):
                self._subscriptions.pop(symbol, None)
                self._subscription_retry_after[symbol] = time.monotonic() + max(
                    5.0, self._poll_interval
                )
                # A research-only stream cannot take the operator-selected stream offline.
                if symbol == digit_symbol:
                    self._notify_disconnect("DERIV_SUBSCRIPTION_DISCONNECTED")
                if new_subscription_id is not None:
                    with contextlib.suppress(RuntimeError, WorkerDispatchError, ValueError):
                        self._supervisor.client.unsubscribe_market_ticks(new_subscription_id)
            else:
                self._subscription_retry_after.pop(symbol, None)

    def _refresh_shadow_universe(self, *, force: bool = False) -> None:
        if not self._is_current():
            return
        now = time.monotonic()
        if not force and now < self._next_universe_refresh:
            return
        self._next_universe_refresh = now + self._universe_refresh_seconds
        selected = self._symbol_provider().strip()
        discovered: set[str] = set()
        try:
            client = self._supervisor.client
            for item in client.market_symbols():
                symbol = item.broker_symbol
                if symbol not in _SHADOW_SYMBOL_ALLOWLIST or not item.is_trading:
                    continue
                contracts = client.market_contracts(symbol)
                if any(
                    contract.is_available
                    and contract.contract_type.strip().upper() in _SHADOW_DIGIT_CONTRACTS
                    for contract in contracts
                ):
                    discovered.add(symbol)
        except (RuntimeError, WorkerDispatchError, ValueError):
            # Discovery is research-only and cannot degrade the selected execution stream.
            discovered.update(self._radar.symbols)
        if selected in _SHADOW_SYMBOL_ALLOWLIST:
            discovered.add(selected)
        with self._engine_lock:
            self._radar.set_symbols(tuple(sorted(discovered)))
            ranking = self._radar.asset_ranking()
            strategies = self._strategy_projections()
            strategy_matrix = _history_based_digit_projections(
                self._radar.all_strategy_projections()
            )
            multi_strategy_metrics = self._radar.metrics
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                synthetic_strategies=strategies,
                asset_ranking=ranking,
                strategy_matrix=strategy_matrix,
                multi_strategy_metrics=multi_strategy_metrics,
            )

    @staticmethod
    def _paged_tick_history(
        client: SocketWorkerClient,
        symbol: str,
        *,
        count: int,
        page_size: int = 100,
    ) -> tuple[MarketTick, ...]:
        if not symbol or not 1 <= count <= 20_000 or not 1 <= page_size <= 100:
            raise ValueError("paged tick history request is outside bounds")
        unique: dict[tuple[object, ...], MarketTick] = {}
        end_epoch: int | None = None
        oldest_epoch: int | None = None
        for _ in range(math.ceil(count / page_size)):
            requested = min(page_size, count - len(unique))
            if requested <= 0:
                break
            ticks, _ = client.market_history(
                symbol,
                style="ticks",
                count=requested,
                end_epoch=end_epoch,
            )
            if not ticks:
                break
            for item in ticks:
                unique[item.identity] = item
            next_oldest = min(item.epoch for item in ticks)
            if oldest_epoch is not None and next_oldest >= oldest_epoch:
                break
            oldest_epoch = next_oldest
            end_epoch = next_oldest - 1
            if end_epoch <= 0:
                break
        return tuple(sorted(unique.values(), key=lambda item: item.epoch)[-count:])

    def _unsubscribe_all(self) -> None:
        subscriptions = tuple(self._subscriptions.values())
        self._subscriptions.clear()
        self._subscription_retry_after.clear()
        for subscription_id in subscriptions:
            with contextlib.suppress(RuntimeError, WorkerDispatchError, ValueError):
                self._supervisor.client.unsubscribe_market_ticks(subscription_id)

    def _notify_disconnect(self, reason_code: str) -> None:
        if not self._is_current():
            return
        self._health_gate.block_scope(
            "DERIV",
            self._HEALTH_ACCOUNT,
            "HG_MARKET_DATA_DISCONNECTED",
        )
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                connected=False,
                reason_code=reason_code,
            )
        notifier = self._disconnect_notifier
        if notifier is not None and not self._stop.is_set():
            notifier(reason_code)

    def _handle_transport_health(self, health: dict[str, object]) -> None:
        if not self._is_current():
            return
        overflow = health.get("contract_events_overflow_total")
        if isinstance(overflow, bool) or not isinstance(overflow, int) or overflow < 0:
            return
        with self._lock:
            if overflow <= self._last_contract_events_overflow_total:
                return
            self._last_contract_events_overflow_total = overflow
        notifier = self._reconciliation_notifier
        if notifier is not None and not self._stop.is_set() and self._is_current():
            with contextlib.suppress(RuntimeError, WorkerDispatchError, ValueError):
                notifier("DERIV_CONTRACT_EVENT_OVERFLOW")

    def _is_current(self) -> bool:
        return not self._stop.is_set() and self._generation_is_current()

    def _strategy_projections(self) -> tuple[DigitStrategyProjection, ...]:
        projections = _history_based_digit_projections(
            self._radar.strategy_projections(self._symbol_provider().strip())
        )
        if self._source is not DerivTelemetrySource.DEMO_LIVE:
            return projections
        return tuple(replace(item, lifecycle_status="PRACTICE_VALIDATION") for item in projections)
