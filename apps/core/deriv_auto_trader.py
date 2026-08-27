from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from apps.core.deriv_telemetry import DerivTelemetrySnapshot, DerivTelemetrySource
from apps.core.runtime import CoreRuntime
from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    Direction,
    OrderRequest,
    utc_now,
)
from packages.persistence.writer import BrokerEventApplyResult
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
    ) -> None:
        if not account_id.strip():
            raise ValueError("Deriv automated trader requires an account id")
        if minimum_ticks < 2 or minimum_order_interval_seconds < 0:
            raise ValueError("Deriv automated trader limits are invalid")
        self._runtime = runtime
        self._account_id = account_id
        self._telemetry = telemetry
        self._minimum_ticks = minimum_ticks
        self._minimum_interval = minimum_order_interval_seconds
        self._monotonic = monotonic_clock
        self._utc_clock = utc_clock
        self._operator_armed = operator_armed
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_order_at = float("-inf")
        self._evaluated_signal_keys: set[tuple[str, str, int]] = set()
        self._evaluated_signal_order: list[tuple[str, str, int]] = []
        self._minimum_signal_epochs: dict[tuple[str, str], int] = {}
        self._signal_lock = threading.RLock()
        self._performance_probe_remaining: dict[tuple[str, str], int] = {}
        self._status_lock = threading.Lock()
        self._last_reason = "BOT_WAITING_FOR_LIVE_DERIV"
        self._last_signal_at = float("-inf")
        self._last_wake_latency_microseconds = 0
        self._last_analysis_latency_microseconds = 0
        self._last_submission_latency_microseconds = 0

    @property
    def last_reason(self) -> str:
        with self._status_lock:
            return self._last_reason

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
        self._wake.set()
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
            return
        config = self._runtime.risk_ledger.digit_config
        candidates = self._execution_candidates(snapshot, automatic=config.auto_select_symbol)
        with self._signal_lock:
            self._minimum_signal_epochs = {
                (item.symbol, item.strategy_id): item.epoch for item in candidates
            }
        self._notify()

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
        snapshot = self._telemetry()
        if (
            snapshot is None
            or snapshot.source is not DerivTelemetrySource.DEMO_LIVE
            or not snapshot.connected
        ):
            return self._skip("BOT_DERIV_ACCOUNT_NOT_CONNECTED")
        frequency = snapshot.digit_frequency
        if frequency is None or frequency.total_ticks < self._minimum_ticks:
            return self._skip("BOT_WARMING_UP_TICKS")
        if self._has_open_deriv_order():
            return self._skip("BOT_ORDER_IN_FLIGHT")

        config = self._runtime.risk_ledger.digit_config
        candidates = self._execution_candidates(snapshot, automatic=config.auto_select_symbol)
        candidates = tuple(
            item for item in candidates if item.strategy_id == config.active_strategy_id
        )
        if not candidates:
            return self._skip("BOT_WAITING_FOR_STRATEGY_SIGNAL")

        metrics_reader = getattr(self._runtime.risk_ledger, "get_digit_metrics", None)
        risk_metrics = metrics_reader() if callable(metrics_reader) else None
        martingale_step = 0 if risk_metrics is None else int(risk_metrics.martingale_step)
        recovery_symbol = None if risk_metrics is None else risk_metrics.recovery_symbol
        if martingale_step > 0:
            if recovery_symbol is None:
                return self._skip("BOT_MARTINGALE_STATE_INCOMPLETE")
            candidates = tuple(item for item in candidates if item.symbol == recovery_symbol)
            if not candidates:
                return self._skip("BOT_MARTINGALE_ASSET_PINNED")

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
            for item in unevaluated:
                self._remember_signal(item)
            reason = (
                "BOT_PERFORMANCE_COOLDOWN"
                if any(item_reason == "PERFORMANCE_COOLDOWN" for _, _, item_reason in decisions)
                else "BOT_NO_POSITIVE_NET_EDGE"
            )
            return self._skip(reason)
        selected = min(
            eligible,
            key=lambda item: (
                -(item.estimated_probability_pct - item.required_probability_pct),
                item.strategy_id,
                item.symbol,
            ),
        )
        # A one-tick signal is never retried later. Risk/transport failures require a new signal.
        self._remember_signal(selected)
        now = self._monotonic()
        if now - self._last_order_at < self._minimum_interval:
            return self._skip("BOT_ENTRY_THROTTLED")

        try:
            allocated_stake = self._runtime.risk_ledger.digit_entry_stake(self._runtime.health_gate)
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
            self._runtime.submit(request)
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
        self._consume_performance_probe(selected)
        self._set_reason("BOT_ORDER_SUBMITTED")
        return True

    @staticmethod
    def _execution_candidates(
        snapshot: DerivTelemetrySnapshot,
        *,
        automatic: bool,
    ) -> tuple[_ExecutionCandidate, ...]:
        if automatic:
            return tuple(
                _ExecutionCandidate(
                    symbol=item.symbol,
                    strategy_id=item.strategy_id.value,
                    contract_type=str(item.contract_type),
                    barrier=item.barrier,
                    epoch=int(item.last_signal_epoch),
                    estimated_probability_pct=Decimal(item.estimated_probability_pct),
                    required_probability_pct=Decimal(item.required_probability_pct),
                )
                for item in snapshot.asset_ranking
                if item.state is DigitAssetShadowState.CANDIDATE
                and item.strategy_id is not None
                and item.contract_type is not None
                and item.last_signal_epoch is not None
                and item.estimated_probability_pct is not None
                and item.required_probability_pct is not None
            )
        assert snapshot.digit_frequency is not None
        return tuple(
            _ExecutionCandidate(
                symbol=str(item.last_signal_symbol or snapshot.digit_frequency.symbol),
                strategy_id=item.strategy_id.value,
                contract_type=str(item.last_contract_type),
                barrier=item.last_barrier,
                epoch=int(item.last_signal_epoch),
                estimated_probability_pct=Decimal(item.estimated_probability_pct),
                required_probability_pct=Decimal(item.required_probability_pct),
            )
            for item in snapshot.synthetic_strategies
            if item.signal_state is ShadowSignalState.SHADOW_SIGNAL
            and item.last_signal_epoch is not None
            and item.last_contract_type is not None
            and item.last_direction is not None
            and item.estimated_probability_pct is not None
            and item.required_probability_pct is not None
        )

    @staticmethod
    def _configured_edge_floor(confidence_pct: Decimal) -> Decimal:
        """Map the 90–98 conservatism control to a 1–3 percentage-point edge floor."""

        return Decimal("1.00") + (confidence_pct - Decimal("90.0")) / Decimal("4")

    def _performance_allows(
        self,
        candidate: _ExecutionCandidate,
        edge_floor: Decimal,
    ) -> tuple[bool, str | None]:
        required = candidate.required_probability_pct
        probe_key = (candidate.strategy_id, candidate.symbol)
        performance_reader = getattr(self._runtime.reader, "deriv_strategy_performance", None)
        if callable(performance_reader):
            summary = performance_reader(
                candidate.strategy_id,
                symbol=candidate.symbol,
                limit=30,
            )
            settled = int(summary.get("settled_count") or 0)
            total_pnl = int(summary.get("total_pnl_minor") or 0)
            if settled >= 10 and total_pnl <= 0:
                if self._performance_probe_remaining.get(probe_key, 0) <= 0:
                    last_settled = self._settled_at(summary.get("last_settled_at"))
                    if (
                        last_settled is None
                        or self._utc_clock() - last_settled < self._PERFORMANCE_COOLDOWN
                    ):
                        return False, "PERFORMANCE_COOLDOWN"
                    self._performance_probe_remaining[probe_key] = self._PERFORMANCE_PROBE_ORDERS
            else:
                self._performance_probe_remaining.pop(probe_key, None)
            avg_win = summary.get("avg_win_minor")
            avg_loss = summary.get("avg_loss_minor")
            if settled >= 10 and avg_win is not None and avg_loss is not None:
                win = Decimal(str(avg_win))
                loss = Decimal(str(avg_loss))
                if win > 0 and loss > 0:
                    payout_break_even = loss * Decimal(100) / (loss + win)
                    required = max(required, payout_break_even)
        allowed = candidate.estimated_probability_pct >= required + edge_floor
        return allowed, None if allowed else "NO_POSITIVE_NET_EDGE"

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
            self._wake.clear()
            if self._stop.is_set():
                return
            self.evaluate_once()

    def notify_tick(self) -> None:
        self._notify()

    def notify_order_event(
        self,
        _event: BrokerOrderEvent,
        _result: BrokerEventApplyResult,
    ) -> None:
        self._notify()

    def _notify(self) -> None:
        with self._status_lock:
            self._last_signal_at = self._monotonic()
        self._wake.set()

    def _has_open_deriv_order(self) -> bool:
        return any(
            str(row.get("broker")) == Broker.DERIV.value
            and str(row.get("state")) not in self._TERMINAL_STATES
            for row in self._runtime.reader.ui_order_summaries(limit=100)
        )

    def _skip(self, reason: str) -> bool:
        self._set_reason(reason)
        return False

    def _set_reason(self, reason: str) -> None:
        with self._status_lock:
            self._last_reason = reason
