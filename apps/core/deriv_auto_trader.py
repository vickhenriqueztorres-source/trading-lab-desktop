from __future__ import annotations

import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from apps.core.deriv_telemetry import DerivTelemetrySnapshot, DerivTelemetrySource
from apps.core.digit_risk_config import (
    DERIV_DIGIT_STRATEGY_ALLOWLIST,
    StrategySelectionMode,
)
from apps.core.payout_routed_differs import (
    PAYOUT_ROUTED_DIFFERS_STRATEGY_ID,
    PayoutRoutedDiffersProposalCache,
)
from apps.core.runtime import CoreRuntime
from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    Direction,
    OrderRequest,
    OrderState,
    utc_now,
)
from packages.persistence.writer import BrokerEventApplyResult, BrokerEventApplyStatus
from packages.protocol import UiBotWaitingStatus
from packages.signal_arbitration import RankedSignalCandidate, SignalArbiter
from packages.strategies.deriv_digits import (
    DigitAssetShadowState,
    ShadowSignalState,
)


@dataclass(frozen=True, slots=True)
class _ExecutionCandidate:
    symbol: str
    strategy_id: str
    contract_type: str
    barrier: int | None
    epoch: int
    estimated_probability_pct: Decimal
    required_probability_pct: Decimal
    conditional_sample: int = 0
    signal_state: ShadowSignalState = ShadowSignalState.SHADOW_SIGNAL
    evidence: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _PerformanceSample:
    order_id: str
    pnl_minor: int
    settled_at: datetime
    observed_monotonic: float | None = None


