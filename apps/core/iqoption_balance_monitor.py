"""Independent Core observation of the selected IQ Option account balance."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from apps.core.health import HealthGate
from apps.core.iqoption_connection_safety import IQOptionMessageBudget
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSupervisor
from apps.core.worker_client import WorkerDispatchError
from packages.domain.market import BrokerAccountBalance
from packages.domain.models import Broker

IQOPTION_BALANCE_POLL_INTERVAL_SECONDS = 5.0
IQOPTION_BALANCE_MAX_AGE_SECONDS = 15.0
IQOPTION_BALANCE_STALE_REASON = "IQOPTION_BALANCE_STALE"

_TRANSPORT_FAILURES = frozenset(
    {
        "IQOPTION_WEBSOCKET_UNAVAILABLE",
        "IQOPTION_RESPONSE_TOO_LARGE",
        "IQOPTION_AUTH_FAILED",
        "IPC_CONNECTION_LOST",
        "WORKER_CRASHED",
        "WORKER_NOT_READY",
    }
)


class IQOptionBalanceQuality(StrEnum):
    """Operator-facing quality of the last broker-confirmed balance."""

    CONFIRMED = "CONFIRMED"
    RETRYING = "RETRYING"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class IQOptionBalanceSnapshot:
    balance: BrokerAccountBalance | None
    is_fresh: bool
    reason_code: str | None
    quality: IQOptionBalanceQuality
    consecutive_failures: int = 0
    core_received_monotonic: float | None = None


class IQOptionBalanceMonitor:
    """Poll balance independently from clock, strategy and arm state.

    A failed refresh retains the last broker timestamp and enters RETRYING while
    that observation is still inside the safety window.  Only expiry (or the
    absence of any confirmed observation) closes the account Health Gate.  It
    never performs login, reconnect or any financial submission.
    """

    def __init__(
        self,
        supervisor: ReadOnlyWorkerSupervisor,
        health_gate: HealthGate,
        message_budget: IQOptionMessageBudget,
        *,
        initial_balance: BrokerAccountBalance | None = None,
        account_id: str = "IQOPTION_PRACTICE",
        poll_interval_seconds: float = IQOPTION_BALANCE_POLL_INTERVAL_SECONDS,
        max_age_seconds: float = IQOPTION_BALANCE_MAX_AGE_SECONDS,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        generation_is_current: Callable[[], bool] = lambda: True,
        balance_notifier: Callable[[BrokerAccountBalance], None] | None = None,
        disconnect_notifier: Callable[[str], None] | None = None,
    ) -> None:
        if not 1.0 <= poll_interval_seconds <= 60.0:
            raise ValueError("IQ Option balance poll interval is outside bounds")
        if not poll_interval_seconds <= max_age_seconds <= 120.0:
            raise ValueError("IQ Option balance freshness interval is outside bounds")
        self._supervisor = supervisor
        self._health_gate = health_gate
        self._message_budget = message_budget
        self._account_id = account_id
        self._poll_interval = poll_interval_seconds
        self._max_age = max_age_seconds
        self._utc_clock = utc_clock
        self._monotonic = monotonic
        self._generation_is_current = generation_is_current
        self._balance_notifier = balance_notifier
        self._disconnect_notifier = disconnect_notifier
        initial_received_mono = self._monotonic() if initial_balance is not None else None
        initial_fresh = self._is_observation_fresh(initial_balance, initial_received_mono)
        self._snapshot = IQOptionBalanceSnapshot(
            balance=initial_balance,
            is_fresh=initial_fresh,
            reason_code=None if initial_fresh else IQOPTION_BALANCE_STALE_REASON,
            quality=(
                IQOptionBalanceQuality.CONFIRMED
                if initial_fresh
                else IQOptionBalanceQuality.STALE
                if initial_balance is not None
                else IQOptionBalanceQuality.UNAVAILABLE
            ),
            core_received_monotonic=initial_received_mono,
        )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def snapshot(self) -> IQOptionBalanceSnapshot:
        with self._lock:
            current = self._snapshot
        fresh = self._is_observation_fresh(
            current.balance,
            current.core_received_monotonic,
        )
        if fresh == current.is_fresh:
            return current
        return IQOptionBalanceSnapshot(
            balance=current.balance,
            is_fresh=fresh,
            reason_code=current.reason_code or IQOPTION_BALANCE_STALE_REASON,
            quality=(
                IQOptionBalanceQuality.STALE
                if current.balance is not None
                else IQOptionBalanceQuality.UNAVAILABLE
            ),
            consecutive_failures=current.consecutive_failures,
            core_received_monotonic=current.core_received_monotonic,
        )

    @property
    def age_seconds(self) -> float | None:
        """Return the current source age without comparing process monotonic epochs."""

        with self._lock:
            balance = self._snapshot.balance
            received_mono = self._snapshot.core_received_monotonic
        if balance is None:
            return None
        if received_mono is not None and balance.connection_generation > 0:
            return balance.source_age_seconds + max(
                0.0,
                self._monotonic() - received_mono,
            )
        return max(0.0, (self._utc_clock() - balance.observed_at_utc).total_seconds())

    def start(self) -> None:
        if self._thread is not None or not self._is_current():
            return
        if self.snapshot.is_fresh:
            self._clear_stale_gate()
        else:
            self.probe_once()
        self._thread = threading.Thread(
            target=self._run,
            name="iqoption-balance-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def probe_once(self) -> IQOptionBalanceSnapshot:
        if not self._is_current():
            return self.snapshot
        admission = self._message_budget.try_acquire_operational(self._monotonic())
        if not admission.allowed:
            current = self.snapshot
            if current.is_fresh and self._is_observation_fresh(
                current.balance,
                current.core_received_monotonic,
            ):
                return current
            return self._publish_failure("IQOPTION_MESSAGE_BUDGET_EXHAUSTED")
        try:
            probe_started = self._monotonic()
            balance = self._supervisor.client.broker_balance()
            received_mono = self._monotonic()
            # Include the complete IPC call conservatively. The worker's
            # source age remains the authority for the original observation.
            effective_source_age = balance.source_age_seconds + max(
                0.0,
                received_mono - probe_started,
            )
            if effective_source_age > self._max_age:
                return self._publish_failure(IQOPTION_BALANCE_STALE_REASON)
        except WorkerDispatchError as exc:
            reason = exc.code.value
            snapshot = self._publish_failure(reason)
            if reason in _TRANSPORT_FAILURES:
                self._notify_disconnect(reason)
            return snapshot
        except (OSError, RuntimeError, ValueError):
            return self._publish_failure("IQOPTION_BALANCE_UNAVAILABLE")
        if not self._is_current():
            return self.snapshot
        snapshot = IQOptionBalanceSnapshot(
            balance,
            True,
            None,
            IQOptionBalanceQuality.CONFIRMED,
            core_received_monotonic=received_mono,
        )
        with self._lock:
            self._snapshot = snapshot
        self._clear_stale_gate()
        notifier = self._balance_notifier
        if notifier is not None:
            notifier(balance)
        return snapshot

    def _run(self) -> None:
        next_due = self._monotonic() + self._poll_interval
        while self._is_current():
            if self._stop.wait(max(0.0, next_due - self._monotonic())):
                return
            if not self._is_current():
                return
            self.probe_once()
            next_due += self._poll_interval
            if next_due < self._monotonic():
                next_due = self._monotonic() + self._poll_interval

    def _publish_failure(self, reason_code: str) -> IQOptionBalanceSnapshot:
        if not self._is_current():
            return self.snapshot
        with self._lock:
            balance = self._snapshot.balance
            received_mono = self._snapshot.core_received_monotonic
            still_fresh = self._is_observation_fresh(balance, received_mono)
            snapshot = IQOptionBalanceSnapshot(
                balance=balance,
                is_fresh=still_fresh,
                reason_code=reason_code,
                quality=(
                    IQOptionBalanceQuality.RETRYING
                    if still_fresh
                    else IQOptionBalanceQuality.STALE
                    if balance is not None
                    else IQOptionBalanceQuality.UNAVAILABLE
                ),
                consecutive_failures=self._snapshot.consecutive_failures + 1,
                core_received_monotonic=received_mono,
            )
            self._snapshot = snapshot
        if still_fresh:
            self._clear_stale_gate()
        else:
            self._health_gate.block_scope(
                Broker.IQ_OPTION.value,
                self._account_id,
                IQOPTION_BALANCE_STALE_REASON,
            )
        return snapshot

    def _clear_stale_gate(self) -> None:
        self._health_gate.clear_scope(
            Broker.IQ_OPTION.value,
            self._account_id,
            IQOPTION_BALANCE_STALE_REASON,
        )

    def _notify_disconnect(self, reason_code: str) -> None:
        notifier = self._disconnect_notifier
        if notifier is not None and self._is_current():
            notifier(reason_code)

    def _is_observation_fresh(
        self,
        balance: BrokerAccountBalance | None,
        core_received_monotonic: float | None,
    ) -> bool:
        if balance is None:
            return False
        if core_received_monotonic is not None and balance.connection_generation > 0:
            age = balance.source_age_seconds + max(
                0.0,
                self._monotonic() - core_received_monotonic,
            )
            return age <= self._max_age
        # Compatibility for legacy snapshots only; a small future wall delta
        # is diagnostic noise, not evidence that the source is stale.
        age = (self._utc_clock() - balance.observed_at_utc).total_seconds()
        return -1.0 <= age <= self._max_age

    def _is_current(self) -> bool:
        return not self._stop.is_set() and self._generation_is_current()


__all__ = [
    "IQOPTION_BALANCE_MAX_AGE_SECONDS",
    "IQOPTION_BALANCE_POLL_INTERVAL_SECONDS",
    "IQOPTION_BALANCE_STALE_REASON",
    "IQOptionBalanceQuality",
    "IQOptionBalanceMonitor",
    "IQOptionBalanceSnapshot",
]
