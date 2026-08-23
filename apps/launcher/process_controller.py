from __future__ import annotations

import json
import queue
import secrets
import subprocess
import sys
import threading
from pathlib import Path
from typing import Protocol

from apps.launcher.core_client import CoreLifecycleClient, CoreLifecycleIpcError
from apps.launcher.windows_job import ProcessContainment, create_process_containment
from packages.protocol import (
    CoreDrainResponse,
    CoreLifecycleStatusResponse,
    CoreRestartComponentResponse,
    LifecycleProcessStatus,
)
from packages.security import SecretValue, without_broker_credentials


class ManagedCoreController(Protocol):
    @property
    def process(self) -> subprocess.Popen[str] | None: ...

    @property
    def ui_process(self) -> subprocess.Popen[str] | None: ...

    def start(self) -> CoreLifecycleStatusResponse: ...

    def status(self) -> CoreLifecycleStatusResponse: ...

    def safe_stop(self) -> bool: ...

    def drain(self, timeout: float) -> CoreDrainResponse: ...

    def shutdown_workers(self, timeout: float) -> bool: ...

    def shutdown_auth(self, timeout: float) -> bool: ...

    def restart_component(self, role: str) -> CoreRestartComponentResponse: ...

    def shutdown_core(self, timeout: float) -> bool: ...

    def wait(self, timeout: float) -> bool: ...

    def terminate(self, timeout: float) -> None: ...

    def terminate_tree(self) -> None: ...

    def close(self) -> None: ...


