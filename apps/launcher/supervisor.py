from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from apps.launcher.core_client import CoreLifecycleIpcError
from apps.launcher.instance import LauncherInstanceGuard
from apps.launcher.models import (
    LauncherLifecycleState,
    LauncherSnapshot,
    ManagedProcessRole,
    ProcessStatusSnapshot,
)
from apps.launcher.process_controller import ManagedCoreController, SubprocessCoreController
from packages.protocol import CoreLifecycleStatusResponse, LifecycleProcessStatus
from packages.security import ReleaseIntegrityVerifier, ReleaseIntegrityViolationError

ControllerFactory = Callable[[Path, tuple[str, ...]], ManagedCoreController]
GuardFactory = Callable[[Path], LauncherInstanceGuard]


@dataclass(frozen=True, slots=True)
class LauncherRestartPolicy:
    max_restarts_per_component: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.max_restarts_per_component <= 10:
            raise ValueError("launcher restart limit is outside bounds")


class ProcessTreeSupervisor:
    """Owns process containment and ordering, never financial state or broker credentials."""

    def __init__(
        self,
        profile_dir: Path,
        *,
        workers: tuple[str, ...] = ("simulated", "deriv_read_only"),
        restart_policy: LauncherRestartPolicy | None = None,
        controller_factory: ControllerFactory | None = None,
        guard_factory: GuardFactory = LauncherInstanceGuard,
        monotonic: Callable[[], float] = time.monotonic,
        force_auth_simulation: bool = False,
        ui_headless: bool = True,
        deriv_transport: str = "fake-public",
        manifest_path: Path | None = None,
        distribution_root: Path | None = None,
    ) -> None:
        if "simulated" not in workers or len(workers) != len(set(workers)):
            raise ValueError("simulated worker is required exactly once")
        if not set(workers) <= {"simulated", "deriv_read_only", "iqoption"}:
            raise ValueError("launcher worker selection is invalid")
        self._profile_dir = Path(profile_dir).resolve()
        self._workers = workers
        self._restart_policy = restart_policy or LauncherRestartPolicy()
        self._controller_factory = controller_factory or (
            lambda profile, selected: SubprocessCoreController(
                profile,
                selected,
                startup_timeout=(60.0 if deriv_transport in {"live-demo", "live-real"} else 12.0),
                force_auth_simulation=force_auth_simulation,
                ui_headless=ui_headless,
                deriv_transport=deriv_transport,
            )
        )
        self._guard = guard_factory(self._profile_dir)
        self._monotonic = monotonic
        self._state = LauncherLifecycleState.UNINITIALIZED
        self._failure_reason: str | None = None
        self._manifest_path = Path(manifest_path).resolve() if manifest_path is not None else None
        self._distribution_root = (
            Path(distribution_root).resolve() if distribution_root is not None else None
        )
        self._controller: ManagedCoreController | None = None
        self._started_at: float | None = None
        self._last_processes = self._empty_processes("NOT_STARTED")
        self._restart_attempts = {
            ManagedProcessRole.AUTH_AGENT: 0,
            ManagedProcessRole.DERIV_WORKER: 0,
            ManagedProcessRole.IQOPTION_WORKER: 0,
        }
        self._lock = threading.RLock()

    @property
    def state(self) -> LauncherLifecycleState:
        with self._lock:
            return self._state

    @property
    def failure_reason(self) -> str | None:
        with self._lock:
            return self._failure_reason

    @property
    def controller(self) -> ManagedCoreController | None:
        return self._controller

    def start_all(self) -> bool:
        with self._lock:
            if self._state in {LauncherLifecycleState.HEALTHY, LauncherLifecycleState.DEGRADED}:
                return True
            if self._state is LauncherLifecycleState.STARTING:
                return False
            self._state = LauncherLifecycleState.STARTING
            self._failure_reason = None
            self._started_at = self._monotonic()
            for role in self._restart_attempts:
                self._restart_attempts[role] = 0
        try:
            if self._manifest_path is not None:
                root = self._distribution_root or self._manifest_path.parent
                verification = ReleaseIntegrityVerifier.verify_distribution(
                    root, self._manifest_path
                )
                if not verification.is_valid:
                    self._failure_reason = ReleaseIntegrityViolationError.reason_code
                    self._state = LauncherLifecycleState.FAILED
                    return False
            self._guard.acquire()
            controller = self._controller_factory(self._profile_dir, self._workers)
            self._controller = controller
            status = controller.start()
            processes = self._convert(status.processes)
            self._last_processes = processes
            healthy = self._is_healthy(status, processes)
            if not healthy:
                deadline = self._monotonic() + 6.0
                while self._monotonic() < deadline and not healthy:
                    time.sleep(0.25)
                    try:
                        status = controller.status()
                        processes = self._convert(status.processes)
                        self._last_processes = processes
                        healthy = self._is_healthy(status, processes)
                    except Exception:
                        break
            if healthy:
                self._state = LauncherLifecycleState.HEALTHY
                return True
            if self._is_essential_healthy(status, processes):
                self._state = LauncherLifecycleState.DEGRADED
                return True
            missing = [
                f"{role.value} ({processes[role].state})"
                for role in (
                    ManagedProcessRole.AUTH_AGENT,
                    ManagedProcessRole.CORE,
                    ManagedProcessRole.SIMULATED_WORKER,
                    ManagedProcessRole.UI,
                )
                if not (processes[role].is_alive and processes[role].state == "READY")
            ]
            self._failure_reason = "Falha em componentes essenciais: " + ", ".join(missing)
            self._state = LauncherLifecycleState.FAILED
            return False
        except Exception as exc:
            self._failure_reason = getattr(exc, "reason_code", type(exc).__name__)
            self._fail_safe_cleanup()
            self._state = LauncherLifecycleState.FAILED
            return False

    def poll_health(self) -> LauncherSnapshot:
        controller = self._controller
        if controller is None:
            return self.snapshot()
        process = controller.process
        if process is None or process.poll() is not None:
            self._handle_core_loss(process)
            return self.snapshot()
        try:
            status = controller.status()
            status = self._restart_non_financial_if_allowed(status)
        except (CoreLifecycleIpcError, OSError, RuntimeError):
            self._state = LauncherLifecycleState.DEGRADED
            return self.snapshot()
        ui_status = next((item for item in status.processes if item.role == "UI"), None)
        if status.ui_shutdown_requested or (ui_status is not None and not ui_status.is_alive):
            self.stop_all()
            return self.snapshot()
        processes = self._convert(status.processes)
        self._last_processes = processes
        self._state = (
            LauncherLifecycleState.HEALTHY
            if self._is_healthy(status, processes)
            else LauncherLifecycleState.DEGRADED
        )
        return self.snapshot()

    def stop_all(self, timeout_seconds: float = 10.0) -> bool:
        if not 0 < timeout_seconds <= 60:
            raise ValueError("launcher shutdown timeout is outside bounds")
        with self._lock:
            if self._state is LauncherLifecycleState.STOPPED:
                return True
            self._state = LauncherLifecycleState.STOPPING
        controller = self._controller
        graceful = True
        deadline = self._monotonic() + timeout_seconds
        try:
            if controller is not None:
                graceful &= self._attempt(controller.safe_stop)
                drain_timeout = min(2.0, self._remaining(deadline))
                try:
                    graceful &= controller.drain(drain_timeout).drained
                except (CoreLifecycleIpcError, OSError, RuntimeError, ValueError):
                    graceful = False
                worker_timeout = min(2.0, self._remaining(deadline))
                graceful &= self._attempt(lambda: controller.shutdown_workers(worker_timeout))
                auth_timeout = min(2.0, self._remaining(deadline))
                graceful &= self._attempt(lambda: controller.shutdown_auth(auth_timeout))
                core_timeout = min(2.0, self._remaining(deadline))
                graceful &= self._attempt(lambda: controller.shutdown_core(core_timeout))
                if not controller.wait(self._remaining(deadline)):
                    graceful = False
                    controller.terminate(min(1.0, self._remaining(deadline)))
                process = controller.process
                if process is not None and process.poll() is None:
                    graceful = False
                    controller.terminate_tree()
        finally:
            if controller is not None:
                controller.close()
            self._controller = None
            self._guard.release()
            self._last_processes = self._empty_processes("STOPPED")
            self._state = LauncherLifecycleState.STOPPED
        return graceful

    def snapshot(self) -> LauncherSnapshot:
        started = self._started_at
        uptime = 0.0 if started is None else max(0.0, self._monotonic() - started)
        return LauncherSnapshot(
            self._state,
            str(self._profile_dir),
            self._last_processes,
            uptime,
        )

    def _restart_non_financial_if_allowed(
        self, status: CoreLifecycleStatusResponse
    ) -> CoreLifecycleStatusResponse:
        controller = self._controller
        if controller is None:
            return status
        for item in status.processes:
            restartable = item.role in {"AUTH_AGENT", "DERIV_WORKER"}
            selected = item.role != "DERIV_WORKER" or "deriv_read_only" in self._workers
            unhealthy = item.state in {"UNAVAILABLE", "DISCONNECTED", "DEGRADED"}
            if (
                restartable
                and selected
                and unhealthy
                and self._restart_attempts[ManagedProcessRole(item.role)]
                < self._restart_policy.max_restarts_per_component
            ):
                self._restart_attempts[ManagedProcessRole(item.role)] += 1
                result = controller.restart_component(item.role)
                if result.accepted:
                    return controller.status()
        return status

    def _handle_core_loss(self, process: subprocess.Popen[str] | None) -> None:
        controller = self._controller
        if controller is not None:
            controller.terminate_tree()
            controller.close()
        self._controller = None
        self._guard.release()
        exit_code = None if process is None else process.poll()
        failed = self._empty_processes("TERMINATED_AFTER_CORE_LOSS")
        failed[ManagedProcessRole.CORE] = ProcessStatusSnapshot(
            ManagedProcessRole.CORE,
            None,
            False,
            1 if exit_code is None else exit_code,
            "FAILED",
            0,
        )
        self._last_processes = failed
        self._state = LauncherLifecycleState.FAILED

    def _fail_safe_cleanup(self) -> None:
        controller = self._controller
        if controller is not None:
            controller.terminate_tree()
            controller.close()
        self._controller = None
        self._guard.release()
        self._last_processes = self._empty_processes("STARTUP_FAILED")

    def _is_healthy(
        self,
        status: CoreLifecycleStatusResponse,
        processes: Mapping[ManagedProcessRole, ProcessStatusSnapshot],
    ) -> bool:
        required = {
            ManagedProcessRole.AUTH_AGENT,
            ManagedProcessRole.CORE,
            ManagedProcessRole.SIMULATED_WORKER,
            ManagedProcessRole.UI,
        }
        if "deriv_read_only" in self._workers:
            required.add(ManagedProcessRole.DERIV_WORKER)
        if "iqoption" in self._workers:
            required.add(ManagedProcessRole.IQOPTION_WORKER)
        return status.core_state == "READY" and all(
            processes[role].is_alive and processes[role].state == "READY" for role in required
        )

    def _is_essential_healthy(
        self,
        status: CoreLifecycleStatusResponse,
        processes: Mapping[ManagedProcessRole, ProcessStatusSnapshot],
    ) -> bool:
        essential = {
            ManagedProcessRole.AUTH_AGENT,
            ManagedProcessRole.CORE,
            ManagedProcessRole.SIMULATED_WORKER,
            ManagedProcessRole.UI,
        }
        return status.core_state == "READY" and all(
            processes[role].is_alive and processes[role].state == "READY" for role in essential
        )

    @staticmethod
    def _convert(
        items: tuple[LifecycleProcessStatus, ...],
    ) -> dict[ManagedProcessRole, ProcessStatusSnapshot]:
        result = ProcessTreeSupervisor._empty_processes("NOT_SELECTED")
        for item in items:
            role = ManagedProcessRole(item.role)
            result[role] = ProcessStatusSnapshot(
                role,
                item.pid,
                item.is_alive,
                item.exit_code,
                item.state,
                item.restarts_count,
            )
        return result

    @staticmethod
    def _empty_processes(state: str) -> dict[ManagedProcessRole, ProcessStatusSnapshot]:
        return {
            role: ProcessStatusSnapshot(role, None, False, None, state, 0)
            for role in ManagedProcessRole
        }

    @staticmethod
    def _attempt(operation: Callable[[], bool]) -> bool:
        try:
            return operation()
        except (CoreLifecycleIpcError, OSError, RuntimeError, ValueError):
            return False

    def _remaining(self, deadline: float) -> float:
        return max(0.05, deadline - self._monotonic())
