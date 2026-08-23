from __future__ import annotations

import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from apps.core.health import HealthGate
from apps.core.worker_client import SocketWorkerClient, WorkerDispatchError
from apps.simulated_worker.scenarios import WorkerScenario
from packages.observability.events import EventSink, NullEventSink
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.transport import FramedSocket
from packages.security import without_broker_credentials


class WorkerHealthState(StrEnum):
    STARTING = "STARTING"
    HANDSHAKING = "HANDSHAKING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    INCOMPATIBLE = "INCOMPATIBLE"
    STOPPED = "STOPPED"


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True, slots=True)
class RestartPolicy:
    max_crashes: int = 3
    window_seconds: float = 10.0
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 0.5
    open_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_crashes <= 0:
            raise ValueError("max_crashes must be positive")
        if (
            min(
                self.window_seconds,
                self.base_delay_seconds,
                self.max_delay_seconds,
                self.open_seconds,
            )
            <= 0
        ):
            raise ValueError("restart durations must be positive")


class CrashCircuitBreaker:
    def __init__(
        self,
        policy: RestartPolicy,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._monotonic = monotonic
        self._crashes: deque[float] = deque(maxlen=policy.max_crashes)
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        if self._monotonic() - self._opened_at >= self._policy.open_seconds:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def record_crash(self) -> None:
        now = self._monotonic()
        while self._crashes and now - self._crashes[0] > self._policy.window_seconds:
            self._crashes.popleft()
        self._crashes.append(now)
        if len(self._crashes) >= self._policy.max_crashes:
            self._opened_at = now

    def allow_restart(self) -> bool:
        return self.state is not CircuitState.OPEN

    def record_success(self) -> None:
        if self.state is CircuitState.HALF_OPEN:
            self._opened_at = None
            self._crashes.clear()

    def next_delay(self) -> float:
        attempt = max(0, len(self._crashes) - 1)
        return min(
            self._policy.base_delay_seconds * float(2**attempt),
            self._policy.max_delay_seconds,
        )


class WorkerSupervisor:
    def __init__(
        self,
        health_gate: HealthGate,
        *,
        scenario: WorkerScenario = WorkerScenario.ACCEPT,
        worker_protocol_version: int = 1,
        event_sink: EventSink | None = None,
        handshake_timeout: float = 5.0,
        response_timeout: float = 1.0,
        heartbeat_interval: float = 0.5,
        heartbeat_timeout: float = 0.25,
        restart_policy: RestartPolicy | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[float], float] | None = None,
        broker_store_path: Path | None = None,
        event_queue_size: int = 128,
    ) -> None:
        self._health_gate = health_gate
        self._scenario = scenario
        self._worker_protocol_version = worker_protocol_version
        self._event_sink = event_sink or NullEventSink()
        self._handshake_timeout = handshake_timeout
        self._response_timeout = response_timeout
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._sleeper = sleeper
        self._restart_policy = restart_policy or RestartPolicy()
        self._breaker = CrashCircuitBreaker(self._restart_policy, monotonic=monotonic)
        self._state_lock = threading.Lock()
        self._health_state = WorkerHealthState.STOPPED
        self._process: subprocess.Popen[bytes] | None = None
        self._client: SocketWorkerClient | None = None
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._stopping = False
        self._jitter = jitter or (lambda ceiling: random.uniform(0.0, ceiling))
        self._recorded_crash_pid: int | None = None
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        if broker_store_path is None:
            self._temporary_directory = tempfile.TemporaryDirectory(
                prefix="dualtrade-simulated-broker-"
            )
            broker_store_path = Path(self._temporary_directory.name) / "broker_state.db"
        self._broker_store_path = broker_store_path
        self._event_queue_size = event_queue_size
        self.last_shutdown_forced = False

    @property
    def health_state(self) -> WorkerHealthState:
        with self._state_lock:
            return self._health_state

    @property
    def circuit_state(self) -> CircuitState:
        return self._breaker.state

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    @property
    def client(self) -> SocketWorkerClient:
        if self._client is None:
            raise RuntimeError("worker client is not connected")
        return self._client

    @property
    def broker_store_path(self) -> Path:
        return self._broker_store_path

    def _set_health(self, state: WorkerHealthState) -> None:
        with self._state_lock:
            self._health_state = state

    def start(self) -> SocketWorkerClient:
        if self._client is not None and self._client.is_ready:
            return self._client
        if not self._breaker.allow_restart():
            self._set_health(WorkerHealthState.DEGRADED)
            self._health_gate.block("HG_WORKER_CIRCUIT_OPEN")
            self._event_sink.emit("worker_circuit_opened", reason_code="WORKER_CRASHED")
            raise RuntimeError("worker circuit breaker is open")
        self._stopping = False
        self._monitor_stop.clear()
        self._set_health(WorkerHealthState.STARTING)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(self._handshake_timeout)
        port = int(listener.getsockname()[1])
        command = [
            sys.executable,
            "-m",
            "apps.simulated_worker",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--scenario",
            self._scenario.value,
            "--protocol-version",
            str(self._worker_protocol_version),
            "--broker-store",
            str(self._broker_store_path),
        ]
        project_root = Path(__file__).resolve().parents[2]
        self._process = subprocess.Popen(
            command,
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None,
            env=without_broker_credentials(),
        )
        self._event_sink.emit(
            "worker_process_started",
            process_id=self._process.pid,
            worker_type="SIMULATED",
        )
        try:
            connection, address = listener.accept()
            if address[0] != "127.0.0.1":
                connection.close()
                raise ProtocolError(
                    ProtocolErrorCode.IPC_ROLE_MISMATCH,
                    "worker IPC peer is not loopback",
                )
            self._event_sink.emit("ipc_connected", worker_type="SIMULATED")
            self._set_health(WorkerHealthState.HANDSHAKING)
            client = SocketWorkerClient.handshake(
                FramedSocket(connection),
                timeout_seconds=self._handshake_timeout,
                response_timeout=self._response_timeout,
                event_queue_size=self._event_queue_size,
                event_sink=self._event_sink,
                on_disconnect=self._on_disconnect,
            )
        except ProtocolError as exc:
            if exc.code is ProtocolErrorCode.IPC_PROTOCOL_INCOMPATIBLE:
                self._set_health(WorkerHealthState.INCOMPATIBLE)
                self._health_gate.block("HG_WORKER_INCOMPATIBLE")
                self._event_sink.emit("ipc_protocol_incompatible", reason_code=exc.code.value)
            else:
                self._set_health(WorkerHealthState.DEGRADED)
                self._health_gate.block("HG_WORKER_DISCONNECTED")
            self._terminate_process()
            raise
        except (OSError, TimeoutError) as exc:
            self._set_health(WorkerHealthState.DISCONNECTED)
            self._health_gate.block("HG_WORKER_DISCONNECTED")
            self._terminate_process()
            raise ProtocolError(
                ProtocolErrorCode.IPC_HANDSHAKE_TIMEOUT,
                "worker did not connect before handshake deadline",
            ) from exc
        finally:
            listener.close()
        self._client = client
        self._set_health(WorkerHealthState.READY)
        self._health_gate.clear_if("HG_WORKER_DISCONNECTED")
        self._health_gate.clear_if("HG_WORKER_CIRCUIT_OPEN")
        self._breaker.record_success()
        self._event_sink.emit("worker_ready", worker_type="SIMULATED")
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="simulated-worker-monitor",
            daemon=True,
        )
        self._monitor_thread.start()
        return client

    def _on_disconnect(self, code: ProtocolErrorCode) -> None:
        if self._stopping:
            return
        self._set_health(
            WorkerHealthState.DEGRADED
            if code is ProtocolErrorCode.IPC_BACKPRESSURE
            else WorkerHealthState.DISCONNECTED
        )
        self._health_gate.block("HG_WORKER_DISCONNECTED")
        if code is ProtocolErrorCode.IPC_BACKPRESSURE:
            self._health_gate.block("HG_BROKER_EVENT_BACKPRESSURE")
            self._health_gate.block("HG_RECONCILIATION_REQUIRED")
        self._record_crash_once()
        self._event_sink.emit("worker_crashed", reason_code=code.value)

    def _record_crash_once(self) -> None:
        process = self._process
        if process is None or self._recorded_crash_pid == process.pid:
            return
        self._recorded_crash_pid = process.pid
        self._breaker.record_crash()
        if self._breaker.state is CircuitState.OPEN:
            self._event_sink.emit("worker_circuit_opened", reason_code="WORKER_CRASHED")

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(self._heartbeat_interval):
            process = self._process
            client = self._client
            if process is None or client is None:
                return
            if process.poll() is not None:
                self._on_disconnect(ProtocolErrorCode.WORKER_CRASHED)
                return
            try:
                client.ping(self._heartbeat_timeout)
            except WorkerDispatchError:
                self._on_disconnect(ProtocolErrorCode.IPC_CONNECTION_LOST)
                return

    def heartbeat(self) -> None:
        try:
            self.client.ping(self._heartbeat_timeout)
        except WorkerDispatchError:
            self._on_disconnect(ProtocolErrorCode.IPC_CONNECTION_LOST)
            raise

    def restart(self) -> SocketWorkerClient:
        if not self._breaker.allow_restart():
            self._set_health(WorkerHealthState.DEGRADED)
            self._health_gate.block("HG_WORKER_CIRCUIT_OPEN")
            raise RuntimeError("worker circuit breaker is open")
        base_delay = self._breaker.next_delay()
        delay = min(
            self._restart_policy.max_delay_seconds,
            base_delay + self._jitter(base_delay * 0.2),
        )
        self._event_sink.emit(
            "worker_restart_scheduled",
            delay_ms=int(delay * 1000),
            worker_type="SIMULATED",
        )
        self._cleanup_connection()
        self._sleeper(delay)
        return self.start()

    def shutdown(self, grace_seconds: float = 1.0) -> None:
        self._stopping = True
        self._monitor_stop.set()
        process = self._process
        client = self._client
        acknowledged = client.shutdown(grace_seconds) if client is not None else False
        self.last_shutdown_forced = False
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=grace_seconds if acknowledged else 0.05)
            except subprocess.TimeoutExpired:
                self.last_shutdown_forced = True
                process.terminate()
                try:
                    process.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=grace_seconds)
        self._cleanup_connection()
        self._set_health(WorkerHealthState.STOPPED)
        self._event_sink.emit(
            "worker_shutdown_completed",
            forced=self.last_shutdown_forced,
            worker_type="SIMULATED",
        )
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None

    def _cleanup_connection(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()
        monitor = self._monitor_thread
        self._monitor_thread = None
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=1.0)
        self._process = None

    def _terminate_process(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
        self._process = None