class SubprocessCoreController:
    """Starts only the Core host; descendants remain Core-owned inside one Job Object."""

    def __init__(
        self,
        profile_dir: Path,
        workers: tuple[str, ...],
        *,
        startup_timeout: float = 12.0,
        request_timeout: float = 3.0,
        force_auth_simulation: bool = False,
        containment: ProcessContainment | None = None,
        ui_headless: bool = True,
        deriv_transport: str = "fake-public",
    ) -> None:
        if min(startup_timeout, request_timeout) <= 0:
            raise ValueError("controller timeouts must be positive")
        self._profile_dir = Path(profile_dir)
        self._workers = workers
        self._startup_timeout = startup_timeout
        self._request_timeout = request_timeout
        self._force_auth_simulation = force_auth_simulation
        self._containment = containment or create_process_containment()
        self._ui_headless = ui_headless
        self._deriv_transport = deriv_transport
        self._process: subprocess.Popen[str] | None = None
        self._ui_process: subprocess.Popen[str] | None = None
        self._client: CoreLifecycleClient | None = None
        self._session_token: SecretValue | None = None

    @property
    def process(self) -> subprocess.Popen[str] | None:
        return self._process

    @property
    def ui_process(self) -> subprocess.Popen[str] | None:
        return self._ui_process

    def start(self) -> CoreLifecycleStatusResponse:
        if self._client is not None and self._client.is_ready:
            return self._with_ui(self._client.status())
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        token = SecretValue.from_text(secrets.token_hex(32))
        ui_token = SecretValue.from_text(secrets.token_hex(32))
        command = [sys.executable, "-m", "apps.core.runner"]
        project_root = Path(__file__).resolve().parents[2]
        process = subprocess.Popen(
            command,
            cwd=project_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        self._process = process
        self._session_token = token
        try:
            self._containment.assign(process.pid)
            startup = {
                "force_auth_simulation": self._force_auth_simulation,
                "deriv_transport": self._deriv_transport,
                "profile_dir": str(self._profile_dir),
                "session_token": token.reveal_text(),
                "ui_session_token": ui_token.reveal_text(),
                "workers": list(self._workers),
            }
            if process.stdin is None:
                raise RuntimeError("CORE_STDIN_UNAVAILABLE")
            process.stdin.write(json.dumps(startup, separators=(",", ":")) + "\n")
            process.stdin.flush()
            process.stdin.close()
            port, ui_port = self._read_ports(process)
            client = CoreLifecycleClient.connect(
                port,
                token,
                connect_timeout=self._startup_timeout,
                request_timeout=self._request_timeout,
            )
            self._client = client
            status = client.status()
            if status.core_state != "READY":
                raise RuntimeError("CORE_NOT_HEALTHY_FOR_UI")
            self._start_ui(ui_port, ui_token)
            return self._with_ui(status)
        except (OSError, RuntimeError, ValueError, CoreLifecycleIpcError):
            self.terminate(0.5)
            raise RuntimeError("CORE_PROCESS_START_FAILED") from None

    def status(self) -> CoreLifecycleStatusResponse:
        return self._with_ui(self._require_client().status())

    def safe_stop(self) -> bool:
        return self._require_client().safe_stop()

    def drain(self, timeout: float) -> CoreDrainResponse:
        return self._require_client().drain(timeout)

    def shutdown_workers(self, timeout: float) -> bool:
        return self._require_client().shutdown_workers(timeout)

    def shutdown_auth(self, timeout: float) -> bool:
        return self._require_client().shutdown_auth(timeout)

    def restart_component(self, role: str) -> CoreRestartComponentResponse:
        return self._require_client().restart_component(role)

    def shutdown_core(self, timeout: float) -> bool:
        return self._require_client().shutdown_core(timeout)

    def wait(self, timeout: float) -> bool:
        if timeout <= 0:
            raise ValueError("wait timeout must be positive")
        process = self._process
        if process is None or process.poll() is not None:
            return True
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    def terminate(self, timeout: float) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=max(0.05, timeout))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=max(0.05, timeout))

    def terminate_tree(self) -> None:
        try:
            self._containment.terminate_tree()
        except OSError:
            self.terminate(0.5)

    def close(self) -> None:
        self._stop_ui(0.5)
        client = self._client
        self._client = None
        self._session_token = None
        if client is not None:
            client.close()
        process = self._process
        self._process = None
        if process is not None and process.stdout is not None:
            process.stdout.close()
        self._containment.close()

    def _read_ports(self, process: subprocess.Popen[str]) -> tuple[int, int]:
        output: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_line() -> None:
            output.put("" if process.stdout is None else process.stdout.readline())

        reader = threading.Thread(target=read_line, name="core-startup-reader", daemon=True)
        reader.start()
        try:
            line = output.get(timeout=self._startup_timeout)
            document = json.loads(line)
        except (queue.Empty, json.JSONDecodeError) as exc:
            raise RuntimeError("CORE_STARTUP_TIMEOUT") from exc
        if (
            not isinstance(document, dict)
            or set(document) != {"port", "ui_port"}
            or type(document["port"]) is not int
            or type(document["ui_port"]) is not int
            or not 0 < document["port"] <= 65535
            or not 0 < document["ui_port"] <= 65535
        ):
            raise RuntimeError("CORE_STARTUP_INVALID")
        return int(document["port"]), int(document["ui_port"])

    def _start_ui(self, port: int, token: SecretValue) -> None:
        project_root = Path(__file__).resolve().parents[2]
        process = subprocess.Popen(
            [sys.executable, "-m", "apps.ui"],
            cwd=project_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=without_broker_credentials(),
        )
        self._ui_process = process
        try:
            self._containment.assign(process.pid)
            if process.stdin is None:
                raise RuntimeError("UI_STDIN_UNAVAILABLE")
            startup = {
                "headless": self._ui_headless,
                "port": port,
                "session_token": token.reveal_text(),
            }
            process.stdin.write(json.dumps(startup, separators=(",", ":")) + "\n")
            process.stdin.flush()
            process.stdin.close()
            line = self._read_line(process, "ui-startup-reader")
            document = json.loads(line)
            if document != {"status": "ready"}:
                raise RuntimeError("UI_STARTUP_INVALID")
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            self._stop_ui(0.5)
            raise RuntimeError("UI_PROCESS_START_FAILED") from None

    def _stop_ui(self, timeout: float) -> None:
        process = self._ui_process
        self._ui_process = None
        if process is None:
            return
        if process.poll() is None:
            try:
                process.wait(timeout=max(0.05, timeout))
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=max(0.05, timeout))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=max(0.05, timeout))
        if process.stdout is not None:
            process.stdout.close()

    def _with_ui(self, status: CoreLifecycleStatusResponse) -> CoreLifecycleStatusResponse:
        process = self._ui_process
        if process is None:
            ui_status = LifecycleProcessStatus("UI", None, False, None, "STOPPED", 0)
        else:
            exit_code = process.poll()
            ui_status = LifecycleProcessStatus(
                "UI",
                process.pid,
                exit_code is None,
                exit_code,
                "READY" if exit_code is None else "FAILED",
                0,
            )
        processes = tuple(item for item in status.processes if item.role != "UI") + (ui_status,)
        return CoreLifecycleStatusResponse(
            status.core_state,
            status.safe_stop_active,
            processes,
            status.ui_shutdown_requested,
        )

    def _read_line(self, process: subprocess.Popen[str], thread_name: str) -> str:
        output: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_line() -> None:
            output.put("" if process.stdout is None else process.stdout.readline())

        reader = threading.Thread(target=read_line, name=thread_name, daemon=True)
        reader.start()
        try:
            return output.get(timeout=self._startup_timeout)
        except queue.Empty as exc:
            raise RuntimeError("PROCESS_STARTUP_TIMEOUT") from exc

    def _require_client(self) -> CoreLifecycleClient:
        if self._client is None or not self._client.is_ready:
            raise CoreLifecycleIpcError("LIFECYCLE_IPC_UNAVAILABLE")
        return self._client
