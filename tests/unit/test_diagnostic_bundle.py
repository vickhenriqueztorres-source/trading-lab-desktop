from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.observability.diagnostic import (
    DiagnosticBundleBuilder,
    DiagnosticContext,
    DiagnosticSecurityViolationError,
)
from packages.observability.events import OperationalEvent
from packages.observability.retention import ReportRetentionPolicy


def _make_context(
    *,
    events: list[OperationalEvent] | None = None,
    env_meta: dict[str, object] | None = None,
) -> DiagnosticContext:
    now = datetime.now(UTC)
    sample_events = events or [
        OperationalEvent(
            event_name="ORDER_SUBMITTED",
            occurred_at=now - timedelta(seconds=10),
            reason_code=None,
            fields=(("broker", "DERIV"), ("symbol", "frxEURUSD"), ("amount_minor", 1000)),
        ),
        OperationalEvent(
            event_name="RISK_RESERVED",
            occurred_at=now - timedelta(seconds=9),
            reason_code="RESERVED",
            fields=(("exposure_minor", 1000),),
        ),
    ]
    return DiagnosticContext(
        app_version="1.0.0",
        python_version="3.13.5",
        os_name="Windows",
        os_release="11",
        os_version="10.0.22631",
        uptime_seconds=3600.0,
        environment_meta=env_meta or {"profile_name": "default", "database_exists": True},
        health_snapshot={
            "global_state": {"is_open": True, "reason_code": None},
            "scoped_states": {"DERIV:VRTC1001": {"is_open": True, "reason_code": None}},
        },
        risk_metrics={
            "global_exposure_minor_units": 1000,
            "global_max_exposure_minor_units": 50000,
            "consecutive_losses": 0,
            "risk_state": "NORMAL",
            "daily_realized_pnl_minor_units": 250,
            "reference_currency": "USD",
        },
        recent_events=tuple(sample_events),
        process_tree=(
            {"role": "SIMULATED_WORKER", "status": "READY"},
            {"role": "AUTH_AGENT", "status": "READY"},
        ),
    )


def test_diagnostic_bundle_builder_creates_valid_zip_and_manifest(tmp_path: Path) -> None:
    builder = DiagnosticBundleBuilder()
    context = _make_context()
    output_dir = tmp_path / "diagnostics"

    result = builder.build_bundle(output_dir, context)

    assert result.zip_path.exists()
    assert result.zip_path.suffix == ".zip"
    assert result.file_count == 5
    assert len(result.sha256_hash) == 64

    # Verify calculated hash matches actual file hash
    actual_hash = hashlib.sha256(result.zip_path.read_bytes()).hexdigest()
    assert actual_hash == result.sha256_hash
    assert result.file_size_bytes == result.zip_path.stat().st_size

    # Verify ZIP structure and contained files
    with zipfile.ZipFile(result.zip_path, "r") as zf:
        namelist = set(zf.namelist())
        expected_files = {
            "environment.json",
            "health_gates.json",
            "manifest.json",
            "recent_events.json",
            "risk_summary.json",
        }
        assert namelist == expected_files

        # Verify manifest content
        manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest_data["app_version"] == "1.0.0"
        assert "generated_at_utc" in manifest_data
        assert set(manifest_data["files"]) == expected_files - {"manifest.json"}

        # Verify environment data
        env_data = json.loads(zf.read("environment.json").decode("utf-8"))
        assert env_data["os_name"] == "Windows"
        assert env_data["python_version"] == "3.13.5"
        assert len(env_data["process_tree"]) == 2

        # Verify risk data
        risk_data = json.loads(zf.read("risk_summary.json").decode("utf-8"))
        assert risk_data["global_exposure_minor_units"] == 1000
        assert risk_data["risk_state"] == "NORMAL"

        # Verify events data
        events_data = json.loads(zf.read("recent_events.json").decode("utf-8"))
        assert len(events_data["events"]) == 2
        assert events_data["events"][0]["event_name"] == "ORDER_SUBMITTED"

    # Verify no temporary directories or leftover files remain
    leftovers = [p for p in output_dir.iterdir() if p.name.startswith(".tmp")]
    assert len(leftovers) == 0


def test_diagnostic_bundle_builder_fails_closed_on_secret_leak(tmp_path: Path) -> None:
    builder = DiagnosticBundleBuilder()
    output_dir = tmp_path / "diagnostics"

    # Inject a sensitive fake secret in the operational event fields
    leaked_event = OperationalEvent(
        event_name="AUTH_ATTEMPT",
        occurred_at=datetime.now(UTC),
        reason_code="AUTH_FAIL",
        fields=(
            (
                "token_header",
                "Authorization: " + "Bearer " + "my" + "-secret-token-123456789",
            ),
        ),
    )
    context = _make_context(events=[leaked_event])

    with pytest.raises(DiagnosticSecurityViolationError) as exc_info:
        builder.build_bundle(output_dir, context)

    assert exc_info.value.reason_code == "DIAGNOSTIC_SECURITY_VIOLATION"

    # Verify no zip or temp files were published to output_dir
    published_zips = list(output_dir.glob("*.zip"))
    assert len(published_zips) == 0

    leftover_temps = list(output_dir.glob(".tmp*"))
    assert len(leftover_temps) == 0


def test_diagnostic_bundle_builder_enforces_bounded_retention(tmp_path: Path) -> None:
    policy = ReportRetentionPolicy(
        max_reports=3,
        max_total_bytes=10 * 1024 * 1024,
        file_pattern="diagnostic_bundle_*.zip",
    )
    builder = DiagnosticBundleBuilder(retention_policy=policy)
    output_dir = tmp_path / "diagnostics"

    generated_paths: list[Path] = []
    for i in range(5):
        context = _make_context(env_meta={"run_index": i})
        res = builder.build_bundle(output_dir, context)
        generated_paths.append(res.zip_path)

    # Exactly 3 newest bundles should remain
    existing_zips = sorted(output_dir.glob("diagnostic_bundle_*.zip"))
    assert len(existing_zips) == 3

    # Oldest 2 should be pruned
    assert not generated_paths[0].exists()
    assert not generated_paths[1].exists()
    assert generated_paths[2].exists()
    assert generated_paths[3].exists()
    assert generated_paths[4].exists()
