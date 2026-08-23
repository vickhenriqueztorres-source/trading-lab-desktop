from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

from apps.core.broker_shadow_session import BrokerShadowSeriesSnapshot, BrokerShadowSessionSnapshot
from apps.core.broker_shadow_soak import (
    BrokerShadowSoakLimits,
    BrokerShadowSoakRunner,
    BrokerShadowSoakSession,
    BrokerShadowSoakSnapshot,
    BrokerShadowTemporalSoakMatrixReport,
    BrokerShadowTemporalSoakMatrixRunner,
    BrokerShadowTemporalSoakOutcome,
    BrokerShadowTemporalSoakPlan,
    BrokerShadowTemporalSoakRunner,
    BrokerShadowTemporalSoakScenario,
)
from apps.core.shadow_runtime import ShadowServiceState
from apps.core.soak_profiles import (
    FaultObservation,
    FaultObservationState,
    FaultPreset,
    FaultSchedule,
    FaultType,
    SoakProfile,
    default_fault_preset,
    fault_schedule_for,
    profile_settings,
)
from apps.core.worker_supervisor import WorkerHealthState
from packages.domain.models import Broker
from packages.market_pipeline import LiveAggregationResult, MarketSeriesId
from packages.observability import (
    AtomicJsonWriteError,
    ReportRetentionError,
    ReportRetentionManager,
    ReportRetentionPolicy,
    atomic_write_json,
)
from packages.security import SecretScanError, SecretScanner

SOAK_MATRIX_OPT_IN_ENV = "DUALTRADE_RUN_SOAK_MATRIX"
MIN_REPORTS = 1
MAX_REPORTS = 100
MIN_CYCLES = 10
MAX_CYCLES = 10_000
MIN_DURATION_SECONDS = 0.1
MAX_DURATION_SECONDS = 3_600.0
DEFAULT_OUTPUT_DIR = Path("reports") / "soak"


@dataclass(frozen=True, slots=True)
class SoakCliConfig:
    output_dir: Path = DEFAULT_OUTPUT_DIR
    max_reports: int = 10
    max_cycles: int = 100
    duration_seconds: float = 5.0
    profile: SoakProfile = SoakProfile.STANDARD
    fault_preset: FaultPreset = FaultPreset.NONE
    quiet: bool = False

    def __post_init__(self) -> None:
        if not MIN_REPORTS <= self.max_reports <= MAX_REPORTS:
            raise ValueError("soak CLI report limit is outside the bounded range")
        if not MIN_CYCLES <= self.max_cycles <= MAX_CYCLES:
            raise ValueError("soak CLI cycle limit is outside the bounded range")
        if not MIN_DURATION_SECONDS <= self.duration_seconds <= MAX_DURATION_SECONDS:
            raise ValueError("soak CLI duration is outside the bounded range")
        resolved = self.output_dir.expanduser().resolve()
        if resolved == Path(resolved.anchor) or self.output_dir.is_symlink():
            raise ValueError("soak CLI output directory is unsafe")
        if self.output_dir.exists() and not self.output_dir.is_dir():
            raise ValueError("soak CLI output path must be a directory")

    @property
    def resolved_output_dir(self) -> Path:
        return self.output_dir.expanduser().resolve()

    @property
    def fault_schedule(self) -> FaultSchedule:
        return fault_schedule_for(self.fault_preset, self.max_cycles)


@dataclass(frozen=True, slots=True)
class ProfiledSoakMatrixReport:
    matrix_report: BrokerShadowTemporalSoakMatrixReport
    profile: SoakProfile
    fault_preset: FaultPreset
    fault_schedule: FaultSchedule
    fault_observations: tuple[FaultObservation, ...]

    @property
    def outcome(self) -> BrokerShadowTemporalSoakOutcome:
        return self.matrix_report.outcome

    @property
    def results(self) -> tuple[object, ...]:
        return self.matrix_report.results

    @property
    def passed_scenario_count(self) -> int:
        return self.matrix_report.passed_scenario_count

    @property
    def failed_scenario_count(self) -> int:
        return self.matrix_report.failed_scenario_count

    def to_payload(self) -> dict[str, object]:
        payload = self.matrix_report.to_payload()
        payload.update(
            {
                "execution_profile": self.profile.value,
                "fault_preset": self.fault_preset.value,
                "fault_schedule": self.fault_schedule.to_payload(),
                "fault_summary": {
                    "injected_count": sum(
                        item.state is FaultObservationState.INJECTED
                        for item in self.fault_observations
                    ),
                    "observed_count": sum(
                        item.state is FaultObservationState.OBSERVED
                        for item in self.fault_observations
                    ),
                    "recovered_count": sum(
                        item.state is FaultObservationState.RECOVERED
                        for item in self.fault_observations
                    ),
                    "events": [item.to_payload() for item in self.fault_observations],
                },
            }
        )
        return payload


