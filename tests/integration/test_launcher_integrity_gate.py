from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from apps.launcher.models import LauncherLifecycleState
from apps.launcher.supervisor import ProcessTreeSupervisor
from packages.security.integrity import (
    ReleaseIntegrityViolationError,
    ReleaseManifestBuilder,
)


def _setup_distribution(root: Path) -> tuple[Path, Path]:
    dist = root / "dist_pkg"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "DualTrade.bat").write_text("@echo off", encoding="utf-8")
    (dist / "main.py").write_text("print('core')", encoding="utf-8")

    manifest = ReleaseManifestBuilder.build_manifest(dist, "1.0.0")
    manifest_path = dist / "release_manifest.json"
    ReleaseManifestBuilder.write_manifest(manifest, manifest_path)
    return dist, manifest_path


def test_launcher_supervisor_passes_valid_distribution(tmp_path: Path) -> None:
    dist, manifest_path = _setup_distribution(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()

    mock_controller = MagicMock()
    from packages.protocol import CoreLifecycleStatusResponse, LifecycleProcessStatus

    mock_controller.start.return_value = CoreLifecycleStatusResponse(
        "READY",
        False,
        tuple(
            LifecycleProcessStatus(role, index + 100, True, None, "READY", 0)
            for index, role in enumerate(
                ("AUTH_AGENT", "CORE", "SIMULATED_WORKER", "DERIV_WORKER", "IQOPTION_WORKER", "UI")
            )
        ),
    )

    supervisor = ProcessTreeSupervisor(
        profile_dir,
        manifest_path=manifest_path,
        distribution_root=dist,
        controller_factory=lambda p, w: mock_controller,
        guard_factory=lambda p: MagicMock(),
    )

    started = supervisor.start_all()

    assert started is True
    assert supervisor.state in {LauncherLifecycleState.HEALTHY, LauncherLifecycleState.DEGRADED}
    assert supervisor.failure_reason is None
    mock_controller.start.assert_called_once()


def test_launcher_supervisor_fails_closed_on_tampered_distribution(tmp_path: Path) -> None:
    dist, manifest_path = _setup_distribution(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()

    # Tamper with file
    (dist / "main.py").write_text("print('tampered')", encoding="utf-8")

    mock_controller = MagicMock()

    supervisor = ProcessTreeSupervisor(
        profile_dir,
        manifest_path=manifest_path,
        distribution_root=dist,
        controller_factory=lambda p, w: mock_controller,
        guard_factory=lambda p: MagicMock(),
    )

    started = supervisor.start_all()

    assert started is False
    assert supervisor.state is LauncherLifecycleState.FAILED
    assert supervisor.failure_reason == ReleaseIntegrityViolationError.reason_code
    # Controller must NEVER have been started
    mock_controller.start.assert_not_called()
