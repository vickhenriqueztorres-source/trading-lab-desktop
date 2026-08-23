from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum

from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSupervisor
from apps.core.worker_client import WorkerDispatchError
from packages.domain.market import BrokerAccountBalance, BrokerClockSnapshot


class DerivTelemetrySource(StrEnum):
    FAKE_SIMULATED = "FAKE_SIMULATED"
    PUBLIC_LIVE = "PUBLIC_LIVE"
    DEMO_LIVE = "DEMO_LIVE"


@dataclass(frozen=True, slots=True)
class DerivTelemetrySnapshot:
    source: DerivTelemetrySource
    connection_mode: str
    connected: bool
    balance: BrokerAccountBalance | None
    clock: BrokerClockSnapshot | None
    reason_code: str | None


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
    ) -> None:
        if not 0.5 <= poll_interval_seconds <= 60:
            raise ValueError("Deriv telemetry poll interval is outside bounds")
        self._supervisor = supervisor
        self._health_gate = health_gate
        self._source = source
        self._poll_interval = poll_interval_seconds
        self._lock = threading.Lock()
        self._snapshot = DerivTelemetrySnapshot(source, "UNKNOWN", False, None, None, "NOT_PROBED")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def snapshot(self) -> DerivTelemetrySnapshot:
        with self._lock:
            return self._snapshot

    def start(self) -> None:
        if self._thread is not None:
            return
        self.probe_once()
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
        self._thread = None

    def probe_once(self) -> DerivTelemetrySnapshot:
        try:
            client = self._supervisor.client
            clock = client.broker_clock()
            trusted = clock.is_synced
            if trusted:
                self._health_gate.clear_scope("DERIV", self._HEALTH_ACCOUNT, "MD_CLOCK_UNTRUSTED")
            else:
                self._health_gate.block_scope("DERIV", self._HEALTH_ACCOUNT, "MD_CLOCK_UNTRUSTED")
            balance = (
                client.broker_balance()
                if client.capabilities.connection_mode == "DEMO_AUTH_READ_ONLY"
                else None
            )
            snapshot = DerivTelemetrySnapshot(
                self._source,
                client.capabilities.connection_mode or "UNKNOWN",
                True,
                balance,
                clock,
                None if trusted else "MD_CLOCK_UNTRUSTED",
            )
        except (RuntimeError, WorkerDispatchError, ValueError):
            self._health_gate.block_scope("DERIV", self._HEALTH_ACCOUNT, "MD_CLOCK_UNTRUSTED")
            snapshot = DerivTelemetrySnapshot(
                self._source,
                "UNKNOWN",
                False,
                None,
                None,
                "DERIV_TELEMETRY_UNAVAILABLE",
            )
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            self.probe_once()
