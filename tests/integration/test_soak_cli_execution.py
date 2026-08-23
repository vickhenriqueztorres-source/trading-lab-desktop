from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from packages.security import SecretScanner

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("DUALTRADE_RUN_SOAK_MATRIX", None)
    return subprocess.run(
        [sys.executable, "-m", "apps.core.soak_cli", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_soak_cli_real_subprocess_requires_opt_in() -> None:
    completed = _run_cli("--quiet")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "SOAK_CLI_OPT_IN_REQUIRED" in completed.stderr


def test_soak_cli_real_subprocess_publishes_redacted_report_and_rotates_fifo(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "soak-reports"
    output_dir.mkdir()
    oldest = output_dir / "soak_matrix_20260820_000001_PASSED.json"
    previous = output_dir / "soak_matrix_20260820_000002_PASSED.json"
    oldest.write_text("{}", encoding="utf-8")
    previous.write_text("{}", encoding="utf-8")
    os.utime(oldest, ns=(1_000, 1_000))
    os.utime(previous, ns=(2_000, 2_000))
    unrelated = output_dir / "operator-note.json"
    unrelated.write_text("{}", encoding="utf-8")

    completed = _run_cli(
        "--run-soak-matrix",
        "--output-dir",
        str(output_dir),
        "--max-reports",
        "2",
        "--max-cycles",
        "10",
        "--duration-seconds",
        "0.1",
        "--quiet",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.startswith("SOAK_CLI_RESULT outcome=PASSED scenarios=4")
    assert "purged=1" in completed.stdout
    assert str(tmp_path) not in completed.stdout
    reports = tuple(output_dir.glob("soak_matrix_*.json"))
    assert len(reports) == 2
    assert not oldest.exists()
    assert previous.exists()
    assert unrelated.exists()
    generated = next(path for path in reports if path != previous)
    payload = json.loads(generated.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["outcome"] == "PASSED"
    assert payload["scenario_count"] == 4
    assert payload["passed_scenario_count"] == 4
    assert payload["failed_scenario_count"] == 0
    assert payload["execution_profile"] == "standard"
    assert payload["fault_preset"] == "none"
    assert payload["fault_summary"]["events"] == []
    assert [item["scenario_id"] for item in payload["results"]] == [
        "baseline",
        "cadence-125pct",
        "suspend-recovery",
        "worker-loss-recovery",
    ]
    serialized = json.dumps(payload)
    for forbidden in (
        "ORDER_SUBMIT",
        "TradeIntent",
        "RiskReservation",
        "access_token",
        "refresh_token",
        "password",
        "cookie",
    ):
        assert forbidden not in serialized
    assert not tuple(output_dir.glob("*.tmp"))
    assert SecretScanner().scan_file(generated) == []


def test_soak_cli_real_subprocess_applies_profiled_fault_schedule(tmp_path: Path) -> None:
    output_dir = tmp_path / "profiled-soak"

    completed = _run_cli(
        "--run-soak-matrix",
        "--output-dir",
        str(output_dir),
        "--profile",
        "fast",
        "--fault-preset",
        "heavy_load",
        "--quiet",
    )

    assert completed.returncode == 0, completed.stderr
    assert "profile=fast fault_preset=heavy_load faults=7" in completed.stdout
    report_path = next(output_dir.glob("soak_matrix_*.json"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["execution_profile"] == "fast"
    assert payload["fault_preset"] == "heavy_load"
    assert payload["fault_summary"]["injected_count"] == 7
    assert payload["fault_summary"]["observed_count"] == 4
    assert payload["fault_summary"]["recovered_count"] == 3
    assert len(payload["fault_summary"]["events"]) == 14
    assert SecretScanner().scan_file(report_path) == []
