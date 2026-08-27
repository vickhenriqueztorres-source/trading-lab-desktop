from __future__ import annotations

from pathlib import Path

import pytest

from apps.launcher.instance import LauncherAlreadyRunning, LauncherInstanceGuard
from apps.launcher.models import LauncherLifecycleState, ManagedProcessRole
from apps.launcher.process_controller import SubprocessCoreController
from apps.launcher.supervisor import ProcessTreeSupervisor
from apps.launcher.windows_job import NoopProcessContainment
from packages.protocol import (
    CoreDrainResponse,
    CoreLifecycleStatusResponse,
    CoreRestartComponentResponse,
    LifecycleProcessStatus,
)


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 43210
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


def _ready_status() -> CoreLifecycleStatusResponse:
    return CoreLifecycleStatusResponse(
        "READY",
        False,
        tuple(
            LifecycleProcessStatus(role, index + 100, True, None, "READY", 0)
            for index, role in enumerate(
                ("AUTH_AGENT", "CORE", "SIMULATED_WORKER", "DERIV_WORKER", "IQOPTION_WORKER", "UI")
            )
        ),
    )


class FakeController:
    def __init__(self, *, wait_result: bool = True) -> None:
        self.process = FakeProcess()
        self.events: list[str] = []
        self.wait_result = wait_result
        self._status = _ready_status()

    def start(self) -> CoreLifecycleStatusResponse:
        self.events.append("start")
        return self._status

    def status(self) -> CoreLifecycleStatusResponse:
        self.events.append("status")
        return self._status

    def safe_stop(self) -> bool:
        self.events.append("safe_stop")
        return True

    def drain(self, timeout: float) -> CoreDrainResponse:
        assert timeout > 0
        self.events.append("drain")
        return CoreDrainResponse(True, 0)

    def shutdown_workers(self, timeout: float) -> bool:
        assert timeout > 0
        self.events.append("workers")
        return True

    def shutdown_auth(self, timeout: float) -> bool:
        assert timeout > 0
        self.events.append("auth")
        return True

    def restart_component(self, role: str) -> CoreRestartComponentResponse:
        self.events.append(f"restart:{role}")
        return CoreRestartComponentResponse(True, "RESTART_COMPLETED")

    def shutdown_core(self, timeout: float) -> bool:
        assert timeout > 0
        self.events.append("core")
        if self.wait_result:
            self.process.returncode = 0
        return True

    def wait(self, timeout: float) -> bool:
        assert timeout > 0
        self.events.append("wait")
        return self.wait_result

    def terminate(self, timeout: float) -> None:
        assert timeout > 0
        self.events.append("terminate")
        self.process.returncode = 1

    def terminate_tree(self) -> None:
        self.events.append("terminate_tree")
        self.process.returncode = 1

    def close(self) -> None:
        self.events.append("close")


def test_launcher_models_and_startup_snapshot_are_immutable(tmp_path: Path) -> None:
    controller = FakeController()
    supervisor = ProcessTreeSupervisor(
        tmp_path,
        controller_factory=lambda _profile, _workers: controller,
    )

    assert supervisor.start_all() is True
    snapshot = supervisor.snapshot()

    assert snapshot.overall_state is LauncherLifecycleState.HEALTHY
    assert snapshot.processes[ManagedProcessRole.CORE].pid == 101
    with pytest.raises(TypeError):
        snapshot.processes[ManagedProcessRole.CORE] = snapshot.processes[ManagedProcessRole.CORE]
    assert supervisor.stop_all() is True


def test_safe_shutdown_ladder_is_strict_and_idempotent(tmp_path: Path) -> None:
    controller = FakeController()
    supervisor = ProcessTreeSupervisor(
        tmp_path,
        controller_factory=lambda _profile, _workers: controller,
    )
    assert supervisor.start_all()

    assert supervisor.stop_all() is True
    assert controller.events == [
        "start",
        "safe_stop",
        "drain",
        "workers",
        "auth",
        "core",
        "wait",
        "close",
    ]
    assert supervisor.stop_all() is True


def test_shutdown_timeout_escalates_to_terminate(tmp_path: Path) -> None:
    controller = FakeController(wait_result=False)
    supervisor = ProcessTreeSupervisor(
        tmp_path,
        controller_factory=lambda _profile, _workers: controller,
    )
    assert supervisor.start_all()

    assert supervisor.stop_all(0.2) is False
    assert "terminate" in controller.events
    assert controller.process.poll() == 1


def test_ui_process_loss_stops_entire_tree_and_releases_profile(tmp_path: Path) -> None:
    controller = FakeController()
    supervisor = ProcessTreeSupervisor(
        tmp_path,
        controller_factory=lambda _profile, _workers: controller,
    )
    assert supervisor.start_all()
    controller._status = CoreLifecycleStatusResponse(
        "READY",
        True,
        tuple(
            LifecycleProcessStatus(
                item.role,
                item.pid,
                False if item.role == "UI" else item.is_alive,
                0 if item.role == "UI" else item.exit_code,
                "STOPPED" if item.role == "UI" else item.state,
                item.restarts_count,
            )
            for item in controller._status.processes
        ),
    )

    snapshot = supervisor.poll_health()

    assert snapshot.overall_state is LauncherLifecycleState.STOPPED
    assert controller.events[-7:] == [
        "safe_stop",
        "drain",
        "workers",
        "auth",
        "core",
        "wait",
        "close",
    ]
    replacement = LauncherInstanceGuard(tmp_path)
    replacement.acquire()
    replacement.release()


def test_launcher_profile_lock_rejects_second_owner_and_recovers(tmp_path: Path) -> None:
    first = LauncherInstanceGuard(tmp_path)
    second = LauncherInstanceGuard(tmp_path)
    first.acquire()
    try:
        with pytest.raises(LauncherAlreadyRunning, match="LAUNCHER_INSTANCE_ALREADY_RUNNING"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_controller_terminate_escalates_from_terminate_to_kill(tmp_path: Path) -> None:
    class StubbornProcess:
        pid = 99999

        def __init__(self) -> None:
            self.terminated = False
            self.killed = False

        def poll(self) -> int | None:
            return 1 if self.killed else None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float) -> int:
            if not self.killed:
                raise __import__("subprocess").TimeoutExpired("stubborn", timeout)
            return 1

    controller = SubprocessCoreController(
        tmp_path,
        ("simulated",),
        containment=NoopProcessContainment(),
    )
    process = StubbornProcess()
    controller._process = process  # type: ignore[assignment]

    controller.terminate(0.05)

    assert process.terminated is True
    assert process.killed is True