class _FaultRecorder:
    def __init__(self) -> None:
        self._observations: list[FaultObservation] = []

    def record(self, observation: FaultObservation) -> None:
        self._observations.append(observation)

    def snapshot(self) -> tuple[FaultObservation, ...]:
        return tuple(self._observations)


class _ProfiledSoakMatrixRunner:
    def __init__(
        self,
        matrix: BrokerShadowTemporalSoakMatrixRunner,
        config: SoakCliConfig,
        recorder: _FaultRecorder,
    ) -> None:
        self._matrix = matrix
        self._config = config
        self._recorder = recorder

    def run(self) -> ProfiledSoakMatrixReport:
        return ProfiledSoakMatrixReport(
            matrix_report=self._matrix.run(),
            profile=self._config.profile,
            fault_preset=self._config.fault_preset,
            fault_schedule=self._config.fault_schedule,
            fault_observations=self._recorder.snapshot(),
        )


class _StableArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        self.print_usage(sys.stderr)
        self.exit(2, "SOAK_CLI_ARGUMENT_INVALID\n")


class _LocalReadOnlySoakSession:
    def __init__(self) -> None:
        self._state = ShadowServiceState.STOPPED
        self._starts = 0
        self._polls = 0
        self._recoveries = 0
        self._poll_failures = 0
        self._pending_backpressure = False
        self._series_id = MarketSeriesId(
            Broker.DERIV,
            "frxEURUSD",
            "frxEURUSD",
            "OPTION",
            60,
        )

    @property
    def state(self) -> ShadowServiceState:
        return self._state

    def force_recovering(self) -> None:
        self._state = ShadowServiceState.RECOVERING

    def inject_backpressure(self) -> None:
        self._pending_backpressure = True

    def start(self) -> bool:
        self._starts += 1
        self._state = ShadowServiceState.RUNNING
        return True

    def poll_once(self, *, timeout: float) -> LiveAggregationResult | None:
        if timeout <= 0:
            raise ValueError("local read-only soak poll timeout must be positive")
        self._polls += 1
        if self._pending_backpressure:
            self._pending_backpressure = False
            self._poll_failures += 1
            raise RuntimeError("SOAK_FAULT_BACKPRESSURE_INJECTED")
        return None

    def recover(self) -> bool:
        self._recoveries += 1
        self._state = ShadowServiceState.RUNNING
        return True

    def shutdown(self) -> None:
        self._state = ShadowServiceState.STOPPED

    def snapshot(self) -> BrokerShadowSessionSnapshot:
        running = self._state is ShadowServiceState.RUNNING
        return BrokerShadowSessionSnapshot(
            broker=Broker.DERIV,
            state=self._state,
            worker_health=(WorkerHealthState.READY if running else WorkerHealthState.DISCONNECTED),
            registered_series=1,
            subscribed_series=int(running),
            start_attempts=self._starts,
            recovery_attempts=self._recoveries,
            poll_count=self._polls,
            poll_failures=self._poll_failures,
            elapsed_monotonic_seconds=0.0,
            router=None,
            series=(
                BrokerShadowSeriesSnapshot(
                    series_id=self._series_id,
                    subscribed=running,
                    poll_count=self._polls,
                    poll_failures=0,
                    live_dispatch_lag_ms_max=0,
                ),
            ),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = _StableArgumentParser(description="DualTrade local read-only soak matrix")
    parser.add_argument(
        "--run-soak-matrix",
        action="store_true",
        help="explicitly opt in to the local read-only soak matrix",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-reports", type=int, default=10)
    parser.add_argument("--max-cycles", type=int)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument(
        "--profile",
        choices=tuple(profile.value for profile in SoakProfile),
        default=SoakProfile.STANDARD.value,
    )
    parser.add_argument(
        "--fault-preset",
        choices=tuple(preset.value for preset in FaultPreset),
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.run_soak_matrix and os.environ.get(SOAK_MATRIX_OPT_IN_ENV) != "1":
        print(
            "SOAK_CLI_OPT_IN_REQUIRED: use --run-soak-matrix or DUALTRADE_RUN_SOAK_MATRIX=1",
            file=sys.stderr,
        )
        return 2
    try:
        profile = SoakProfile(arguments.profile)
        settings = profile_settings(profile)
        fault_preset = (
            default_fault_preset(profile)
            if arguments.fault_preset is None
            else FaultPreset(arguments.fault_preset)
        )
        config = SoakCliConfig(
            output_dir=arguments.output_dir,
            max_reports=arguments.max_reports,
            max_cycles=(
                settings.max_cycles if arguments.max_cycles is None else arguments.max_cycles
            ),
            duration_seconds=(
                settings.duration_seconds
                if arguments.duration_seconds is None
                else arguments.duration_seconds
            ),
            profile=profile,
            fault_preset=fault_preset,
            quiet=arguments.quiet,
        )
    except ValueError:
        print("SOAK_CLI_ARGUMENT_INVALID", file=sys.stderr)
        return 2
    if not config.quiet:
        print(
            "SOAK_CLI_STARTED mode=DECISION_ONLY dispatch=false "
            f"profile={config.profile.value} fault_preset={config.fault_preset.value} "
            f"max_cycles={config.max_cycles} duration_seconds={config.duration_seconds:g}"
        )
    try:
        matrix = _build_matrix(config)
        report = matrix.run()
        output_dir = config.resolved_output_dir
        output_path = _next_report_path(output_dir, _utc_now(), report.outcome.value)
        payload = report.to_payload()
        serialized = json.dumps(payload, allow_nan=False, sort_keys=True)
        if SecretScanner().scan_text(serialized):
            raise SecretScanError("SECRET_SCAN_MATCH_DETECTED")
        atomic_write_json(output_path, payload)
        retention = ReportRetentionManager().enforce_retention(
            output_dir,
            ReportRetentionPolicy(max_reports=config.max_reports),
        )
    except (AtomicJsonWriteError, ReportRetentionError, SecretScanError, OSError, ValueError):
        print("SOAK_CLI_OPERATION_FAILED", file=sys.stderr)
        return 1
    except Exception:
        print("SOAK_CLI_OPERATION_FAILED", file=sys.stderr)
        return 1
    print(
        "SOAK_CLI_RESULT "
        f"outcome={report.outcome.value} "
        f"scenarios={len(report.results)} "
        f"passed={report.passed_scenario_count} "
        f"failed={report.failed_scenario_count} "
        f"report={output_path.name} "
        f"purged={retention.deleted_files} "
        f"profile={config.profile.value} "
        f"fault_preset={config.fault_preset.value} "
        f"faults={config.fault_schedule.total_faults}"
    )
    return 0 if report.outcome is BrokerShadowTemporalSoakOutcome.PASSED else 1


def _build_matrix(config: SoakCliConfig) -> _ProfiledSoakMatrixRunner:
    recorder = _FaultRecorder()
    matrix = BrokerShadowTemporalSoakMatrixRunner(
        (
            _build_scenario("baseline", config, recorder),
            _build_scenario(
                "cadence-125pct",
                config,
                recorder,
                pacing_multiplier=1.25,
                fault_types=frozenset({FaultType.BACKPRESSURE}),
            ),
            _build_scenario(
                "suspend-recovery",
                config,
                recorder,
                fault_types=frozenset({FaultType.SUSPENSION_GAP}),
            ),
            _build_scenario(
                "worker-loss-recovery",
                config,
                recorder,
                fault_types=frozenset({FaultType.WORKER_KILL}),
            ),
        ),
        maximum_scenarios=4,
    )
    return _ProfiledSoakMatrixRunner(matrix, config, recorder)


def _build_scenario(
    scenario_id: str,
    config: SoakCliConfig,
    recorder: _FaultRecorder,
    *,
    pacing_multiplier: float = 1.0,
    fault_types: frozenset[FaultType] = frozenset(),
) -> BrokerShadowTemporalSoakScenario:
    session = _LocalReadOnlySoakSession()
    scenario_faults = tuple(
        fault for fault in config.fault_schedule.events() if fault.fault_type in fault_types
    )

    def inject_faults(
        cycle: int,
        _session: BrokerShadowSoakSession,
    ) -> None:
        for fault in scenario_faults:
            if fault.cycle != cycle:
                continue
            if fault.fault_type is FaultType.BACKPRESSURE:
                session.inject_backpressure()
                reason_code = "SOAK_FAULT_BACKPRESSURE_INJECTED"
            elif fault.fault_type is FaultType.SUSPENSION_GAP:
                session.force_recovering()
                reason_code = "SOAK_FAULT_SUSPENSION_GAP_INJECTED"
            else:
                session.force_recovering()
                reason_code = "SOAK_FAULT_WORKER_LOSS_INJECTED"
            recorder.record(
                FaultObservation(
                    scenario_id=scenario_id,
                    cycle=cycle,
                    fault_type=fault.fault_type,
                    state=FaultObservationState.INJECTED,
                    reason_code=reason_code,
                )
            )

    interval = config.duration_seconds / config.max_cycles * pacing_multiplier

    def observe_and_pace(snapshot: BrokerShadowSoakSnapshot) -> None:
        for fault in scenario_faults:
            if fault.cycle != snapshot.cycles:
                continue
            if fault.fault_type is FaultType.BACKPRESSURE:
                state = FaultObservationState.OBSERVED
                reason_code = (
                    "SOAK_FAULT_BACKPRESSURE_OBSERVED"
                    if snapshot.poll_failures > 0
                    else "SOAK_FAULT_BACKPRESSURE_NOT_OBSERVED"
                )
            else:
                recovered = session.state is ShadowServiceState.RUNNING
                state = (
                    FaultObservationState.RECOVERED if recovered else FaultObservationState.OBSERVED
                )
                reason_code = (
                    "SOAK_FAULT_RECOVERY_CONFIRMED"
                    if recovered
                    else "SOAK_FAULT_RECOVERY_UNCONFIRMED"
                )
            recorder.record(
                FaultObservation(
                    scenario_id=scenario_id,
                    cycle=snapshot.cycles,
                    fault_type=fault.fault_type,
                    state=state,
                    reason_code=reason_code,
                )
            )
        time.sleep(interval)

    recovery_fault_count = sum(
        fault.fault_type in {FaultType.WORKER_KILL, FaultType.SUSPENSION_GAP}
        for fault in scenario_faults
    )
    backpressure_fault_count = sum(
        fault.fault_type is FaultType.BACKPRESSURE for fault in scenario_faults
    )

    bounded = BrokerShadowSoakRunner(
        session,
        limits=BrokerShadowSoakLimits(
            max_cycles=config.max_cycles,
            poll_timeout_seconds=min(0.05, max(0.001, interval)),
            max_recoveries=max(1, recovery_fault_count),
        ),
        before_cycle=inject_faults,
    )
    temporal = BrokerShadowTemporalSoakRunner(
        bounded,
        BrokerShadowTemporalSoakPlan(
            duration_seconds=config.duration_seconds,
            minimum_cycles=min(MIN_CYCLES, config.max_cycles),
            maximum_cycles=config.max_cycles,
            sample_every_cycles=max(1, config.max_cycles // 32),
            max_samples=min(profile_settings(config.profile).sample_limit, config.max_cycles),
            max_poll_failures=backpressure_fault_count,
            max_recovery_attempts=max(1, recovery_fault_count),
        ),
        after_cycle=observe_and_pace,
    )
    return BrokerShadowTemporalSoakScenario(scenario_id, temporal)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _next_report_path(output_dir: Path, observed_at: datetime, outcome: str) -> Path:
    timestamp = observed_at.astimezone(UTC).strftime("%Y%m%d_%H%M%S")
    primary = output_dir / f"soak_matrix_{timestamp}_{outcome}.json"
    if not primary.exists():
        return primary
    for collision in range(1, 1_000):
        candidate = output_dir / f"soak_matrix_{timestamp}_{collision:03d}_{outcome}.json"
        if not candidate.exists():
            return candidate
    raise OSError("soak CLI report filename collision limit exceeded")