class DerivDigitAutoTrader:
    """Core-owned Demo execution loop for statistically confirmed digit signals."""

    _TERMINAL_STATES = frozenset({"SETTLED", "REJECTED", "CANCELLED"})
    _PERFORMANCE_COOLDOWN = timedelta(minutes=10)
    _PERFORMANCE_PROBE_ORDERS = 10

    def __init__(
        self,
        runtime: CoreRuntime,
        account_id: str,
        telemetry: Callable[[], DerivTelemetrySnapshot | None],
        *,
        minimum_ticks: int = 500,
        minimum_order_interval_seconds: float = 0.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] = utc_now,
        operator_armed: Callable[[], bool] = lambda: True,
        minimum_evaluation_interval_seconds: float = 0.25,
        interval_waiter: Callable[[float], bool] | None = None,
        quote_provider: Callable[..., Decimal] | None = None,
        proposal_cache: PayoutRoutedDiffersProposalCache | None = None,
        signal_arbiter: SignalArbiter | None = None,
        arbitration_notifier: Callable[[str | None, tuple[str, ...]], None] | None = None,
    ) -> None:
        if not account_id.strip():
            raise ValueError("Deriv automated trader requires an account id")
        if (
            minimum_ticks < 2
            or minimum_order_interval_seconds < 0
            or minimum_evaluation_interval_seconds < 0
        ):
            raise ValueError("Deriv automated trader limits are invalid")
        self._runtime = runtime
        self._account_id = account_id
        self._telemetry = telemetry
        self._minimum_ticks = minimum_ticks
        self._minimum_interval = minimum_order_interval_seconds
        self._monotonic = monotonic_clock
        self._utc_clock = utc_clock
        self._operator_armed = operator_armed
        self._minimum_evaluation_interval = minimum_evaluation_interval_seconds
        self._stop = threading.Event()
        self._interval_waiter = interval_waiter or self._stop.wait
        self._quote_provider = quote_provider
        self._proposal_cache = proposal_cache
        self._signal_arbiter = signal_arbiter or SignalArbiter(None)
        self._arbitration_notifier = arbitration_notifier
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_order_at = float("-inf")
        self._evaluated_signal_keys: set[tuple[str, str, int]] = set()
        self._evaluated_signal_order: list[tuple[str, str, int]] = []
        self._minimum_signal_epochs: dict[tuple[str, str], int] = {}
        self._signal_lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._inflight_order_ids: set[str] | None = None
        self._order_scope: dict[str, tuple[str, str]] = {}
        self._performance: dict[tuple[str, str], deque[_PerformanceSample]] = {}
        self._performance_cache_initialized = False
        self._performance_probe_remaining: dict[tuple[str, str], int] = {}
        self._performance_probe_pending: set[tuple[str, str]] = set()
        self._performance_cooldown_until: dict[tuple[str, str], float] = {}
        self._performance_reset_at: dict[tuple[str, str], float] = {}
        self._performance_gate_details: dict[tuple[str, str], str] = {}
        self._martingale_pin_started_at: dict[tuple[str, str], float] = {}
        self._notification_generation = 0
        self._evaluated_notification_generation = 0
        self._last_evaluation_at = float("-inf")
        self._status_lock = threading.Lock()
        self._last_reason = "BOT_WAITING_FOR_LIVE_DERIV"
        self._reason_since = self._monotonic()
        self._reason_symbol: str | None = None
        self._armed_epoch: int | None = None
        self._rearm_notice = False
        self._last_reason_detail: str | None = None
        self._last_signal_at = float("-inf")
        self._last_wake_latency_microseconds = 0
        self._last_analysis_latency_microseconds = 0
        self._last_submission_latency_microseconds = 0
        self.reload_runtime_caches(report_divergence=False)

    @property
    def last_reason(self) -> str:
        with self._status_lock:
            return self._last_reason

    @property
    def waiting_status(self) -> UiBotWaitingStatus:
        with self._status_lock:
            reason = self._last_reason
            waited = max(0, int(self._monotonic() - self._reason_since))
            symbol = self._reason_symbol
            armed_epoch = self._armed_epoch
            rearm_notice = self._rearm_notice
        description = self._reason_description(reason, symbol, rearm_notice)
        with self._status_lock:
            detail = self._last_reason_detail
        if detail and reason in {
            "BOT_PERFORMANCE_COOLDOWN",
            "BOT_PERFORMANCE_CACHE_UNAVAILABLE",
            "BOT_NO_POSITIVE_NET_EDGE",
        }:
            description = f"{description} ({detail})"
        return UiBotWaitingStatus(
            reason_code=reason,
            description=description,
            waiting_since_seconds=waited,
            symbol=symbol,
            armed_epoch=armed_epoch,
            rearm_notice=rearm_notice,
        )

    @property
    def latency_metrics(self) -> dict[str, int]:
        with self._status_lock:
            return {
                "analysis_microseconds": self._last_analysis_latency_microseconds,
                "signal_to_analysis_microseconds": self._last_wake_latency_microseconds,
                "submission_microseconds": self._last_submission_latency_microseconds,
            }

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._notify()
        self._thread = threading.Thread(
            target=self._run,
            name="deriv-digit-auto-trader",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def begin_new_run(self) -> None:
        """Require a signal observed after the operator re-arms the bot.

        Pausing never authorizes the next start to execute a stale one-tick candidate.
        """

        snapshot = self._telemetry()
        if snapshot is None:
            with self._status_lock:
                was_waiting = self._last_reason.startswith("BOT_WAITING")
            self._set_reason(
                "BOT_WAITING_FOR_LIVE_DERIV",
                rearm_notice=was_waiting,
                reset_duration=True,
            )
            self._notify()
            return
        config = self._runtime.risk_ledger.digit_config
        candidates = self._execution_candidates(snapshot, automatic=config.auto_select_symbol)
        with self._signal_lock:
            self._minimum_signal_epochs = {
                (item.symbol, item.strategy_id): item.epoch for item in candidates
            }
        armed_epoch = max((item.epoch for item in candidates), default=None)
        with self._status_lock:
            was_waiting = self._last_reason.startswith("BOT_WAITING")
        self._set_reason(
            "BOT_WAITING_FOR_NEW_TICK",
            armed_epoch=armed_epoch,
            rearm_notice=was_waiting,
            reset_duration=True,
        )
        self._notify()

    def manual_resume(self) -> bool:
        """Official operator recovery action for a stuck performance pause."""

        previous = self.last_reason
        reset = getattr(self._runtime, "reset_digit_recovery_state", None)
        if callable(reset):
            outcome = reset()
            accepted = bool(outcome[0]) if isinstance(outcome, tuple) else bool(outcome)
            if not accepted:
                return False
        else:
            reset_risk = getattr(self._runtime.risk_ledger, "reset_digit_recovery_state", None)
            if callable(reset_risk):
                reset_risk(self._runtime.health_gate)
        now = self._monotonic()
        config = self._runtime.risk_ledger.digit_config
        active_strategies = set(config.enabled_strategy_ids) or {config.active_strategy_id}
        with self._cache_lock:
            for key in tuple(self._performance):
                if key[0] in active_strategies:
                    self._performance.pop(key, None)
                    self._performance_reset_at[key] = now
            self._performance_probe_remaining.clear()
            self._performance_probe_pending.clear()
            self._performance_cooldown_until.clear()
            self._performance_gate_details.clear()
        self._martingale_pin_started_at.clear()
        self._emit(
            "digit_operator_manual_resume",
            reason_code="OPERATOR_MANUAL_RESUME",
            previous_reason=previous,
            timestamp_monotonic=str(now),
        )
        self.begin_new_run()
        return True

    def evaluate_once(self) -> bool:
        analysis_started = self._monotonic()
        with self._status_lock:
            if self._last_signal_at != float("-inf"):
                self._last_wake_latency_microseconds = max(
                    0, int((analysis_started - self._last_signal_at) * 1_000_000)
                )
        if not self._operator_armed():
            return self._skip("BOT_OPERATOR_NOT_ARMED")
        if not self._runtime.dispatcher_started:
            return self._skip("BOT_DISABLED_OR_HEALTH_BLOCKED")
        with self._cache_lock:
            inflight = self._inflight_order_ids
        if inflight is None:
            return self._skip("BOT_ORDER_STATE_UNAVAILABLE")
        if inflight:
            return self._skip("BOT_ORDER_IN_FLIGHT")
        # HG_COOLDOWN_ACTIVE is time-dependent.  Refresh it before the global
        # gate check so the next live tick can clear an expired pause and resume
        # analysis automatically.  The refresh performs persistence work only
        # once, at the cooldown-expiry transition.
        refresh_digit_gate = getattr(
            self._runtime.risk_ledger,
            "refresh_digit_health_gate",
            None,
        )
        if callable(refresh_digit_gate):
            refresh_digit_gate(self._runtime.health_gate)
        gate = getattr(self._runtime, "health_gate", None)
        gate_state = getattr(gate, "state", None)
        if gate_state is not None and not bool(getattr(gate_state, "is_open", False)):
            if getattr(gate_state, "reason_code", None) == "HG_COOLDOWN_ACTIVE":
                return self._skip("BOT_RISK_COOLDOWN_ACTIVE")
            return self._skip("BOT_DISABLED_OR_HEALTH_BLOCKED")
        snapshot = self._telemetry()
        config = self._runtime.risk_ledger.digit_config
        if (
            snapshot is not None
            and snapshot.source is DerivTelemetrySource.REAL_LIVE
            and config.selection_mode is StrategySelectionMode.STRESS
        ):
            return self._skip("BOT_STRESS_MODE_REQUIRES_DEMO")
        if (
            snapshot is None
            or snapshot.source is not DerivTelemetrySource.DEMO_LIVE
            or not snapshot.connected
        ):
            return self._skip("BOT_DERIV_ACCOUNT_NOT_CONNECTED")
        execution_ids = self._execution_strategy_ids(config)
        if execution_ids is None:
            return self._skip("BOT_NO_STRATEGY_SELECTED")
        payout_session_enabled = self._proposal_cache is not None and (
            PAYOUT_ROUTED_DIFFERS_STRATEGY_ID in execution_ids
            or config.selection_mode is StrategySelectionMode.STRESS
        )
        frequency = snapshot.digit_frequency
        if not payout_session_enabled and (
            frequency is None or frequency.total_ticks < self._minimum_ticks
        ):
            return self._skip("BOT_WARMING_UP_TICKS")
        if config.selection_mode is StrategySelectionMode.MULTI and not config.enabled_strategy_ids:
            return self._skip("BOT_NO_STRATEGY_SELECTED")
        shadow_candidates = self._execution_candidates(
            snapshot, automatic=config.auto_select_symbol
        )
        candidate_list = [
            replace(item, signal_state=ShadowSignalState.EXECUTABLE_SIGNAL)
            for item in shadow_candidates
            if item.strategy_id in execution_ids
        ]
        payout_skip_reason: str | None = None
        if payout_session_enabled:
            payout_candidate, payout_skip_reason = self._payout_routed_candidate(snapshot)
            if payout_candidate is not None:
                candidate_list.append(payout_candidate)
        candidates = tuple(
            item
            for item in candidate_list
            if item.signal_state is ShadowSignalState.EXECUTABLE_SIGNAL
        )
        if not candidates:
            if payout_skip_reason is not None and not shadow_candidates:
                return self._skip(payout_skip_reason)
            return self._skip("BOT_WAITING_FOR_STRATEGY_SIGNAL")

        metrics_reader = getattr(self._runtime.risk_ledger, "get_digit_metrics", None)
        risk_metrics = metrics_reader() if callable(metrics_reader) else None
        martingale_step = 0 if risk_metrics is None else int(risk_metrics.martingale_step)
        recovery_symbol = None if risk_metrics is None else risk_metrics.recovery_symbol
        if martingale_step > 0:
            if recovery_symbol is None:
                return self._skip("BOT_MARTINGALE_STATE_INCOMPLETE")
            pin_key = (config.active_strategy_id, recovery_symbol)
            pin_started = self._martingale_pin_started_at.setdefault(pin_key, self._monotonic())
            candidates = tuple(item for item in candidates if item.symbol == recovery_symbol)
            if not candidates:
                pinned_for = self._monotonic() - pin_started
                if pinned_for >= config.martingale_pin_timeout_seconds:
                    reset_risk = getattr(
                        self._runtime.risk_ledger,
                        "reset_digit_recovery_state",
                        None,
                    )
                    if callable(reset_risk):
                        reset_risk(self._runtime.health_gate)
                    self._martingale_pin_started_at.pop(pin_key, None)
                    self._emit(
                        "digit_martingale_pin_released",
                        reason_code="MARTINGALE_PIN_TIMEOUT",
                        symbol=recovery_symbol,
                        duration_seconds=str(pinned_for),
                    )
                    # The sequence is deliberately not transferred to another
                    # symbol; normal selection resumes at base stake.
                    return self._skip("BOT_MARTINGALE_PIN_RELEASED")
                self._set_reason("BOT_MARTINGALE_ASSET_PINNED", symbol=recovery_symbol)
                return False

        edge_floor = self._configured_edge_floor(config.min_quantum_confidence_pct)
        with self._signal_lock:
            unevaluated = tuple(
                item
                for item in candidates
                if (item.symbol, item.strategy_id, item.epoch) not in self._evaluated_signal_keys
                and item.epoch
                > self._minimum_signal_epochs.get((item.symbol, item.strategy_id), -1)
            )
        if not unevaluated:
            return self._skip("BOT_WAITING_FOR_NEW_TICK")
        decisions = tuple(
            (item, *self._performance_allows(item, edge_floor)) for item in unevaluated
        )
        eligible = tuple(item for item, allowed, _reason in decisions if allowed)
        if not eligible:
            # A gate block is not a judgement of the signal.  Preserve it for
            # the next fresh tick/probe; only merit rejections consume epochs.
            for item, _allowed, item_reason in decisions:
                if item_reason == "NO_POSITIVE_NET_EDGE":
                    self._remember_signal(item)
            reason = (
                "BOT_PERFORMANCE_COOLDOWN"
                if any(item_reason == "PERFORMANCE_COOLDOWN" for _, _, item_reason in decisions)
                else (
                    "BOT_PERFORMANCE_CACHE_UNAVAILABLE"
                    if any(
                        item_reason == "PERFORMANCE_CACHE_UNAVAILABLE"
                        for _, _, item_reason in decisions
                    )
                    else "BOT_NO_POSITIVE_NET_EDGE"
                )
            )
            blocked = next(
                (
                    item
                    for item, _allowed, item_reason in decisions
                    if item_reason in {"PERFORMANCE_COOLDOWN", "PERFORMANCE_CACHE_UNAVAILABLE"}
                ),
                None,
            )
            if blocked is not None:
                self._set_performance_reason(blocked, reason)
            return self._skip(reason)
        ranked = tuple(
            RankedSignalCandidate(
                signal_id=f"{item.symbol}:{item.strategy_id}:{item.epoch}",
                strategy_id=item.strategy_id,
                symbol=item.symbol,
                conservative_margin=(
                    item.estimated_probability_pct - item.required_probability_pct
                ),
                conditional_sample=item.conditional_sample,
            )
            for item in eligible
        )
        arbitration = self._signal_arbiter.arbitrate_ranked(ranked)
        if self._arbitration_notifier is not None:
            self._arbitration_notifier(
                arbitration.winner_signal_id,
                tuple(item.signal_id for item in arbitration.rejected),
            )
        self._emit(
            "digit_signal_arbitration_winner",
            reason_code="ARBITRATION_RANKED_WINNER",
            winner_signal_id=arbitration.winner_signal_id,
            candidates=len(arbitration.considered_signal_ids),
            entry_mode="EXECUTABLE_SIGNAL",
            execution_environment="DEMO",
        )
        for rejected in arbitration.rejected:
            self._emit(
                "digit_signal_arbitration_rejected",
                reason_code=rejected.reason.value,
                signal_id=rejected.signal_id,
                winner_signal_id=arbitration.winner_signal_id,
                entry_mode="SHADOW_ONLY",
                execution_environment="DEMO",
            )
        winner_id = arbitration.winner_signal_id
        selected = next(
            item
            for item in eligible
            if f"{item.symbol}:{item.strategy_id}:{item.epoch}" == winner_id
        )
        # A one-tick signal is never retried later. Risk/transport failures require a new signal.
        for item in eligible:
            self._remember_signal(item)
        now = self._monotonic()
        if now - self._last_order_at < self._minimum_interval:
            return self._skip("BOT_ENTRY_THROTTLED")

        try:
            net_profit_ratio: Decimal | None = None
            if martingale_step > 0:
                if self._quote_provider is None:
                    net_profit_ratio = self._fallback_net_profit_ratio(selected.contract_type)
                else:
                    net_profit_ratio = self._quote_provider(
                        product=selected.contract_type,
                        symbol=selected.symbol,
                        amount_minor_units=config.stake_minor_units,
                        currency=config.currency,
                        prediction_digit=selected.barrier,
                    )
            if net_profit_ratio is None:
                if (
                    selected.strategy_id == PAYOUT_ROUTED_DIFFERS_STRATEGY_ID
                    and config.martingale_enabled
                ):
                    return self._skip("SESSION_MARTINGALE_NOT_SUPPORTED")
                allocated_stake = self._runtime.risk_ledger.digit_entry_stake(
                    self._runtime.health_gate
                )
            else:
                allocated_stake = self._runtime.risk_ledger.digit_entry_stake(
                    self._runtime.health_gate,
                    net_profit_ratio=net_profit_ratio,
                )
        except Exception as exc:
            detail = str(getattr(exc, "reason_code", str(exc)))
            normalized = re.sub(r"[^A-Z0-9_]+", "_", detail.upper()).strip("_")[:64]
            return self._skip(f"BOT_STAKE_REJECTED_{normalized or type(exc).__name__.upper()}")

        contract_type = selected.contract_type.upper()
        request = OrderRequest(
            correlation_id=str(uuid4()),
            broker=Broker.DERIV,
            account_id=self._account_id,
            product=contract_type,
            symbol=selected.symbol,
            direction=Direction.CALL,
            amount=allocated_stake,
            strategy_id=selected.strategy_id,
            strategy_version="1.9.11-resilient-connection-and-performance",
            deadline_at=utc_now() + timedelta(seconds=10),
            duration=1,
            duration_unit="t",
            prediction_digit=selected.barrier,
        )
        before_submit = self._monotonic()
        with self._status_lock:
            self._last_analysis_latency_microseconds = max(
                0, int((before_submit - analysis_started) * 1_000_000)
            )
        try:
            persisted = self._runtime.submit(request)
        except Exception as exc:
            detail = str(getattr(exc, "reason_code", str(exc)))
            normalized = re.sub(r"[^A-Z0-9_]+", "_", detail.upper()).strip("_")[:64]
            return self._skip(f"BOT_ENTRY_REJECTED_{normalized or type(exc).__name__.upper()}")
        after_submit = self._monotonic()
        with self._status_lock:
            self._last_submission_latency_microseconds = max(
                0, int((after_submit - before_submit) * 1_000_000)
            )
        self._last_order_at = now
        if selected.strategy_id == PAYOUT_ROUTED_DIFFERS_STRATEGY_ID:
            self._emit(
                "payout_routed_differs_entry_evidence",
                reason_code="PAYOUT_ROUTED_DIFFERS_SELECTED",
                symbol=selected.symbol,
                barrier=selected.barrier,
                candidate_count=int(dict(selected.evidence).get("candidate_count", "0")),
                theoretical_ev_ratio=dict(selected.evidence).get("theoretical_ev_ratio"),
                proposal_age_ms=dict(selected.evidence).get("proposal_age_ms"),
                entry_mode="EXECUTABLE_SIGNAL",
                execution_environment="DEMO",
            )
        order_id = getattr(persisted, "order_id", None)
        message_id = getattr(persisted, "message_id", None)
        if isinstance(order_id, str) and order_id:
            with self._cache_lock:
                if self._inflight_order_ids is not None:
                    self._inflight_order_ids.add(order_id)
                    self._order_scope[order_id] = (selected.strategy_id, selected.symbol)
            # The worker can synchronously reject during dispatch. In that path the
            # database is already terminal/flat when submit() returns, but no later
            # order event is guaranteed to arrive to clear the in-memory inflight set.
            # Reloading only after a financial submission keeps the tick path hot and
            # prevents a rejected order from freezing the bot as "order in flight".
            self.reload_runtime_caches()
            if not self._order_is_terminal(order_id):
                with self._cache_lock:
                    if self._inflight_order_ids is not None:
                        self._inflight_order_ids.add(order_id)
                        self._order_scope[order_id] = (selected.strategy_id, selected.symbol)
            else:
                rejection_reason = self._dispatch_rejection_reason(message_id)
                if rejection_reason is not None:
                    self._emit(
                        "autotrader_order_rejected",
                        reason_code=rejection_reason,
                        strategy_id=selected.strategy_id,
                        symbol=selected.symbol,
                        amount_minor_units=allocated_stake.minor_units,
                    )
                    return self._skip(f"BOT_ENTRY_REJECTED_{rejection_reason}")
        self._consume_performance_probe(selected)
        self._set_reason("BOT_ORDER_SUBMITTED")
        return True

    def _payout_routed_candidate(
        self,
        snapshot: DerivTelemetrySnapshot,
    ) -> tuple[_ExecutionCandidate | None, str | None]:
        cache = self._proposal_cache
        if cache is None:
            return None, None
        now = self._monotonic()
        decision = cache.select(now_monotonic=now)
        if decision.selection is None:
            if decision.reason_code == "SESSION_PAYOUT_BELOW_FLOOR":
                self._emit(
                    "payout_routed_differs_payout_below_floor",
                    reason_code=decision.reason_code,
                    observed_payout_return_ratio=(
                        None
                        if decision.observed_payout_return_ratio is None
                        else str(decision.observed_payout_return_ratio)
                    ),
                    minimum_payout_return_ratio=(
                        None
                        if decision.minimum_payout_return_ratio is None
                        else str(decision.minimum_payout_return_ratio)
                    ),
                    execution_environment="DEMO",
                )
            return None, decision.reason_code
        selection = decision.selection
        quote = selection.quote
        epoch = (
            int(snapshot.digit_frequency.total_ticks)
            if snapshot.digit_frequency is not None
            else int(now * 1_000)
        )
        return (
            _ExecutionCandidate(
                symbol=quote.broker_symbol,
                strategy_id=PAYOUT_ROUTED_DIFFERS_STRATEGY_ID,
                contract_type="DIGITDIFF",
                barrier=quote.barrier,
                epoch=epoch,
                estimated_probability_pct=Decimal("90.0"),
                required_probability_pct=Decimal("0.0"),
                conditional_sample=0,
                signal_state=ShadowSignalState.EXECUTABLE_SIGNAL,
                evidence=selection.evidence,
            ),
            None,
        )

    @staticmethod
    def _execution_candidates(
        snapshot: DerivTelemetrySnapshot,
        *,
        automatic: bool,
    ) -> tuple[_ExecutionCandidate, ...]:
        if automatic:
            if snapshot.strategy_matrix:
                return tuple(
                    _ExecutionCandidate(
                        symbol=str(item.last_signal_symbol),
                        strategy_id=str(item.strategy_id),
                        contract_type=str(item.last_contract_type),
                        barrier=item.last_barrier,
                        epoch=int(item.last_signal_epoch),
                        estimated_probability_pct=Decimal(item.estimated_probability_pct),
                        required_probability_pct=Decimal(item.required_probability_pct),
                        conditional_sample=item.conditional_sample,
                    )
                    for item in snapshot.strategy_matrix
                    if item.signal_state is ShadowSignalState.SHADOW_SIGNAL
                    and str(item.strategy_id) != PAYOUT_ROUTED_DIFFERS_STRATEGY_ID
                    and item.last_signal_symbol is not None
                    and item.last_contract_type is not None
                    and item.last_signal_epoch is not None
                    and item.estimated_probability_pct is not None
                    and item.required_probability_pct is not None
                )
            return tuple(
                _ExecutionCandidate(
                    symbol=item.symbol,
                    strategy_id=str(item.strategy_id),
                    contract_type=str(item.contract_type),
                    barrier=item.barrier,
                    epoch=int(item.last_signal_epoch),
                    estimated_probability_pct=Decimal(item.estimated_probability_pct),
                    required_probability_pct=Decimal(item.required_probability_pct),
                )
                for item in snapshot.asset_ranking
                if item.state is DigitAssetShadowState.CANDIDATE
                and item.strategy_id is not None
                and str(item.strategy_id) != PAYOUT_ROUTED_DIFFERS_STRATEGY_ID
                and item.contract_type is not None
                and item.last_signal_epoch is not None
                and item.estimated_probability_pct is not None
                and item.required_probability_pct is not None
            )
        assert snapshot.digit_frequency is not None
        return tuple(
            _ExecutionCandidate(
                symbol=str(item.last_signal_symbol or snapshot.digit_frequency.symbol),
                strategy_id=str(item.strategy_id),
                contract_type=str(item.last_contract_type),
                barrier=item.last_barrier,
                epoch=int(item.last_signal_epoch),
                estimated_probability_pct=Decimal(item.estimated_probability_pct),
                required_probability_pct=Decimal(item.required_probability_pct),
                conditional_sample=item.conditional_sample,
            )
            for item in snapshot.synthetic_strategies
            if item.signal_state is ShadowSignalState.SHADOW_SIGNAL
            and str(item.strategy_id) != PAYOUT_ROUTED_DIFFERS_STRATEGY_ID
            and item.last_signal_epoch is not None
            and item.last_contract_type is not None
            and item.last_direction is not None
            and item.estimated_probability_pct is not None
            and item.required_probability_pct is not None
        )

    @staticmethod
    def _execution_strategy_ids(config: object) -> frozenset[str] | None:
        mode = getattr(config, "selection_mode", StrategySelectionMode.SINGLE)
        if not isinstance(mode, StrategySelectionMode):
            try:
                mode = StrategySelectionMode(str(mode))
            except ValueError:
                return None
        if mode is StrategySelectionMode.SINGLE:
            active = str(getattr(config, "active_strategy_id", ""))
            enabled = frozenset(getattr(config, "enabled_strategy_ids", frozenset()))
            if not enabled or active not in DERIV_DIGIT_STRATEGY_ALLOWLIST:
                return None
            return frozenset({active})
        if mode is StrategySelectionMode.MULTI:
            enabled = frozenset(getattr(config, "enabled_strategy_ids", frozenset()))
            return enabled or None
        return frozenset(DERIV_DIGIT_STRATEGY_ALLOWLIST)

    @staticmethod
    def _configured_edge_floor(confidence_pct: Decimal) -> Decimal:
        """Map the 90–98 conservatism control to a 1–3 percentage-point edge floor."""

        return Decimal("1.00") + (confidence_pct - Decimal("90.0")) / Decimal("4")

    @staticmethod
    def _fallback_net_profit_ratio(contract_type: str) -> Decimal:
        """Deterministic fake/test fallback; live Demo always supplies a proposal quote."""

        return Decimal("0.10") if contract_type.upper() == "DIGITDIFF" else Decimal("0.90")

    def _performance_allows(
        self,
        candidate: _ExecutionCandidate,
        edge_floor: Decimal,
    ) -> tuple[bool, str | None]:
        if candidate.strategy_id == PAYOUT_ROUTED_DIFFERS_STRATEGY_ID:
            return True, None
        probe_key = (candidate.strategy_id, candidate.symbol)
        with self._cache_lock:
            if not self._performance_cache_initialized:
                return False, "PERFORMANCE_CACHE_UNAVAILABLE"
            samples = tuple(self._performance.get(probe_key, ()))
            reset_at = self._performance_reset_at.get(probe_key)
        if reset_at is not None:
            samples = tuple(
                item
                for item in samples
                if item.observed_monotonic is None or item.observed_monotonic >= reset_at
            )
        # Use the intersection of the count and time windows.  This means an
        # old losing run cannot punish a new run indefinitely.
        samples = samples[-self._runtime.risk_ledger.digit_config.performance_window_trades :]
        cutoff = self._utc_clock() - timedelta(
            hours=self._runtime.risk_ledger.digit_config.performance_window_hours
        )
        samples = tuple(item for item in samples if item.settled_at >= cutoff)
        settled = len(samples)
        total_pnl = sum(item.pnl_minor for item in samples)
        config = self._runtime.risk_ledger.digit_config
        required_original = candidate.required_probability_pct
        raw_required = required_original + edge_floor
        if settled:
            if settled >= 10 and total_pnl <= 0:
                if (
                    self._performance_probe_remaining.get(probe_key, 0) <= 0
                    and probe_key in self._performance_probe_pending
                ):
                    self._performance_gate_details[probe_key] = self._format_gate_detail(
                        candidate,
                        required_original + edge_floor,
                        candidate.estimated_probability_pct,
                        total_pnl,
                        settled,
                        0,
                        0,
                    )
                    return False, "PERFORMANCE_COOLDOWN"
                if (
                    self._performance_probe_remaining.get(probe_key, 0) <= 0
                    and probe_key not in self._performance_probe_pending
                ):
                    last_settled = samples[-1].settled_at
                    monotonic_now = self._monotonic()
                    cooldown_until = self._performance_cooldown_until.get(probe_key)
                    utc_remaining = (
                        0
                        if last_settled is None
                        else int(
                            max(
                                0,
                                (
                                    last_settled + self._PERFORMANCE_COOLDOWN - self._utc_clock()
                                ).total_seconds(),
                            )
                        )
                    )
                    if cooldown_until is None and utc_remaining > 0:
                        cooldown_until = monotonic_now + utc_remaining
                        self._performance_cooldown_until[probe_key] = cooldown_until
                    if cooldown_until is not None and monotonic_now < cooldown_until:
                        remaining_seconds = max(0, int(cooldown_until - monotonic_now))
                        self._performance_gate_details[probe_key] = self._format_gate_detail(
                            candidate,
                            required_original + edge_floor,
                            candidate.estimated_probability_pct,
                            total_pnl,
                            settled,
                            remaining_seconds,
                            self._PERFORMANCE_PROBE_ORDERS,
                        )
                        return False, "PERFORMANCE_COOLDOWN"
                    self._performance_cooldown_until.pop(probe_key, None)
                    self._performance_probe_remaining[probe_key] = self._PERFORMANCE_PROBE_ORDERS
                    self._emit(
                        "performance_cooldown_probe_granted",
                        reason_code="PERFORMANCE_COOLDOWN_EXPIRED",
                        strategy_id=candidate.strategy_id,
                        symbol=candidate.symbol,
                        timestamp_monotonic=str(monotonic_now),
                        probes=self._PERFORMANCE_PROBE_ORDERS,
                    )
            else:
                self._performance_probe_remaining.pop(probe_key, None)
            wins = [item.pnl_minor for item in samples if item.pnl_minor > 0]
            losses = [-item.pnl_minor for item in samples if item.pnl_minor < 0]
            if settled >= 10 and wins and losses:
                win = Decimal(sum(wins)) / Decimal(len(wins))
                loss = Decimal(sum(losses)) / Decimal(len(losses))
                if win > 0 and loss > 0:
                    payout_break_even = loss * Decimal(100) / (loss + win)
                    raw_required = max(required_original, payout_break_even) + edge_floor
        cap = required_original + config.performance_ratchet_cap_pp
        applied_required = min(raw_required, cap)
        if raw_required > cap:
            self._emit(
                "performance_ratchet_capped",
                reason_code="PERFORMANCE_RATCHET_CAPPED",
                strategy_id=candidate.strategy_id,
                symbol=candidate.symbol,
                required_raw=str(raw_required),
                required_applied=str(applied_required),
                performance_ratchet_capped=True,
            )
        self._performance_gate_details[probe_key] = self._format_gate_detail(
            candidate,
            applied_required,
            candidate.estimated_probability_pct,
            total_pnl,
            settled,
            0,
            self._performance_probe_remaining.get(probe_key, 0),
        )
        allowed = candidate.estimated_probability_pct >= applied_required
        return allowed, None if allowed else "NO_POSITIVE_NET_EDGE"

    def _format_gate_detail(
        self,
        candidate: _ExecutionCandidate,
        required: Decimal,
        estimated: Decimal,
        pnl_minor: int,
        settled: int,
        cooldown_remaining: int,
        probes: int,
    ) -> str:
        minutes, seconds = divmod(max(0, cooldown_remaining), 60)
        return (
            f"exigido {required:.2f}% · estimado {estimated:.2f}% · "
            f"P&L janela USD {Decimal(pnl_minor) / Decimal(100):.2f} · "
            f"{settled} operações · cooldown {minutes:02d}:{seconds:02d} · "
            f"{probes} sonda(s) após expiração"
        )

    @staticmethod
    def _settled_at(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)

    def _consume_performance_probe(self, candidate: _ExecutionCandidate) -> None:
        key = (candidate.strategy_id, candidate.symbol)
        remaining = self._performance_probe_remaining.get(key)
        if remaining is None:
            return
        if remaining <= 1:
            self._performance_probe_remaining.pop(key, None)
        else:
            self._performance_probe_remaining[key] = remaining - 1
        self._performance_probe_pending.add(key)

    def _set_performance_reason(self, candidate: _ExecutionCandidate, reason: str) -> None:
        detail = self._performance_gate_details.get((candidate.strategy_id, candidate.symbol))
        with self._status_lock:
            self._last_reason_detail = detail

    def _remember_signal(self, candidate: _ExecutionCandidate) -> None:
        key = (candidate.symbol, candidate.strategy_id, candidate.epoch)
        with self._signal_lock:
            if key in self._evaluated_signal_keys:
                return
            self._evaluated_signal_keys.add(key)
            self._evaluated_signal_order.append(key)
            if len(self._evaluated_signal_order) > 512:
                expired = self._evaluated_signal_order.pop(0)
                self._evaluated_signal_keys.discard(expired)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            if self._stop.is_set():
                return
            while not self._stop.is_set() and self._process_pending_once():
                pass

    def _process_pending_once(self) -> bool:
        """Evaluate one coalesced notification generation; deterministic for unit tests."""

        with self._signal_lock:
            target_generation = self._notification_generation
            if target_generation == self._evaluated_notification_generation:
                self._wake.clear()
                return False
            remaining = max(
                0.0,
                self._minimum_evaluation_interval - (self._monotonic() - self._last_evaluation_at),
            )
        if remaining > 0 and self._interval_waiter(remaining):
            return False
        self.evaluate_once()
        with self._signal_lock:
            self._evaluated_notification_generation = target_generation
            self._last_evaluation_at = self._monotonic()
        return True

    def notify_tick(self) -> None:
        self._notify()

    def notify_order_event(
        self,
        event: BrokerOrderEvent,
        result: BrokerEventApplyResult,
    ) -> None:
        with self._cache_lock:
            if result.status is BrokerEventApplyStatus.CONFLICT or result.order_state is None:
                self._inflight_order_ids = None
            elif self._inflight_order_ids is not None:
                order_id = event.client_order_ref
                if result.order_state.is_terminal:
                    self._inflight_order_ids.discard(order_id)
                    if (
                        result.order_state is OrderState.SETTLED
                        and event.result_minor is not None
                        and result.status
                        in {
                            BrokerEventApplyStatus.APPLIED,
                            BrokerEventApplyStatus.APPLIED_WITH_GAP,
                        }
                    ):
                        scope = self._order_scope.get(order_id)
                        if scope is not None:
                            samples = self._performance.setdefault(scope, deque(maxlen=30))
                            if not any(item.order_id == order_id for item in samples):
                                samples.append(
                                    _PerformanceSample(
                                        order_id,
                                        event.result_minor,
                                        event.observed_at,
                                    )
                                )
                                self._performance_probe_pending.discard(scope)
                    self._order_scope.pop(order_id, None)
                else:
                    self._inflight_order_ids.add(order_id)
        self._notify()

    def _notify(self) -> None:
        with self._status_lock:
            self._last_signal_at = self._monotonic()
        with self._signal_lock:
            self._notification_generation += 1
        self._wake.set()

    def _has_open_deriv_order(self) -> bool:
        with self._cache_lock:
            return self._inflight_order_ids is None or bool(self._inflight_order_ids)

    def _order_is_terminal(self, order_id: str) -> bool:
        reader = getattr(self._runtime, "reader", None)
        lookup = getattr(reader, "one", None)
        if not callable(lookup):
            return False
        try:
            row = lookup("orders", "order_id", order_id)
        except Exception:
            return False
        if row is None:
            return False
        return str(row.get("state")) in self._TERMINAL_STATES

    def _dispatch_rejection_reason(self, message_id: object) -> str | None:
        if not isinstance(message_id, str) or not message_id:
            return None
        reader = getattr(self._runtime, "reader", None)
        lookup = getattr(reader, "one", None)
        if not callable(lookup):
            return None
        try:
            row = lookup("outbox_messages", "message_id", message_id)
        except Exception:
            return None
        if row is None:
            return None
        reason = row.get("state_reason")
        if not isinstance(reason, str) or not reason:
            return None
        normalized = re.sub(r"[^A-Z0-9_]+", "_", reason.upper()).strip("_")[:64]
        return normalized or None

    def reload_runtime_caches(self, *, report_divergence: bool = True) -> None:
        """Reload authoritative projections outside the tick evaluation path."""

        try:
            orders = self._runtime.reader.list_nonterminal_orders()
            session_reader = getattr(
                self._runtime.reader,
                "digit_test_session_started_at",
                None,
            )
            session_started_at = session_reader() if callable(session_reader) else None
            if session_started_at is None:
                performance_rows = self._runtime.reader.deriv_recent_strategy_settlements(
                    limit_per_scope=30
                )
            else:
                performance_rows = self._runtime.reader.deriv_recent_strategy_settlements(
                    limit_per_scope=30,
                    since_utc=session_started_at,
                )
            inflight = {
                str(row["order_id"])
                for row in orders
                if str(row.get("broker")) == Broker.DERIV.value
                and str(row.get("state")) not in self._TERMINAL_STATES
            }
            scopes = {
                str(row["order_id"]): (str(row["strategy_id"]), str(row["symbol"]))
                for row in (*orders, *performance_rows)
                if row.get("strategy_id") and row.get("symbol")
            }
            performance: dict[tuple[str, str], deque[_PerformanceSample]] = {}
            for row in performance_rows:
                settled_at = self._settled_at(row.get("settled_at"))
                if settled_at is None:
                    raise ValueError("invalid settlement timestamp")
                key = (str(row["strategy_id"]), str(row["symbol"]))
                performance.setdefault(key, deque(maxlen=30)).append(
                    _PerformanceSample(
                        str(row["order_id"]),
                        int(row["realized_pnl_minor"]),
                        settled_at,
                        self._monotonic(),
                    )
                )
        except Exception:
            with self._cache_lock:
                self._inflight_order_ids = None
                self._performance_cache_initialized = False
            self._emit(
                "autotrader_runtime_cache_reload_failed",
                reason_code="AUTOTRADER_CACHE_UNAVAILABLE",
            )
            return
        with self._cache_lock:
            previous = self._inflight_order_ids
            if report_divergence and previous is not None and previous != inflight:
                self._emit(
                    "autotrader_inflight_cache_divergence",
                    reason_code="AUTOTRADER_CACHE_RECONCILED",
                    cached_count=len(previous),
                    persisted_count=len(inflight),
                )
            self._inflight_order_ids = inflight
            self._order_scope = scopes
            self._performance = performance
            self._performance_cache_initialized = True

    def _emit(self, event_name: str, **fields: str | int | bool | None) -> None:
        sink = getattr(self._runtime, "event_sink", None)
        emit = getattr(sink, "emit", None)
        if callable(emit):
            emit(event_name, **fields)

    def _skip(self, reason: str) -> bool:
        self._set_reason(reason)
        return False

    def _set_reason(
        self,
        reason: str,
        *,
        symbol: str | None = None,
        armed_epoch: int | None = None,
        rearm_notice: bool = False,
        reset_duration: bool = False,
    ) -> None:
        with self._status_lock:
            changed = reason != self._last_reason
            if changed or reset_duration:
                self._reason_since = self._monotonic()
            self._last_reason = reason
            if changed and reason not in {
                "BOT_PERFORMANCE_COOLDOWN",
                "BOT_PERFORMANCE_CACHE_UNAVAILABLE",
                "BOT_NO_POSITIVE_NET_EDGE",
            }:
                self._last_reason_detail = None
            if changed or symbol is not None:
                self._reason_symbol = symbol
            if changed or armed_epoch is not None:
                self._armed_epoch = armed_epoch
            if changed:
                self._rearm_notice = rearm_notice
            elif rearm_notice:
                self._rearm_notice = True

    @staticmethod
    def _reason_description(reason: str, symbol: str | None, rearm_notice: bool) -> str:
        descriptions = {
            "BOT_WAITING_FOR_LIVE_DERIV": "Aguardando conexão autenticada com a conta Demo.",
            "BOT_DERIV_ACCOUNT_NOT_CONNECTED": "Aguardando conexão autenticada com a conta Demo.",
            "BOT_WAITING_FOR_NEW_TICK": (
                "Aguardando um novo tick e um sinal posterior ao acionamento."
            ),
            "BOT_WAITING_FOR_STRATEGY_SIGNAL": (
                "Aguardando um sinal válido da estratégia selecionada."
            ),
            "BOT_NO_STRATEGY_SELECTED": (
                "Nenhuma estratégia está habilitada; a análise shadow continua visível."
            ),
            "BOT_STRESS_MODE_REQUIRES_DEMO": (
                "O modo estresse com várias estratégias está disponível somente na conta Demo."
            ),
            "BOT_WARMING_UP_TICKS": "Aquecendo a amostra de mercado antes de avaliar entradas.",
            "BOT_ORDER_IN_FLIGHT": (
                "Há uma operação em andamento; nenhuma nova entrada será enviada."
            ),
            "BOT_ORDER_STATE_UNAVAILABLE": (
                "Estado das operações indisponível; novas entradas estão bloqueadas por segurança."
            ),
            "BOT_PERFORMANCE_CACHE_UNAVAILABLE": (
                "Histórico de desempenho indisponível; avaliação bloqueada por segurança."
            ),
            "BOT_PERFORMANCE_COOLDOWN": (
                "Estratégia em pausa de desempenho por até 10 minutos após resultado "
                "líquido negativo nas últimas operações. A retomada é automática."
            ),
            "BOT_RISK_COOLDOWN_ACTIVE": (
                "Pausa de segurança após a sequência de perdas. A contagem é atualizada "
                "a cada tick e a análise volta automaticamente quando o prazo termina."
            ),
            "BOT_MARTINGALE_PIN_RELEASED": (
                "O pino de recuperação expirou; a sequência foi encerrada e a seleção "
                "normal voltou."
            ),
            "BOT_NO_POSITIVE_NET_EDGE": (
                "O filtro de qualidade não encontrou vantagem estatística suficiente."
            ),
            "BOT_OPERATOR_NOT_ARMED": "Bot pausado. Use Ligar Bot para iniciar uma nova rodada.",
            "BOT_DISABLED_OR_HEALTH_BLOCKED": (
                "Entrada bloqueada pelos controles de segurança do sistema."
            ),
            "BOT_ORDER_SUBMITTED": "Ordem Demo enviada; aguardando eventos da operação.",
            "SESSION_NO_FRESH_PROPOSAL": "Aguardando cotação atualizada da corretora.",
            "SESSION_PAYOUT_BELOW_FLOOR": (
                "Payout atual abaixo do mínimo configurado para esta sessão."
            ),
            "SESSION_MARTINGALE_NOT_SUPPORTED": (
                "Esta estratégia de sessão não usa Martingale nesta fase."
            ),
        }
        if reason == "BOT_MARTINGALE_ASSET_PINNED":
            base = f"Recuperação Martingale aguardando sinal no ativo {symbol or 'fixado'}."
        else:
            base = descriptions.get(reason, "Aguardando a próxima condição operacional válida.")
        if rearm_notice:
            return f"{base} O rearme reiniciou a espera e descartou sinais anteriores."
        return base
