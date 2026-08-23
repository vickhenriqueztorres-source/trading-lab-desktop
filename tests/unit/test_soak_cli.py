from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

import apps.core.soak_cli_runtime as soak_cli
from apps.core.broker_shadow_soak import BrokerShadowTemporalSoakOutcome
from apps.core.soak_profiles import FaultPreset, SoakProfile


@dataclass(frozen=True, slots=True)
class FakeMatrixReport:
    outcome: BrokerShadowTemporalSoakOutcome

    @property
    def results(self) -> tuple[str, ...]:
        return ("baseline", "recovery")

    @property
    def passed_scenario_count(self) -> int:
        return 2 if self.outcome is BrokerShadowTemporalSoakOutcome.PASSED else 1

    @property
    def failed_scenario_count(self) -> int:
        return len(self.results) - self.passed_scenario_count

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "outcome": self.outcome.value,
            "scenario_count": len(self.results),
            "passed_scenario_count": self.passed_scenario_count,
            "failed_scenario_count": self.failed_scenario_count,
            "results": [{"scenario_id": item} for item in self.results],
        }


class FakeMatrixRunner:
    def __init__(self, outcome: BrokerShadowTemporalSoakOutcome) -> None:
        self._outcome = outcome

    def run(self) -> FakeMatrixReport:
        return FakeMatrixReport(self._outcome)


class UnsafeMatrixRunner:
    def run(self) -> FakeMatrixReport:
        report = FakeMatrixReport(BrokerShadowTemporalSoakOutcome.PASSED)
        unsafe_payload = report.to_payload()
        unsafe_payload["diagnostic"] = "Bearer " + "synthetic-secret-value-123"

        @dataclass(frozen=True, slots=True)
        class UnsafeReport(FakeMatrixReport):
            def to_payload(self) -> dict[str, object]:
                return unsafe_payload

        return UnsafeReport(BrokerShadowTemporalSoakOutcome.PASSED)


def _fixed_now() -> datetime:
    return datetime(2026, 8, 21, 12, 34, 56, tzinfo=UTC)


