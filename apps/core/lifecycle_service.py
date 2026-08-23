from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

from apps.auth_agent.core_gate import CoreLeaseEntryAuthorizer
from apps.core.auth_supervisor import AuthAgentSupervisor
from apps.core.deriv_telemetry import (
    DerivTelemetryMonitor,
    DerivTelemetrySource,
)
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSpec, ReadOnlyWorkerSupervisor
from apps.core.runtime import CoreRuntime
from apps.core.ui_service import CoreUiProjectionBuilder, CoreUiProjectionService
from apps.core.worker_supervisor import WorkerHealthState
from packages.protocol import EndpointRole, LifecycleProcessStatus
from packages.security import SecretValue


class CoreServiceState(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    SAFE_STOP = "SAFE_STOP"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class CoreLifecycleService:
    """Core-owned composition used by the Launcher; no financial state enters the Launcher."""

    def __init__(
        self,
        profile_dir: Path,
        workers: tuple[str, ...],
        *,
        force_auth_simulation: bool = False,
        ui_session_token: SecretValue | None = None,
        deriv_transport: str = "fake-public",
    ) -> None:
        if "simulated" not in workers:
            raise ValueError("the Phase 1 Core requires the simulated financial worker")
        if len(workers) != len(set(workers)) or not set(workers) <= {
            "simulated",
            "deriv_read_only",
            "iqoption",
        }:
            raise ValueError("worker selection is invalid")
        if deriv_transport not in {
            "fake-public",
            "fake-demo",
            "live-public",
            "live-demo",
        }:
            raise ValueError("Deriv transport selection is invalid")
        self._profile_dir = Path(profile_dir)
        self._workers = workers
        self._auth = AuthAgentSupervisor(
            self._profile_dir / "auth",
            force_simulation=force_auth_simulation,
        )
        self._runtime: CoreRuntime | None = None
        self._deriv: ReadOnlyWorkerSupervisor | None = None
        self._deriv_transport = deriv_transport
        self._deriv_telemetry: DerivTelemetryMonitor | None = None
        self._ui_session_token = ui_session_token
        self._ui_service: CoreUiProjectionService | None = None
        self._ui_shutdown_requested = False
        self._state = CoreServiceState.STARTING
        self._safe_stop = False
        self._restart_counts = {"AUTH_AGENT": 0, "DERIV_WORKER": 0}
        self._workers_stopped = False
        self._auth_stopped = False
        self._startup_sequence: list[str] = []

    @property
    def state(self) -> CoreServiceState:
        return self._state

    @property
    def safe_stop_active(self) -> bool:
        return self._safe_stop

    @property
    def ui_port(self) -> int:
        if self._ui_service is None:
            raise RuntimeError("CORE_UI_SERVICE_UNAVAILABLE")
        return self._ui_service.port

    @property
    def ui_shutdown_requested(self) -> bool:
        return self._ui_shutdown_requested

    @property
    def startup_sequence(self) -> tuple[str, ...]:
        return tuple(self._startup_sequence)

    def start(self) -> None:
        if self._state is CoreServiceState.READY:
            return
        self._state = CoreServiceState.STARTING
        try:
            self._auth.start()
            self._startup_sequence.append("AUTH_AGENT")
            runtime = CoreRuntime(
                self._profile_dir / "core",
                entry_authorizer_factory=lambda gate: CoreLeaseEntryAuthorizer(self._auth, gate),
            )
            self._startup_sequence.append("CORE")
            runtime.start()
            self._runtime = runtime
            self._startup_sequence.append("SIMULATED_WORKER")
            if "deriv_read_only" in self._workers:
                deriv = ReadOnlyWorkerSupervisor(
                    runtime.health_gate,
                    self._deriv_spec(),
                )
                deriv.start()
                self._deriv = deriv
                self._start_deriv_telemetry(runtime, deriv)
                self._startup_sequence.append("DERIV_WORKER")
            if self._ui_session_token is not None:
                projection = CoreUiProjectionBuilder(
                    runtime,
                    deriv_health=lambda: None if self._deriv is None else self._deriv.health_state,
                    deriv_telemetry=lambda: (
                        None if self._deriv_telemetry is None else self._deriv_telemetry.snapshot
                    ),
                )
                ui_service = CoreUiProjectionService(
                    self._ui_session_token,
                    projection.snapshot,
                    self.safe_stop,
                    self.resume,
                    self._request_ui_shutdown,
                )
                ui_service.start()
                self._ui_service = ui_service
        except Exception:
            self._state = CoreServiceState.FAILED
            self.emergency_shutdown()
            raise
        self._state = CoreServiceState.READY

    def safe_stop(self) -> None:
        runtime = self._require_runtime()
        runtime.stop_new_entries()
        self._safe_stop = True
        self._state = CoreServiceState.SAFE_STOP

    def resume(self) -> bool:
        runtime = self._require_runtime()
        accepted = runtime.resume_new_entries()
        self._safe_stop = False
        self._state = CoreServiceState.READY if accepted else CoreServiceState.DEGRADED
        return accepted

    def _request_ui_shutdown(self) -> None:
        self._ui_shutdown_requested = True

    def drain(self, timeout: float) -> tuple[bool, int]:
        runtime = self._require_runtime()
        drained = runtime.drain_financial_events(timeout)
        return drained, runtime.pending_financial_event_count

    def shutdown_workers(self, grace_seconds: float) -> bool:
        if self._workers_stopped:
            return True
        self._state = CoreServiceState.STOPPING
        telemetry = self._deriv_telemetry
        self._deriv_telemetry = None
        if telemetry is not None:
            telemetry.stop()
        if self._deriv is not None:
            self._deriv.shutdown(grace_seconds)
            self._deriv = None
        runtime = self._require_runtime()
        drained = runtime.shutdown_workers(grace_seconds)
        self._workers_stopped = True
        return drained

    def shutdown_auth(self, grace_seconds: float) -> None:
        if self._auth_stopped:
            return
        self._auth.shutdown(grace_seconds)
        self._auth_stopped = True

    def shutdown_core(self) -> None:
        if self._state is CoreServiceState.STOPPED:
            return
        self._state = CoreServiceState.STOPPING
        ui_service = self._ui_service
        self._ui_service = None
        if ui_service is not None:
            ui_service.stop()
        runtime = self._runtime
        if runtime is not None:
            runtime.shutdown()
            self._runtime = None
        self._state = CoreServiceState.STOPPED

    def emergency_shutdown(self) -> None:
        self._safe_stop = True
        try:
            if self._runtime is not None:
                self._runtime.stop_new_entries()
                self.shutdown_workers(0.5)
        finally:
            try:
                self.shutdown_auth(0.5)
            finally:
                self.shutdown_core()

    def restart_component(self, role: str) -> tuple[bool, str]:
        if self._state in {CoreServiceState.STOPPING, CoreServiceState.STOPPED}:
            return False, "LIFECYCLE_STOPPING"
        if role == "AUTH_AGENT":
            self._auth.restart()
            self._restart_counts[role] += 1
            return True, "RESTART_COMPLETED"
        if role == "DERIV_WORKER" and "deriv_read_only" in self._workers:
            runtime = self._require_runtime()
            telemetry = self._deriv_telemetry
            self._deriv_telemetry = None
            if telemetry is not None:
                telemetry.stop()
            if self._deriv is None:
                self._deriv = ReadOnlyWorkerSupervisor(
                    runtime.health_gate,
                    self._deriv_spec(),
                )
                self._deriv.start()
            else:
                self._deriv.restart()
            self._start_deriv_telemetry(runtime, self._deriv)
            self._restart_counts[role] += 1
            return True, "RESTART_COMPLETED"
        return False, "RESTART_NOT_PERMITTED"

    def process_statuses(self) -> tuple[LifecycleProcessStatus, ...]:
        runtime = self._runtime
        auth_process = self._auth.process
        simulated = None if runtime is None else runtime.worker_supervisor
        simulated_process = None if simulated is None else simulated.process
        deriv_process = None if self._deriv is None else self._deriv.process
        statuses: list[LifecycleProcessStatus] = [
            self._status(
                "AUTH_AGENT",
                auth_process,
                self._auth.health_state.value,
                self._restart_counts["AUTH_AGENT"],
            ),
            LifecycleProcessStatus(
                role="CORE",
                pid=os.getpid(),
                is_alive=True,
                exit_code=None,
                state=self._state.value,
                restarts_count=0,
            ),
            self._status(
                "SIMULATED_WORKER",
                simulated_process,
                (
                    WorkerHealthState.STOPPED.value
                    if simulated is None
                    else simulated.health_state.value
                ),
                0,
            ),
        ]
        if "deriv_read_only" in self._workers or self._deriv is not None:
            statuses.append(
                self._status(
                    "DERIV_WORKER",
                    deriv_process,
                    WorkerHealthState.STOPPED.value
                    if self._deriv is None
                    else self._deriv.health_state.value,
                    self._restart_counts["DERIV_WORKER"],
                )
            )
        if "iqoption" in self._workers:
            statuses.append(
                self._status(
                    "IQOPTION_WORKER",
                    None,
                    WorkerHealthState.STOPPED.value,
                    0,
                )
            )
        return tuple(statuses)

    @staticmethod
    def _status(
        role: str,
        process: object | None,
        state: str,
        restarts: int,
    ) -> LifecycleProcessStatus:
        if process is None:
            return LifecycleProcessStatus(role, None, False, None, state, restarts)
        pid = getattr(process, "pid", None)
        poll = getattr(process, "poll", None)
        if type(pid) is not int or not callable(poll):
            raise RuntimeError("managed process handle is invalid")
        exit_code = poll()
        return LifecycleProcessStatus(role, pid, exit_code is None, exit_code, state, restarts)

    def _require_runtime(self) -> CoreRuntime:
        if self._runtime is None:
            raise RuntimeError("CORE_RUNTIME_UNAVAILABLE")
        return self._runtime

    def _deriv_spec(self) -> ReadOnlyWorkerSpec:
        return ReadOnlyWorkerSpec(
            module="apps.deriv_worker",
            role=EndpointRole.DERIV_WORKER,
            broker="DERIV",
            extra_arguments=("--deriv-transport", self._deriv_transport),
        )

    def _start_deriv_telemetry(
        self,
        runtime: CoreRuntime,
        supervisor: ReadOnlyWorkerSupervisor,
    ) -> None:
        source = {
            "fake-public": DerivTelemetrySource.FAKE_SIMULATED,
            "fake-demo": DerivTelemetrySource.FAKE_SIMULATED,
            "live-public": DerivTelemetrySource.PUBLIC_LIVE,
            "live-demo": DerivTelemetrySource.DEMO_LIVE,
        }[self._deriv_transport]
        monitor = DerivTelemetryMonitor(supervisor, runtime.health_gate, source)
        monitor.start()
        self._deriv_telemetry = monitor
