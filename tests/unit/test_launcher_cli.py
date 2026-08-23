from __future__ import annotations

import sys
from pathlib import Path

import pytest

from apps.launcher.cli import _default_profile_dir, main
from packages.security import ReleaseManifestBuilder


def test_frozen_style_main_reads_process_argv_without_name_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The packaged entrypoint calls main() and therefore exercises sys.argv directly."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["TradingLab.exe", "--post-update-health-check"],
    )

    assert main() == 0


def test_frozen_health_check_requires_and_verifies_adjacent_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "TradingLab.exe"
    executable.write_bytes(b"packaged-executable-placeholder")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "argv", [str(executable), "--post-update-health-check"])

    assert main() == 1

    manifest = ReleaseManifestBuilder.build_manifest(tmp_path, "1.0.0", "win64")
    ReleaseManifestBuilder.write_manifest(manifest, tmp_path / "release_manifest.json")

    assert main() == 0


def test_frozen_default_profile_is_user_writable_local_app_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert _default_profile_dir() == tmp_path / "TradingLab" / "profiles" / "default"
