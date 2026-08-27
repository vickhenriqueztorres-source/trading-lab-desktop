from __future__ import annotations

import json
import queue
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from apps.core.auth_client import AuthAgentIpcClient, AuthIpcError
from packages.identity import OtpCode
from packages.licensing import AuthorizationDecision, AuthorizationReason
from packages.observability.events import EventSink, NullEventSink
from packages.protocol import (
    AuthCheckAuthorizationResponse,
    AuthMode,
    AuthStartLoginResponse,
    AuthStatusResponse,
    AuthSubmitOtpResponse,
)
from packages.security import SecretValue, without_broker_credentials


class AuthAgentHealthState(StrEnum):
    STARTING = "STARTING"
    HANDSHAKING = "HANDSHAKING"
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class AuthRestartPolicy:
    max_restarts: int = 3
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.max_restarts <= 0 or min(self.base_delay_seconds, self.max_delay_seconds) <= 0:
            raise ValueError("auth restart policy must be positive")


class AuthAgentSupervisor:
    """Owns the Auth Agent process and exposes only reduced authorization to the Core."""

    def __init__(
        self,
        profile_dir: Path,
        *,
        force_simulation: bool = False,
        allow_real_mode: bool = False,
        enable_test_otp: bool = False,
        test_lease_ttl_seconds: float | None = None,
        startup_timeout: float = 5.0,
        request_timeout: float = 1.0,
        heartbeat_interval: float = 0.25,
        restart_policy: AuthRestartPolicy | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        if min(startup_timeout, request_timeout, heartbeat_interval) <= 0:
            raise ValueError("auth supervisor timeouts must be positive")
        self._profile_dir = Path(profile_dir)
        self._force_simulation = force_simulation
        self._allow_real_mode = allow_real_mode
        self._enable_test_otp = enable_test_otp
        if test_lease_ttl_seconds is not None and (
            not enable_test_otp or not 0 < test_lease_ttl_seconds <= 7 * 24 * 60 * 60
        ):
            raise ValueError("test lease TTL requires the test OTP channel")
        self._lease_ttl_seconds = (
            float((24 if allow_real_mode else 7 * 24) * 60 * 60)
            if test_lease_ttl_seconds is None
            else test_lease_ttl_seconds
        )
        self._startup_timeout = startup_timeout
        self._request_timeout = request_timeout
        self._heartbeat_interval = heartbeat_interval
        self._restart_policy = restart_policy or AuthRestartPolicy()
        self._events = event_sink or NullEventSink()
        self._lock = threading.RLock()
        self._state = AuthAgentHealthState.STOPPED
        self._process: subprocess.Popen[str] | None = None
        self._client: AuthAgentIpcClient | None = None
        self._session_token: SecretValue | None = None
        self._test_otp: OtpCode | None = None
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._stopping = False
        self._restart_count = 0

    @property
    def health_state(self) -> AuthAgentHealthState:
        with self._lock:
            return self._state

    @property
    def process(self) -> subprocess.Popen[str] | None:
        with self._lock:
            return self._process

    @property
    def client(self) -> AuthAgentIpcClient:
        with self._lock:
            if self._client is None or not self._client.is_ready:
                raise RuntimeError("auth agent client is not connected")
            return self._client

    def start(self) -> AuthAgentIpcClient:
        with self._lock:
            if self._client is not None and self._client.is_ready:
                return self._client
            self._stopping = False
            self._monitor_stop.clear()
            self._state = AuthAgentHealthState.STARTING
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        session_token = SecretValue.from_text(secrets.token_hex(32))
        test_otp = OtpCode(f"{secrets.randbelow(1_000_000):06d}") if self._enable_test_otp else None
        command = [sys.executable, "-m", "apps.auth_agent.runner"]
        project_root = Path(__file__).resolve().parents[2]
        process = subprocess.Popen(
            command,
            cwd=project_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=without_broker_credentials(),
        )
        with self._lock:
            self._process = process
            self._session_token = session_token
            self._test_otp = test_otp
            startup = {
                "allow_real_mode": self._allow_real_mode,
                "force_simulation": self._force_simulation,
                "lease_ttl_seconds": self._lease_ttl_seconds,
                "profile_dir": str(self._profile_dir),
                "session_token": session_token.reveal_text(),
                "test_otp": None if test_otp is None else test_otp.value,
            }
        try:
            if process.stdin is None:
                raise RuntimeError("auth agent stdin is unavailable")
            process.stdin.write(json.dumps(startup, separators=(",", ":")) + "\n")
            process.stdin.flush()
            process.stdin.close()
            port = self._read_startup_port(process)
            with self._lock:
                self._state = AuthAgentHealthState.HANDSHAKING
            client = AuthAgentIpcClient.connect(
                "127.0.0.1",
                port,
                session_token,
                connect_timeout=self._startup_timeout,
                request_timeout=self._request_timeout,
            )
        except (OSError, RuntimeError, AuthIpcError, ValueError):
            self._terminate_process(process)
            with self._lock:
                self._state = AuthAgentHealthState.UNAVAILABLE
                self._process = None
            self._events.emit(
                "auth_agent_start_failed",
                reason_code=AuthorizationReason.AUTH_AGENT_UNAVAILABLE.value,
            )
            raise RuntimeError("AUTH_AGENT_START_FAILED") from None
        with self._lock:
            self._client = client
            self._state = AuthAgentHealthState.READY
        self._events.emit("auth_agent_ready")
        self._start_monitor()
        return client

    def take_test_otp(self) -> OtpCode:
        if not self._enable_test_otp:
            raise RuntimeError("test OTP channel is disabled")
        with self._lock:
            value = self._test_otp
            self._test_otp = None
        if value is None:
            raise RuntimeError("test OTP is unavailable")
        return value

    def start_login(self, email: str) -> AuthStartLoginResponse:
        return self.client.start_login(email)

    def submit_otp(self, challenge_id: str, code: OtpCode) -> AuthSubmitOtpResponse:
        return self.client.submit_otp(challenge_id, code)

    def status(self) -> AuthStatusResponse:
        return self.client.status()

    def renew(self) -> AuthCheckAuthorizationResponse:
        return self.client.renew()

    def authorization(
        self, broker: str, strategy_pack: str, *, real_mode: bool = False
    ) -> AuthorizationDecision:
        with self._lock:
            client = self._client
            ready = self._state is AuthAgentHealthState.READY
        if client is None or not ready:
            return self._unavailable_decision()
        try:
            response = client.check_authorization(
                broker,
                strategy_pack,
                mode=AuthMode.REAL if real_mode else AuthMode.PRACTICE,
            )
            reason = AuthorizationReason(response.reason_code)
        except (AuthIpcError, ValueError):
            self._mark_unavailable()
            return self._unavailable_decision()
        if response.allowed != (reason is AuthorizationReason.AUTHORIZED):
            self._mark_unavailable()
            return self._unavailable_decision()
        return AuthorizationDecision(
            new_entries_allowed=response.allowed,
            open_order_follow_up_allowed=True,
            reconciliation_allowed=True,
            reason=reason,
            lease_id=None,
            expires_at=response.lease_expires_at_utc,
        )

    def restart(self) -> AuthAgentIpcClient:
        with self._lock:
            if self._restart_count >= self._restart_policy.max_restarts:
                self._state = AuthAgentHealthState.UNAVAILABLE
                raise RuntimeError("AUTH_AGENT_RESTART_LIMIT_EXCEEDED")
            self._restart_count += 1
            attempt = self._restart_count
        delay = min(
            self._restart_policy.base_delay_seconds * float(2 ** (attempt - 1)),
            self._restart_policy.max_delay_seconds,
        )
        self._events.emit("auth_agent_restart_scheduled", delay_ms=int(delay * 1000))
        self._stop_monitor()
        self._cleanup_connection(terminate=True)
        time.sleep(delay)
        return self.start()

    def shutdown(self, grace_seconds: float = 1.0) -> None:
        self._stopping = True
        self._stop_monitor()
        with self._lock:
            client = self._client
            process = self._process
        acknowledged = client.shutdown(grace_seconds) if client is not None else False
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=grace_seconds if acknowledged else 0.05)
            except subprocess.TimeoutExpired:
                self._terminate_process(process)
        self._cleanup_connection(terminate=False)
        with self._lock:
            self._state = AuthAgentHealthState.STOPPED
            self._restart_count = 0
        self._events.emit("auth_agent_stopped")

    def wait_for_state(self, state: AuthAgentHealthState, timeout: float = 2.0) -> bool:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.health_state is state:
                return True
            time.sleep(0.01)
        return self.health_state is state

    def _read_startup_port(self, process: subprocess.Popen[str]) -> int:
        output: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_line() -> None:
            if process.stdout is None:
                output.put("")
            else:
                output.put(process.stdout.readline())

        thread = threading.Thread(target=read_line, name="auth-agent-startup-reader", daemon=True)
        thread.start()
        try:
            line = output.get(timeout=self._startup_timeout)
            document = json.loads(line)
        except (queue.Empty, json.JSONDecodeError) as exc:
            raise RuntimeError("AUTH_AGENT_STARTUP_TIMEOUT") from exc
        if (
            not isinstance(document, dict)
            or set(document) != {"port"}
            or type(document["port"]) is not int
            or not 0 < document["port"] <= 65535
        ):
            raise RuntimeError("AUTH_AGENT_STARTUP_INVALID")
        return int(document["port"])

    def _start_monitor(self) -> None:
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="auth-agent-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(self._heartbeat_interval):
            with self._lock:
                process = self._process
                client = self._client
            if process is None or client is None or process.poll() is not None:
                self._mark_unavailable()
                return
            try:
                client.status()
            except AuthIpcError:
                self._mark_unavailable()
                return

    def _mark_unavailable(self) -> None:
        if self._stopping:
            return
        with self._lock:
            client = self._client
            self._client = None
            self._state = AuthAgentHealthState.UNAVAILABLE
        if client is not None:
            client.close()
        self._events.emit(
            "auth_agent_unavailable",
            reason_code=AuthorizationReason.AUTH_AGENT_UNAVAILABLE.value,
        )

    def _stop_monitor(self) -> None:
        self._monitor_stop.set()
        monitor = self._monitor_thread
        self._monitor_thread = None
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=1.0)
        self._monitor_stop.clear()

    def _cleanup_connection(self, *, terminate: bool) -> None:
        with self._lock:
            client = self._client
            process = self._process
            self._client = None
            self._process = None
            self._session_token = None
        if client is not None:
            client.close()
        if terminate and process is not None:
            self._terminate_process(process)
        elif process is not None and process.stdout is not None:
            process.stdout.close()

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
        if process.stdout is not None:
            process.stdout.close()

    @staticmethod
    def _unavailable_decision() -> AuthorizationDecision:
        return AuthorizationDecision(
            new_entries_allowed=False,
            open_order_follow_up_allowed=True,
            reconciliation_allowed=True,
            reason=AuthorizationReason.AUTH_AGENT_UNAVAILABLE,
            lease_id=None,
            expires_at=None,
        )
