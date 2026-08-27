from __future__ import annotations

import ctypes
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from apps.core.lifecycle_service import CoreLifecycleService
from apps.launcher.models import LauncherLifecycleState, ManagedProcessRole
from apps.launcher.supervisor import ProcessTreeSupervisor

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process-tree proof")

_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 258


def _is_process_alive(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _terminate_pid(pid: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
    if not handle:
        return
    try:
        if not kernel32.TerminateProcess(handle, 17):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.WaitForSingleObject(handle, 5_000)
    finally:
        kernel32.CloseHandle(handle)


def _wait_dead(pids: list[int], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(not _is_process_alive(pid) for pid in pids):
            return True
        time.sleep(0.02)
    return all(not _is_process_alive(pid) for pid in pids)


def _readline_with_timeout(process: subprocess.Popen[str], timeout: float) -> str:
    output: queue.Queue[str] = queue.Queue(maxsize=1)

    def read() -> None:
        output.put("" if process.stdout is None else process.stdout.readline())

    threading.Thread(target=read, name="launcher-actor-reader", daemon=True).start()
    try:
        return output.get(timeout=timeout)
    except queue.Empty as exc:
        raise AssertionError("launcher actor did not report its process tree") from exc


def test_core_service_starts_components_in_declared_order(tmp_path: Path) -> None:
    service = CoreLifecycleService(
        tmp_path,
        ("simulated", "deriv_read_only"),
        force_auth_simulation=True,
    )
    try:
        service.start()
        assert service.startup_sequence == (
            "AUTH_AGENT",
            "CORE",
            "SIMULATED_WORKER",
            "DERIV_WORKER",
        )
        assert all(item.is_alive for item in service.process_statuses())
    finally:
        service.emergency_shutdown()


def test_launcher_real_tree_starts_and_stops_without_orphans(tmp_path: Path) -> None:
    supervisor = ProcessTreeSupervisor(tmp_path)
    assert supervisor.start_all()
    snapshot = supervisor.poll_health()
    pids = [item.pid for item in snapshot.processes.values() if item.pid is not None]
    assert snapshot.overall_state is LauncherLifecycleState.HEALTHY
    assert snapshot.uptime_seconds <= 15.0
    assert len(set(pids)) == 5

    assert supervisor.stop_all(10.0)
    assert _wait_dead(pids)
    assert supervisor.snapshot().overall_state is LauncherLifecycleState.STOPPED


def test_worker_loss_degrades_without_killing_core(tmp_path: Path) -> None:
    supervisor = ProcessTreeSupervisor(tmp_path)
    assert supervisor.start_all()
    snapshot = supervisor.snapshot()
    core_pid = snapshot.processes[ManagedProcessRole.CORE].pid
    worker_pid = snapshot.processes[ManagedProcessRole.SIMULATED_WORKER].pid
    assert core_pid is not None and worker_pid is not None

    _terminate_pid(worker_pid)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        snapshot = supervisor.poll_health()
        if snapshot.overall_state is LauncherLifecycleState.DEGRADED:
            break
        time.sleep(0.05)

    assert snapshot.overall_state is LauncherLifecycleState.DEGRADED
    assert _is_process_alive(core_pid)
    assert snapshot.processes[ManagedProcessRole.SIMULATED_WORKER].is_alive is False
    supervisor.stop_all(10.0)
    assert _wait_dead([core_pid, worker_pid])


def test_ui_loss_stops_entire_process_tree(tmp_path: Path) -> None:
    supervisor = ProcessTreeSupervisor(tmp_path)
    assert supervisor.start_all()
    snapshot = supervisor.snapshot()
    core_pid = snapshot.processes[ManagedProcessRole.CORE].pid
    ui_pid = snapshot.processes[ManagedProcessRole.UI].pid
    assert core_pid is not None and ui_pid is not None

    _terminate_pid(ui_pid)
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        snapshot = supervisor.poll_health()
        if snapshot.overall_state is LauncherLifecycleState.STOPPED:
            break
        time.sleep(0.05)

    assert snapshot.overall_state is LauncherLifecycleState.STOPPED
    assert snapshot.processes[ManagedProcessRole.UI].is_alive is False
    assert not _is_process_alive(core_pid)
    assert _wait_dead([core_pid, ui_pid])


def test_non_financial_components_restart_without_restarting_core(tmp_path: Path) -> None:
    supervisor = ProcessTreeSupervisor(tmp_path)
    assert supervisor.start_all()
    snapshot = supervisor.snapshot()
    core_pid = snapshot.processes[ManagedProcessRole.CORE].pid
    auth_pid = snapshot.processes[ManagedProcessRole.AUTH_AGENT].pid
    deriv_pid = snapshot.processes[ManagedProcessRole.DERIV_WORKER].pid
    assert core_pid is not None and auth_pid is not None and deriv_pid is not None

    for role, old_pid in (
        (ManagedProcessRole.AUTH_AGENT, auth_pid),
        (ManagedProcessRole.DERIV_WORKER, deriv_pid),
    ):
        _terminate_pid(old_pid)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            snapshot = supervisor.poll_health()
            current = snapshot.processes[role]
            if current.is_alive and current.pid != old_pid and current.restarts_count == 1:
                break
            time.sleep(0.05)
        current = snapshot.processes[role]
        assert current.is_alive
        assert current.pid != old_pid
        assert current.restarts_count == 1
        assert snapshot.processes[ManagedProcessRole.CORE].pid == core_pid

    assert supervisor.stop_all(10.0)
    assert _wait_dead([core_pid, auth_pid, deriv_pid])


def test_core_loss_terminates_descendants_and_releases_profile_lock(tmp_path: Path) -> None:
    supervisor = ProcessTreeSupervisor(tmp_path)
    assert supervisor.start_all()
    snapshot = supervisor.snapshot()
    pids = [item.pid for item in snapshot.processes.values() if item.pid is not None]
    core_pid = snapshot.processes[ManagedProcessRole.CORE].pid
    assert core_pid is not None

    _terminate_pid(core_pid)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        snapshot = supervisor.poll_health()
        if snapshot.overall_state is LauncherLifecycleState.FAILED:
            break
        time.sleep(0.05)

    assert snapshot.overall_state is LauncherLifecycleState.FAILED
    assert _wait_dead(pids)

    replacement = ProcessTreeSupervisor(tmp_path, workers=("simulated",))
    assert replacement.start_all()
    assert replacement.stop_all(10.0)


def test_second_launcher_is_rejected_without_disturbing_owner(tmp_path: Path) -> None:
    owner = ProcessTreeSupervisor(tmp_path, workers=("simulated",))
    contender = ProcessTreeSupervisor(tmp_path, workers=("simulated",))
    assert owner.start_all()
    assert contender.start_all() is False
    assert contender.snapshot().overall_state is LauncherLifecycleState.FAILED
    assert owner.poll_health().overall_state is LauncherLifecycleState.HEALTHY
    assert owner.stop_all(10.0)


def test_job_object_kills_descendants_when_launcher_process_is_killed(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    actor = subprocess.Popen(
        [sys.executable, "-m", "tests.helpers.launcher_actor", str(tmp_path)],
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    pids: list[int] = []
    try:
        line = _readline_with_timeout(actor, 15.0)
        document = json.loads(line)
        assert isinstance(document, dict)
        pids = [int(value) for value in document.values()]
        assert len(set(pids)) == 5
        actor.kill()
        actor.wait(timeout=3.0)
        assert _wait_dead(pids)
    finally:
        if actor.poll() is None:
            actor.kill()
            actor.wait(timeout=3.0)
        for pid in pids:
            if _is_process_alive(pid):
                _terminate_pid(pid)


def test_launcher_cli_auto_shutdown_is_executable_and_releases_profile(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "apps.launcher",
            "--profile-dir",
            str(tmp_path),
            "--workers",
            "simulated",
            "--auto-shutdown-after",
            "0.1",
        ],
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20.0,
        check=False,
    )
    assert result.returncode == 0
    assert "token" not in result.stdout.lower()

    replacement = ProcessTreeSupervisor(tmp_path, workers=("simulated",))
    assert replacement.start_all()
    assert replacement.stop_all(10.0)
