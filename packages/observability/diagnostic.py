from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.observability.events import OperationalEvent
from packages.observability.retention import (
    ReportRetentionManager,
    ReportRetentionPolicy,
)
from packages.security.secret_scanner import SecretScanner

DEFAULT_MAX_DIAGNOSTIC_EVENTS = 1000
DEFAULT_DIAGNOSTIC_REPORTS_LIMIT = 5
DEFAULT_DIAGNOSTIC_MAX_BYTES = 50 * 1024 * 1024


class DiagnosticSecurityViolationError(RuntimeError):
    reason_code = "DIAGNOSTIC_SECURITY_VIOLATION"


@dataclass(frozen=True, slots=True)
class DiagnosticContext:
    app_version: str
    python_version: str
    os_name: str
    os_release: str
    os_version: str
    uptime_seconds: float
    environment_meta: Mapping[str, Any] = field(default_factory=dict)
    health_snapshot: Mapping[str, Any] = field(default_factory=dict)
    risk_metrics: Mapping[str, Any] = field(default_factory=dict)
    recent_events: Sequence[OperationalEvent] = field(default_factory=tuple)
    process_tree: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DiagnosticBundleResult:
    zip_path: Path
    sha256_hash: str
    file_size_bytes: int
    generated_at_utc: datetime
    file_count: int


class DiagnosticBundleBuilder:
    """Builds secure, redacted diagnostic bundles and verifies zero secret leakage."""

    def __init__(
        self,
        retention_policy: ReportRetentionPolicy | None = None,
        scanner: SecretScanner | None = None,
    ) -> None:
        self._retention_policy = retention_policy or ReportRetentionPolicy(
            max_reports=DEFAULT_DIAGNOSTIC_REPORTS_LIMIT,
            max_total_bytes=DEFAULT_DIAGNOSTIC_MAX_BYTES,
            file_pattern="diagnostic_bundle_*.zip",
        )
        self._retention_manager = ReportRetentionManager()
        self._scanner = scanner or SecretScanner()

    def build_bundle(
        self,
        output_dir: Path,
        context: DiagnosticContext,
        *,
        max_events: int = DEFAULT_MAX_DIAGNOSTIC_EVENTS,
    ) -> DiagnosticBundleResult:
        if max_events <= 0:
            raise ValueError("max_events must be positive")

        output_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        temp_dir = output_dir / f".tmp_diag_{uuid4().hex}"
        temp_zip: Path | None = None

        try:
            temp_dir.mkdir(parents=True, exist_ok=True)

            # 1. Environment & Process info
            env_payload = {
                "app_version": context.app_version,
                "environment_meta": dict(context.environment_meta),
                "os_name": context.os_name,
                "os_release": context.os_release,
                "os_version": context.os_version,
                "process_tree": [dict(p) for p in context.process_tree],
                "python_version": context.python_version,
                "uptime_seconds": context.uptime_seconds,
            }
            self._write_json(temp_dir / "environment.json", env_payload)

            # 2. Health Gates snapshot
            self._write_json(temp_dir / "health_gates.json", dict(context.health_snapshot))

            # 3. Risk & Exposure metrics
            self._write_json(temp_dir / "risk_summary.json", dict(context.risk_metrics))

            # 4. Recent Operational Events (bounded)
            events_to_include = (
                context.recent_events[-max_events:]
                if len(context.recent_events) > max_events
                else context.recent_events
            )
            events_payload = [
                {
                    "event_name": event.event_name,
                    "fields": dict(event.fields),
                    "occurred_at": event.occurred_at.isoformat(),
                    "reason_code": event.reason_code,
                }
                for event in events_to_include
            ]
            self._write_json(temp_dir / "recent_events.json", {"events": events_payload})

            # 5. Build Manifest with file hashes (before scanning)
            files_meta: dict[str, dict[str, Any]] = {}
            for item in sorted(temp_dir.iterdir()):
                if item.is_file() and not item.name.startswith("."):
                    file_bytes = item.read_bytes()
                    file_sha = hashlib.sha256(file_bytes).hexdigest()
                    files_meta[item.name] = {
                        "sha256": file_sha,
                        "size_bytes": len(file_bytes),
                    }

            manifest_payload = {
                "app_version": context.app_version,
                "files": files_meta,
                "generated_at_utc": now.isoformat(),
            }
            self._write_json(temp_dir / "manifest.json", manifest_payload)

            # 6. SCAN FOR SECRETS (Fail-Closed)
            scan_report = self._scanner.scan_directory(temp_dir)
            if not scan_report.is_clean:
                raise DiagnosticSecurityViolationError(
                    f"Secret pattern detected during diagnostic compilation: "
                    f"{scan_report.total_matches} match(es) found"
                )

            # 7. Zip all JSON files
            ts_str = now.strftime("%Y%m%d_%H%M%S")
            temp_zip = output_dir / f".tmp_{ts_str}_{uuid4().hex}.zip"

            file_count = 0
            with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in sorted(temp_dir.iterdir()):
                    if file_path.is_file() and not file_path.name.startswith("."):
                        # Prohibit including SQLite databases, keys or vault files
                        if file_path.suffix in {".db", ".wal", ".vault", ".key", ".pem"}:
                            raise DiagnosticSecurityViolationError(
                                f"Prohibited file type {file_path.name} attempted in bundle"
                            )
                        zf.write(file_path, arcname=file_path.name)
                        file_count += 1

            zip_bytes = temp_zip.read_bytes()
            zip_sha = hashlib.sha256(zip_bytes).hexdigest()
            final_filename = f"diagnostic_bundle_{ts_str}_{zip_sha[:8]}.zip"
            final_zip_path = output_dir / final_filename

            os.replace(temp_zip, final_zip_path)
            temp_zip = None

            # 8. Apply Retention Policy
            self._retention_manager.enforce_retention(output_dir, self._retention_policy)

            return DiagnosticBundleResult(
                zip_path=final_zip_path,
                sha256_hash=zip_sha,
                file_size_bytes=final_zip_path.stat().st_size,
                generated_at_utc=now,
                file_count=file_count,
            )
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if temp_zip is not None and temp_zip.exists():
                with suppress(OSError):
                    temp_zip.unlink(missing_ok=True)

    @staticmethod
    def _write_json(file_path: Path, payload: dict[str, Any]) -> None:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        file_path.write_text(encoded + "\n", encoding="utf-8")
