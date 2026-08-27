from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.launcher.cli import (
    _default_profile_dir,
    _restore_redirected_standard_streams,
    build_parser,
    main,
)
from packages.security import ReleaseManifestBuilder


def test_default_startup_does_not_depend_on_deriv_network() -> None:
    arguments = build_parser().parse_args([])

    assert arguments.deriv_transport == "fake-public"


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


def test_windowed_frozen_child_restores_only_missing_redirected_streams() -> None:
    existing_stdin = object()
    system = SimpleNamespace(stdin=existing_stdin, stdout=None, stderr=None)
    duplicated: list[int] = []
    opened: list[tuple[int, str, dict[str, Any]]] = []

    def duplicate(descriptor: int) -> int:
        duplicated.append(descriptor)
        return descriptor + 100

    def open_fd(descriptor: int, mode: str, **kwargs: Any) -> Any:
        opened.append((descriptor, mode, kwargs))
        return f"stream-{descriptor}"

    _restore_redirected_standard_streams(
        system=system,
        duplicate=duplicate,
        open_fd=open_fd,
    )

    assert system.stdin is existing_stdin
    assert system.stdout == "stream-101"
    assert system.stderr == "stream-102"
    assert duplicated == [1, 2]
    assert [item[:2] for item in opened] == [(101, "w"), (102, "w")]
