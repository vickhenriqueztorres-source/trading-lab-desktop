from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from packages.observability.retention import (
    AtomicJsonWriteError,
    ReportRetentionError,
    ReportRetentionManager,
    ReportRetentionPolicy,
    atomic_write_json,
)


def _write_report(path: Path, size: int, modified_ns: int) -> None:
    path.write_bytes(b"x" * size)
    os.utime(path, ns=(modified_ns, modified_ns))


def test_atomic_write_json_publishes_complete_canonical_utf8_document(tmp_path: Path) -> None:
    destination = tmp_path / "reports" / "soak_matrix_20260821_120000_PASSED.json"

    atomic_write_json(destination, {"z": 1, "message": "saúde", "nested": {"ok": True}})

    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "message": "saúde",
        "nested": {"ok": True},
        "z": 1,
    }
    assert destination.read_text(encoding="utf-8").startswith('{\n  "message"')
    assert not tuple(destination.parent.glob("*.tmp"))


def test_atomic_write_json_leaves_no_temporary_after_serialization_failure(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "soak_matrix_invalid.json"

    with pytest.raises(AtomicJsonWriteError, match="ATOMIC_JSON_WRITE_FAILED"):
        atomic_write_json(destination, {"unsupported": object()})

    assert not destination.exists()
    assert not tuple(tmp_path.glob("*.tmp"))


def test_atomic_write_json_cleans_temporary_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "soak_matrix_replace_failed.json"

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("packages.observability.retention.os.replace", fail_replace)

    with pytest.raises(AtomicJsonWriteError, match="ATOMIC_JSON_WRITE_FAILED"):
        atomic_write_json(destination, {"outcome": "PASSED"})

    assert not destination.exists()
    assert not tuple(tmp_path.glob("*.tmp"))


def test_retention_deletes_oldest_reports_until_count_is_bounded(tmp_path: Path) -> None:
    paths = [tmp_path / f"soak_matrix_20260821_12000{index}_PASSED.json" for index in range(4)]
    for index, path in enumerate(paths):
        _write_report(path, size=10, modified_ns=1_000 + index)
    unrelated = tmp_path / "diagnostic.json"
    unrelated.write_text("preserve", encoding="utf-8")

    summary = ReportRetentionManager().enforce_retention(
        tmp_path,
        ReportRetentionPolicy(max_reports=2, max_total_bytes=1_000),
    )

    assert summary.scanned_files == 4
    assert summary.deleted_files == 2
    assert summary.retained_files == 2
    assert summary.total_bytes_retained == 20
    assert summary.deleted_filenames == (paths[0].name, paths[1].name)
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists()
    assert paths[3].exists()
    assert unrelated.exists()


def test_retention_deletes_oldest_reports_until_total_bytes_are_bounded(
    tmp_path: Path,
) -> None:
    paths = [tmp_path / f"soak_matrix_bytes_{index}.json" for index in range(3)]
    for index, path in enumerate(paths):
        _write_report(path, size=8, modified_ns=2_000 + index)

    summary = ReportRetentionManager().enforce_retention(
        tmp_path,
        ReportRetentionPolicy(max_reports=10, max_total_bytes=10),
    )

    assert summary.deleted_filenames == (paths[0].name, paths[1].name)
    assert summary.retained_files == 1
    assert summary.total_bytes_retained == 8
    assert paths[2].exists()


def test_retention_creates_missing_directory_and_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "reports"
    manager = ReportRetentionManager()
    policy = ReportRetentionPolicy(max_reports=3, max_total_bytes=100)

    first = manager.enforce_retention(target, policy)
    second = manager.enforce_retention(target, policy)

    assert target.is_dir()
    assert first == second
    assert first.scanned_files == 0
    assert first.deleted_files == 0
    assert first.deleted_filenames == ()


def test_retention_fails_closed_when_a_selected_report_cannot_be_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oldest = tmp_path / "soak_matrix_old.json"
    newest = tmp_path / "soak_matrix_new.json"
    _write_report(oldest, size=10, modified_ns=1_000)
    _write_report(newest, size=10, modified_ns=2_000)
    original_unlink = Path.unlink

    def fail_oldest(path: Path, *, missing_ok: bool = False) -> None:
        if path == oldest:
            raise PermissionError("simulated locked report")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_oldest)

    with pytest.raises(ReportRetentionError, match="REPORT_RETENTION_FAILED"):
        ReportRetentionManager().enforce_retention(
            tmp_path,
            ReportRetentionPolicy(max_reports=1, max_total_bytes=100),
        )

    assert oldest.exists()
    assert newest.exists()


def test_retention_policy_rejects_unbounded_or_unsafe_configuration() -> None:
    with pytest.raises(ValueError, match="count"):
        ReportRetentionPolicy(max_reports=0)
    with pytest.raises(ValueError, match="count"):
        ReportRetentionPolicy(max_reports=101)
    with pytest.raises(ValueError, match="byte"):
        ReportRetentionPolicy(max_total_bytes=0)
    with pytest.raises(ValueError, match="pattern"):
        ReportRetentionPolicy(file_pattern="*.json")
    with pytest.raises(ValueError, match="pattern"):
        ReportRetentionPolicy(file_pattern="../soak_matrix_*.json")
