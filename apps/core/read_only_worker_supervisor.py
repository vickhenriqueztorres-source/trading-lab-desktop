from __future__ import annotations

import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from apps.core.health import HealthGate
from apps.core.worker_client import SocketWorkerClient, WorkerDispatchError
from apps.core.worker_supervisor import WorkerHealthState
from packages.protocol.envelope import EndpointRole
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.transport import FramedSocket


@dataclass(frozen=True, slots=True)
class ReadOnlyWorkerSpec:
    module: str
    role: EndpointRole
    broker: str
    extra_arguments: tuple[str, ...] = ()
    allow_demo_financial_submission: bool = False
    allow_real_financial_submission: bool = False

    def __post_init__(self) -> None:
        if not self.module or not self.broker:
            raise ValueError("worker module and broker are required")
        if self.role is EndpointRole.CORE:
            raise ValueError("read-only worker cannot use the Core role")
        if self.allow_demo_financial_submission and self.allow_real_financial_submission:
            raise ValueError("worker cannot be both Demo and Real")


class ReadOnlyWorkerSupervisor:
    """Broker-neutral subprocess lifecycle for capability-gated data workers."""

    _HEALTH_ACCOUNT = "market-data"

    def __init__(
        self,
        health_gate: HealthGate,
        spec: ReadOnlyWorkerSpec,
        *,
        worker_protocol_version: int = 1,
        handshake_timeout: float = 5.0,
        response_timeout: float = 2.0,
        heartbeat_interval: float = 0.5,
        heartbeat_timeout: float = 1.0,
        event_queue_size: int = 128,
    ) -> None:
        self._health_gate = health_gate
        self._spec = spec
        self._worker_protocol_version = worker_protocol_version
        self._handshake_timeout = handshake_timeout
        self._response_timeout = response_timeout
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._event_queue_size = event_queue_size
        self._health_lock = threading.Lock()
        self._health_state = WorkerHealthState.STOPPED
        self._process: subprocess.Popen[bytes] | None = None
        self._client: SocketWorkerClient | None = None
        self._monitor_stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._stopping = False

    @property
    def health_state(self) -> WorkerHealthState:
        with self._health_lock:
            return self._health_state

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    @property
    def client(self) -> SocketWorkerClient:
        if self._client is None:
            raise RuntimeError("read-only worker is not connected")
        return self._client

    def start(self) -> SocketWorkerClient:
        if self._client is not None and self._client.is_ready:
            return self._client
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
            self._spec.module,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--protocol-version",
            str(self._worker_protocol_version),
            *self._spec.extra_arguments,
        ]
        project_root = Path(__file__).resolve().parents[2]
        self._process = subprocess.Popen(
            command,
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            connection, address = listener.accept()
            if address[0] != "127.0.0.1":
                connection.close()
                raise ProtocolError(
                    ProtocolErrorCode.IPC_ROLE_MISMATCH,
                    "worker IPC peer is not loopback",
                )
            self._set_health(WorkerHealthState.HANDSHAKING)
            client = SocketWorkerClient.handshake(
                FramedSocket(connection),
                timeout_seconds=self._handshake_timeout,
                response_timeout=self._response_timeout,
                event_queue_size=self._event_queue_size,
                on_disconnect=self._on_disconnect,
                expected_worker_role=self._spec.role,
                expected_broker=self._spec.broker,
            )
        except ProtocolError as exc:
            state = (
                WorkerHealthState.INCOMPATIBLE
                if exc.code is ProtocolErrorCode.IPC_PROTOCOL_INCOMPATIBLE
                else WorkerHealthState.DISCONNECTED
            )
            self._set_health(state)
            self._block_health(
                "HG_MARKET_DATA_INCOMPATIBLE"
                if state is WorkerHealthState.INCOMPATIBLE
                else "HG_MARKET_DATA_DISCONNECTED"
            )
            self._terminate_process()
            raise
        except (OSError, TimeoutError) as exc:
            self._set_health(WorkerHealthState.DISCONNECTED)
            self._block_health("HG_MARKET_DATA_DISCONNECTED")
            self._terminate_process()
            raise ProtocolError(
                ProtocolErrorCode.IPC_HANDSHAKE_TIMEOUT,
                "read-only worker did not connect before deadline",
            ) from exc
        finally:
            listener.close()
        financial_mode = (
            "DEMO"
            if self._spec.allow_demo_financial_submission
            else "REAL"
            if self._spec.allow_real_financial_submission
            else None
        )
        if client.capabilities.can_submit_orders and financial_mode is None:
            client.close()
            self._terminate_process()
            self._set_health(WorkerHealthState.INCOMPATIBLE)
            self._block_health("HG_MARKET_DATA_INCOMPATIBLE")
            raise ProtocolError(
                ProtocolErrorCode.IPC_PROTOCOL_INCOMPATIBLE,
                "read-only worker advertised financial submission",
            )
        if financial_mode is not None and not (
            client.capabilities.can_submit_orders
            and client.capabilities.supports_order_status_query
            and client.capabilities.supports_reconciliation
            and client.capabilities.supports_order_events
            and client.capabilities.connection_mode == financial_mode
        ):
            client.close()
            self._terminate_process()
            self._set_health(WorkerHealthState.INCOMPATIBLE)
            self._block_health("HG_MARKET_DATA_INCOMPATIBLE")
            raise ProtocolError(
                ProtocolErrorCode.IPC_PROTOCOL_INCOMPATIBLE,
                "authenticated financial worker capabilities are incomplete",
            )
        self._client = client
        self._set_health(WorkerHealthState.READY)
        self._clear_health("HG_MARKET_DATA_DISCONNECTED")
        self._clear_health("HG_MARKET_DATA_INCOMPATIBLE")
        self._monitor = threading.Thread(
            target=self._monitor_loop,
            name="read-only-worker-monitor",
            daemon=True,
        )
        self._monitor.start()
        return client

    def restart(self) -> SocketWorkerClient:
        self._stopping = True
        self._monitor_stop.set()
        self._terminate_process()
        self._cleanup_connection()
        return self.start()

    def shutdown(self, grace_seconds: float = 1.0) -> None:
        self._stopping = True
        self._monitor_stop.set()
        client = self._client
        process = self._process
        acknowledged = client.shutdown(grace_seconds) if client is not None else False
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=grace_seconds if acknowledged else 0.05)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=grace_seconds)
        self._cleanup_connection()
        self._set_health(WorkerHealthState.STOPPED)

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

    def _on_disconnect(self, _code: ProtocolErrorCode) -> None:
        if self._stopping:
            return
        self._set_health(WorkerHealthState.DISCONNECTED)
        self._block_health("HG_MARKET_DATA_DISCONNECTED")

    def _block_health(self, reason: str) -> None:
        self._health_gate.block_scope(self._spec.broker, self._HEALTH_ACCOUNT, reason)

    def _clear_health(self, reason: str) -> None:
        self._health_gate.clear_scope(self._spec.broker, self._HEALTH_ACCOUNT, reason)

    def _set_health(self, state: WorkerHealthState) -> None:
        with self._health_lock:
            self._health_state = state

    def _cleanup_connection(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()
        monitor = self._monitor
        self._monitor = None
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
