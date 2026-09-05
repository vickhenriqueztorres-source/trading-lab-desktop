"""Core-owned IQ Option RSI execution using broker candles and durable orders."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from apps.core.families.base import EvalResult
from apps.core.iqoption_candidates import (
    CandidateSignal,
    arbitrate,
    next_open_utc,
    resolve_candidates,
)
from apps.core.iqoption_connection_safety import IQOptionMessageBudget
from apps.core.iqoption_failures import IQFailurePolicy, ScopedFailure
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.live_monitor import LiveMonitor
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSupervisor
from apps.core.runtime import CoreRuntime
from packages.domain.market import BrokerAccountBalance, BrokerClockSnapshot, MarketCandle
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
from packages.protocol.ui_messages import UiIqOptionAssetRank
from packages.strategies.iqoption_rsi import (
    IQOptionRsiDemoStrategy,
    calculate_wilder_rsi,
)
from packages.strategies.models import RuntimeContext

logger = logging.getLogger("core.iqoption_auto_trader")

# Assets whose protocol identifiers are verified by the worker adapter. AUTO
# rotates one asset per cycle instead of creating a burst of WebSocket requests.
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


@dataclass(frozen=True, slots=True)
class _DispatchResult:
    order_id: str | None
    state: OrderState | None
    reason_code: str
    admission_blocked: bool = False

    @property
    def financially_accepted(self) -> bool:
        return self.state in {OrderState.ACCEPTED, OrderState.OPEN, OrderState.SETTLED}


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
        self._execution_ticket: tuple[str, str, str | None, object, float, Decimal] | None = None
        self._utc_clock = utc_clock
        self._decision_epochs: dict[tuple[str, str, int], None] = {}
        self._timeframe_override_reported = False
        self._candidate_details: dict[str, str] = {}
        self._evaluation_interval = evaluation_interval_seconds
        self._monotonic = monotonic
        self._message_budget = message_budget or IQOptionMessageBudget()
        self._message_budget_pressure_reported = False
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
        self._candle_cache: dict[tuple[str, int, int], list[MarketCandle]] = {}
        self._candle_cache_owner: object | None = None
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
    def last_rsi(self) -> Decimal | None:
        with self._lock:
            return self._last_rsi_value

    @property
    def asset_ranking(self) -> tuple[UiIqOptionAssetRank, ...]:
        with self._lock:
            return self._asset_ranking

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
            self._execution_ticket = None
            self._armed_after_epoch = int(self._utc_clock().timestamp())
            self._status_reason = "IQOPTION_BOT_ARMED"

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
        risk_config = self._risk_config_provider()
        selected_symbol = risk_config.symbol or "AUTO"
        automatic = selected_symbol == "AUTO"
        supervisor = self._supervisor_provider()
        runtime = self._runtime_provider()
        if supervisor is None or supervisor.client is None or runtime is None:
            self._set_status("IQOPTION_CONNECTION_REQUIRED")
            return
        now_mono = self._monotonic()
        if now_mono - self._last_telemetry_probe >= 2.0:
            self._last_telemetry_probe = now_mono
            clock_fn = getattr(supervisor.client, "broker_clock", None)
            if callable(clock_fn):
                try:
                    clock = clock_fn()
                    with self._lock:
                        self._latest_clock = clock
                except Exception:
                    pass
            balance_fn = getattr(supervisor.client, "broker_balance", None)
            if callable(balance_fn):
                try:
                    balance = balance_fn()
                    with self._lock:
                        self._latest_balance = balance
                except Exception:
                    pass
        try:
            self._restore_execution_state(runtime)
        except Exception:
            self._set_status("IQOPTION_EXECUTION_STATE_UNAVAILABLE")
            return

        symbols = self._symbols_for_cycle(selected_symbol)
        if not symbols:
            self._set_status("IQOPTION_SYMBOL_UNSUPPORTED")
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
            self._candle_cache.clear()

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
            for key, reason in rejected.items():
                info = None if catalog is None else catalog.active_strategies.get(key)
                self._record_decision(
                    runtime,
                    symbol,
                    key,
                    0 if info is None else self._timeframe_seconds(info.entry.timeframe),
                    int(now_utc.timestamp()) // 60 * 60,
                    reason,
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
                    )
                except Exception as exc:
                    evaluation_waiting = True
                    logger.info("IQ candle request failed: %s", type(exc).__name__)
                    runtime.health_gate.block_scope(
                        Broker.IQ_OPTION.value, "market-data", "HG_MARKET_DATA_DISCONNECTED"
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
                        # Only the pure resolver can admit the explicit SINGLE/Practice recipe.
                        decision = self._strategy.evaluate_decision(candles, context)
                        direction, rsi = decision.direction, decision.rsi
                        stage = "NO_SIGNAL" if direction is None else "OK"
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
                    runtime, symbol, item.key, item.timeframe_seconds, epoch, stage
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
                )
                if direction is not None:
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
                self._update_rank(
                    asset,
                    dict(IQOPTION_RADAR_SYMBOLS).get(asset, asset),
                    rsi="--",
                    condition="SIGNAL_CONFLICT",
                    selected=False,
                    status="SIGNAL_CONFLICT",
                )
        if winner is not None:
            symbol = winner.candidate.entry.asset
            display_name = dict(IQOPTION_RADAR_SYMBOLS).get(symbol, symbol)
            candidate = (
                symbol,
                display_name,
                winner.direction,
                winner.rsi,
                winner.epoch,
                winner.candidate.key,
            )

        if not self._operator_armed():
            self._set_status("IQOPTION_BOT_DISARMED")
            return
        if candidate is None:
            if evaluation_waiting:
                return
            if automatic:
                self._set_status(f"AUTO_SCAN_REAL_DATA ({len(IQOPTION_RADAR_SYMBOLS)} ASSETS)")
            else:
                self._set_status(f"IQOPTION_WAITING_RSI_SIGNAL ({selected_symbol})")
            return

        symbol, display_name, direction, rsi, candle_epoch, strat_key = candidate
        with self._lock:
            self._last_rsi_value = rsi
        if self._last_evaluated_epochs.get(symbol, -1) >= candle_epoch:
            self._set_status(
                self._last_dispatch_reasons.get(symbol)
                or f"SINAL_CONSUMIDO: {display_name} {direction.value} @ RSI={rsi:.1f}"
            )
            return
        if self._has_nonterminal_iq_order(runtime):
            self._set_status("IQOPTION_ORDER_IN_FLIGHT")
            return
        if self._armed_after_epoch is not None and candle_epoch <= self._armed_after_epoch:
            self._set_status("IQOPTION_NEW_SIGNAL_REQUIRED_AFTER_ARM")
            return

        risk_reason = self._risk_block_reason(risk_config)
        if risk_reason is not None:
            self._set_status(risk_reason)
            return

        try:
            manifest_context = self._prepare_execution(symbol, strat_key, supervisor.client)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, RuntimeError) else "IQOPTION_PAYOUT_UNAVAILABLE"
            if not reason or not all(c.isupper() or c == "_" for c in reason):
                reason = "IQOPTION_PAYOUT_UNAVAILABLE"
            self._record_decision(
                runtime,
                symbol,
                strat_key,
                winner.candidate.timeframe_seconds if winner else 60,
                candle_epoch,
                reason,
            )
            self._set_status(reason)
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
            self._daily_trades_count += 1
            self._set_status("IQOPTION_ORDER_UNKNOWN_RECONCILIATION_REQUIRED")
        else:
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

    def _prepare_execution(self, symbol: str, key: str, client: Any) -> str | None:
        self._execution_ticket = None
        if self._account_type_provider().upper() not in {"DEMO", "PRACTICE"}:
            raise RuntimeError("IQOPTION_REAL_ACCOUNT_FORBIDDEN")
        if not self._operator_armed():
            raise RuntimeError("IQOPTION_BOT_DISARMED")
        if key != "iqoption-rsi-demo":
            monitor = None if self._monitor_provider is None else self._monitor_provider()
            if monitor is None or not monitor.ready:
                raise RuntimeError("MANIFEST_MONITOR_UNAVAILABLE")
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
        started = self._monotonic()
        payout = client.iqoption_binary_payout(symbol)
        if not isinstance(payout, Decimal) or not payout.is_finite() or not 0 < payout <= 1:
            raise RuntimeError("IQOPTION_PAYOUT_UNAVAILABLE")
        context = self._check_manifest_execution(symbol, key, payout)
        self._execution_ticket = (symbol, key, context, client, started, payout)
        self._validate_execution_ticket(symbol, key, context)
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
        monitor = None if self._monitor_provider is None else self._monitor_provider()
        if monitor is None or not monitor.ready:
            raise RuntimeError("MANIFEST_MONITOR_UNAVAILABLE")
        catalog = None if self._catalog_provider is None else self._catalog_provider()
        if catalog is None:
            raise RuntimeError("STRATEGY_NOT_FOUND")
        allowed, reason, _ = catalog.is_eligible(
            key, account_type=account, current_payout=payout, now_utc=self._utc_clock()
        )
        if not allowed:
            raise RuntimeError(reason)
        info = catalog.get_strategy(key)
        if info is None or info.entry.asset != symbol:
            raise RuntimeError("ASSET_MISMATCH")
        return json.dumps(LiveMonitor.binding(info.entry), sort_keys=True, separators=(",", ":"))

    def _validate_execution_ticket(self, symbol: str, key: str, context: str | None) -> None:
        ticket = self._execution_ticket
        supervisor = self._supervisor_provider()
        if (
            ticket is None
            or supervisor is None
            or supervisor.client is not ticket[3]
            or (symbol, key, context) != ticket[:3]
            or not 0 <= self._monotonic() - ticket[4] < 2
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
        next_open: str | None = None,
    ) -> None:
        identity = (symbol, key, epoch)
        if identity in self._decision_epochs:
            return
        # Bounded deduplication, no database reads in candidate routing.
        self._decision_epochs[identity] = None
        if len(self._decision_epochs) > 4096:
            self._decision_epochs.pop(next(iter(self._decision_epochs)))
        runtime.event_sink.emit(
            "iqoption_decision",
            symbol=symbol,
            strategy_key=key,
            timeframe=timeframe,
            epoch=epoch,
            stage_rejected=reason,
            next_open_utc=next_open,
        )

    @staticmethod
    def _timeframe_seconds(timeframe: str) -> int:
        return {"M1": 60, "M5": 300, "M15": 900}.get(timeframe, 0)

    def _candles_for_closed_interval(
        self,
        *,
        supervisor: ReadOnlyWorkerSupervisor,
        runtime: CoreRuntime,
        symbol: str,
        timeframe: int,
        warmup_need: int,
    ) -> list[MarketCandle] | None:
        # A replacement worker/session must never consume history from its
        # predecessor, even when both generations fall in the same minute.
        if self._candle_cache_owner is not supervisor.client:
            self._candle_cache_owner = supervisor.client
            self._candle_cache.clear()
        request_epoch = int(self._utc_clock().timestamp()) // timeframe
        cache_key = (symbol, timeframe, request_epoch)
        cached = self._candle_cache.get(cache_key)
        if cached is not None:
            return cached

        budget = self._message_budget.try_acquire(self._monotonic())
        if budget.pressure and not self._message_budget_pressure_reported:
            self._message_budget_pressure_reported = True
            runtime.event_sink.emit(
                "iqoption_message_budget_pressure",
                used_in_window=budget.used_in_window,
                limit=budget.limit,
            )
        elif not budget.pressure:
            self._message_budget_pressure_reported = False
        if not budget.allowed:
            return None

        candles = self._fetch_candles(
            supervisor,
            symbol,
            timeframe,
            warmup_need=warmup_need,
        )
        # Epochs of different TFs are not comparable. Retain one window per pair.
        self._candle_cache = {
            key: value
            for key, value in self._candle_cache.items()
            if key[:2] != (symbol, timeframe)
        }
        self._candle_cache[cache_key] = candles
        return candles

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
            count=min(120, warmup_need + 3),
            timeframe_seconds=timeframe,
        )
        return [
            candle
            for candle in candles
            if candle.is_closed
            and candle.broker is Broker.IQ_OPTION
            and candle.broker_symbol == symbol
            and candle.timeframe_seconds == timeframe
        ]

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
        if selected_symbol != "AUTO":
            # No implicit substitution between spot and OTC after a rejection.
            return (
                (
                    selected_symbol,
                    dict(IQOPTION_RADAR_SYMBOLS).get(selected_symbol, selected_symbol),
                ),
            )
        item = IQOPTION_RADAR_SYMBOLS[self._scan_cursor % len(IQOPTION_RADAR_SYMBOLS)]
        self._scan_cursor = (self._scan_cursor + 1) % len(IQOPTION_RADAR_SYMBOLS)
        return (item,)

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
    ) -> None:
        rank = UiIqOptionAssetRank(
            symbol=symbol,
            display_name=display_name,
            rsi=rsi,
            direction=direction,
            condition=condition,
            selected=selected,
            status=status,
            candidate_details=self._candidate_details.get(symbol, ""),
        )
        with self._lock:
            self._asset_ranking_by_symbol[symbol] = rank
            self._asset_ranking = self._ordered_ranking()

    def _set_status(self, reason: str) -> None:
        with self._lock:
            self._status_reason = reason

    def _ordered_ranking(self) -> tuple[UiIqOptionAssetRank, ...]:
        return tuple(self._asset_ranking_by_symbol.values())


__all__ = [
    "IQOPTION_ACTIVE_SUSPENSION_COOLDOWN_SECONDS",
    "IQOPTION_PRACTICE_ACCOUNT_ID",
    "IQOPTION_RADAR_SYMBOLS",
    "IqOptionAutoTrader",
]
