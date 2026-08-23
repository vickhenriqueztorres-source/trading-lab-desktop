from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_REPORTS_LIMIT = 100
MAX_TOTAL_BYTES_LIMIT = 1024 * 1024 * 1024


class AtomicJsonWriteError(RuntimeError):
    reason_code = "ATOMIC_JSON_WRITE_FAILED"


class ReportRetentionError(RuntimeError):
    reason_code = "REPORT_RETENTION_FAILED"


@dataclass(frozen=True, slots=True)
class ReportRetentionPolicy:
    max_reports: int = 10
    max_total_bytes: int = 20 * 1024 * 1024
    file_pattern: str = "soak_matrix_*.json"

    def __post_init__(self) -> None:
        if not 1 <= self.max_reports <= MAX_REPORTS_LIMIT:
            raise ValueError("report retention count must be between 1 and 100")
        if not 1 <= self.max_total_bytes <= MAX_TOTAL_BYTES_LIMIT:
            raise ValueError("report retention byte limit must be between 1 and 1073741824")
        is_soak = self.file_pattern.startswith("soak_matrix_") and self.file_pattern.endswith(
            ".json"
        )
        is_diag = self.file_pattern.startswith("diagnostic_bundle_") and self.file_pattern.endswith(
            ".zip"
        )
        if Path(self.file_pattern).name != self.file_pattern or not (is_soak or is_diag):
            raise ValueError(
                "report retention pattern must target soak_matrix JSON or diagnostic ZIP filenames"
            )


@dataclass(frozen=True, slots=True)
class RetentionSummary:
    scanned_files: int
    deleted_files: int
    retained_files: int
    total_bytes_retained: int
    deleted_filenames: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ReportFile:
    path: Path
    modified_ns: int
    size_bytes: int


def atomic_write_json(
    file_path: Path,
    payload: dict[str, Any],
    indent: int = 2,
) -> None:
    if not 0 <= indent <= 8:
        raise ValueError("JSON indentation must be between 0 and 8")
    temporary: Path | None = None
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.is_symlink():
            raise OSError("atomic JSON destination cannot be a symbolic link")
        temporary = file_path.with_name(f".{file_path.name}.{uuid4().hex}.tmp")
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, file_path)
        temporary = None
    except Exception as exc:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        raise AtomicJsonWriteError(AtomicJsonWriteError.reason_code) from exc


class ReportRetentionManager:
    def enforce_retention(
        self,
        target_dir: Path,
        policy: ReportRetentionPolicy,
    ) -> RetentionSummary:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            resolved_dir = target_dir.resolve(strict=True)
            if not resolved_dir.is_dir():
                raise OSError("report retention target is not a directory")
            reports = self._scan_reports(target_dir, resolved_dir, policy)
            scanned_files = len(reports)
            total_bytes = sum(report.size_bytes for report in reports)
            pending = list(reports)
            to_delete: list[_ReportFile] = []
            while len(pending) > policy.max_reports or total_bytes > policy.max_total_bytes:
                oldest = pending.pop(0)
                to_delete.append(oldest)
                total_bytes -= oldest.size_bytes
            deleted_filenames: list[str] = []
            for report in to_delete:
                report.path.unlink()
                deleted_filenames.append(report.path.name)
            return RetentionSummary(
                scanned_files=scanned_files,
                deleted_files=len(deleted_filenames),
                retained_files=len(pending),
                total_bytes_retained=total_bytes,
                deleted_filenames=tuple(deleted_filenames),
            )
        except ReportRetentionError:
            raise
        except Exception as exc:
            raise ReportRetentionError(ReportRetentionError.reason_code) from exc

    @staticmethod
    def _scan_reports(
        target_dir: Path,
        resolved_dir: Path,
        policy: ReportRetentionPolicy,
    ) -> tuple[_ReportFile, ...]:
        reports: list[_ReportFile] = []
        for candidate in target_dir.glob(policy.file_pattern):
            if candidate.is_symlink():
                raise ReportRetentionError("REPORT_RETENTION_SYMLINK_FORBIDDEN")
            if not candidate.is_file():
                continue
            resolved_candidate = candidate.resolve(strict=True)
            if resolved_candidate.parent != resolved_dir:
                raise ReportRetentionError("REPORT_RETENTION_SCOPE_MISMATCH")
            stat = resolved_candidate.stat()
            reports.append(
                _ReportFile(
                    path=candidate,
                    modified_ns=stat.st_mtime_ns,
                    size_bytes=stat.st_size,
                )
            )
        reports.sort(key=lambda report: (report.modified_ns, report.path.name))
        return tuple(reports)