def test_soak_cli_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(soak_cli.SOAK_MATRIX_OPT_IN_ENV, raising=False)

    exit_code = soak_cli.main(["--quiet"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "SOAK_CLI_OPT_IN_REQUIRED" in captured.err
    assert "DUALTRADE_RUN_SOAK_MATRIX=1" in captured.err


@pytest.mark.parametrize(
    "arguments",
    (
        ("--max-reports", "0"),
        ("--max-reports", "101"),
        ("--max-cycles", "9"),
        ("--max-cycles", "10001"),
        ("--duration-seconds", "0"),
        ("--duration-seconds", "3600.1"),
    ),
)
def test_soak_cli_rejects_arguments_outside_bounded_limits(
    arguments: tuple[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = soak_cli.main(["--run-soak-matrix", *arguments])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "SOAK_CLI_ARGUMENT_INVALID\n"


def test_soak_cli_accepts_environment_opt_in_and_returns_zero_for_passed_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(soak_cli.SOAK_MATRIX_OPT_IN_ENV, "1")
    monkeypatch.setattr(
        soak_cli,
        "_build_matrix",
        lambda _config: FakeMatrixRunner(BrokerShadowTemporalSoakOutcome.PASSED),
    )
    monkeypatch.setattr(soak_cli, "_utc_now", _fixed_now)

    exit_code = soak_cli.main(["--output-dir", str(tmp_path), "--quiet"])

    captured = capsys.readouterr()
    report_path = tmp_path / "soak_matrix_20260821_123456_PASSED.json"
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.startswith("SOAK_CLI_RESULT outcome=PASSED")
    assert "SOAK_CLI_STARTED" not in captured.out
    assert json.loads(report_path.read_text(encoding="utf-8"))["outcome"] == "PASSED"


def test_soak_cli_returns_one_and_persists_failed_matrix_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(soak_cli.SOAK_MATRIX_OPT_IN_ENV, raising=False)
    monkeypatch.setattr(
        soak_cli,
        "_build_matrix",
        lambda _config: FakeMatrixRunner(BrokerShadowTemporalSoakOutcome.FAILED),
    )
    monkeypatch.setattr(soak_cli, "_utc_now", _fixed_now)

    exit_code = soak_cli.main(
        [
            "--run-soak-matrix",
            "--output-dir",
            str(tmp_path),
            "--max-reports",
            "1",
        ]
    )

    captured = capsys.readouterr()
    report_path = tmp_path / "soak_matrix_20260821_123456_FAILED.json"
    assert exit_code == 1
    assert captured.err == ""
    assert "SOAK_CLI_STARTED mode=DECISION_ONLY dispatch=false" in captured.out
    assert "SOAK_CLI_RESULT outcome=FAILED" in captured.out
    assert json.loads(report_path.read_text(encoding="utf-8"))["outcome"] == "FAILED"


def test_soak_cli_returns_one_without_raw_exception_when_operation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_build(_config: soak_cli.SoakCliConfig) -> FakeMatrixRunner:
        raise RuntimeError("raw external detail must not reach console")

    monkeypatch.setattr(soak_cli, "_build_matrix", fail_build)

    exit_code = soak_cli.main(["--run-soak-matrix", "--output-dir", str(tmp_path), "--quiet"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "SOAK_CLI_OPERATION_FAILED\n"
    assert "raw external detail" not in captured.err


def test_soak_cli_secret_scan_blocks_report_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(soak_cli, "_build_matrix", lambda _config: UnsafeMatrixRunner())

    exit_code = soak_cli.main(["--run-soak-matrix", "--output-dir", str(tmp_path), "--quiet"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "SOAK_CLI_OPERATION_FAILED\n"
    assert not tuple(tmp_path.glob("soak_matrix_*.json"))
    assert not tuple(tmp_path.glob("*.tmp"))


def test_soak_cli_uses_collision_suffix_without_overwriting_previous_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        soak_cli,
        "_build_matrix",
        lambda _config: FakeMatrixRunner(BrokerShadowTemporalSoakOutcome.PASSED),
    )
    monkeypatch.setattr(soak_cli, "_utc_now", _fixed_now)
    arguments = [
        "--run-soak-matrix",
        "--output-dir",
        str(tmp_path),
        "--max-reports",
        "2",
        "--quiet",
    ]

    assert soak_cli.main(arguments) == 0
    assert soak_cli.main(arguments) == 0

    names = sorted(path.name for path in tmp_path.glob("soak_matrix_*.json"))
    assert names == [
        "soak_matrix_20260821_123456_001_PASSED.json",
        "soak_matrix_20260821_123456_PASSED.json",
    ]


def test_soak_cli_resolves_profile_fault_preset_and_explicit_limit_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[soak_cli.SoakCliConfig] = []

    def build(config: soak_cli.SoakCliConfig) -> FakeMatrixRunner:
        observed.append(config)
        return FakeMatrixRunner(BrokerShadowTemporalSoakOutcome.PASSED)

    monkeypatch.setattr(soak_cli, "_build_matrix", build)
    monkeypatch.setattr(soak_cli, "_utc_now", _fixed_now)

    exit_code = soak_cli.main(
        [
            "--run-soak-matrix",
            "--output-dir",
            str(tmp_path),
            "--profile",
            "extended",
            "--fault-preset",
            "intermittent_crash",
            "--max-cycles",
            "10",
            "--duration-seconds",
            "0.1",
            "--quiet",
        ]
    )

    assert exit_code == 0
    assert len(observed) == 1
    assert observed[0].profile is SoakProfile.EXTENDED
    assert observed[0].fault_preset is FaultPreset.INTERMITTENT_CRASH
    assert observed[0].max_cycles == 10
    assert observed[0].duration_seconds == 0.1
