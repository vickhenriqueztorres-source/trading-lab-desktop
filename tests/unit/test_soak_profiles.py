from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.core.soak_cli_runtime import SoakCliConfig, _build_matrix
from apps.core.soak_profiles import (
    FaultPreset,
    FaultSchedule,
    FaultType,
    SoakProfile,
    default_fault_preset,
    fault_schedule_for,
    profile_settings,
)


def test_soak_profiles_are_explicit_and_bounded() -> None:
    assert profile_settings(SoakProfile.FAST).max_cycles == 10
    assert profile_settings(SoakProfile.STANDARD).duration_seconds == 5.0
    assert profile_settings(SoakProfile.EXTENDED).duration_seconds == 300.0
    assert profile_settings(SoakProfile.CHAOS).max_cycles == 1_000
    assert default_fault_preset(SoakProfile.STANDARD) is FaultPreset.NONE
    assert default_fault_preset(SoakProfile.CHAOS) is FaultPreset.HEAVY_LOAD


def test_fault_presets_produce_deterministic_sorted_schedules() -> None:
    schedule = fault_schedule_for(FaultPreset.HEAVY_LOAD, 100)

    assert schedule.kill_worker_at_cycles == (25, 75)
    assert schedule.inject_suspension_at_cycles == (50,)
    assert schedule.inject_backpressure_at_cycles == (20, 40, 60, 80)
    assert schedule.total_faults == 7
    assert tuple((event.cycle, event.fault_type) for event in schedule.events()) == (
        (20, FaultType.BACKPRESSURE),
        (25, FaultType.WORKER_KILL),
        (40, FaultType.BACKPRESSURE),
        (50, FaultType.SUSPENSION_GAP),
        (60, FaultType.BACKPRESSURE),
        (75, FaultType.WORKER_KILL),
        (80, FaultType.BACKPRESSURE),
    )


def test_fault_schedule_rejects_unbounded_or_nondeterministic_cycles() -> None:
    with pytest.raises(ValueError, match="sorted and unique"):
        FaultSchedule(kill_worker_at_cycles=(4, 2))
    with pytest.raises(ValueError, match="sorted and unique"):
        FaultSchedule(inject_suspension_at_cycles=(2, 2))
    with pytest.raises(ValueError, match="bounded range"):
        FaultSchedule(inject_backpressure_at_cycles=(0,))
    with pytest.raises(ValueError, match="bounded event limit"):
        FaultSchedule(kill_worker_at_cycles=tuple(range(1, 34)))


def test_profiled_matrix_records_every_injected_fault_and_recovery(tmp_path: Path) -> None:
    config = SoakCliConfig(
        output_dir=tmp_path,
        max_cycles=10,
        duration_seconds=0.1,
        profile=SoakProfile.FAST,
        fault_preset=FaultPreset.HEAVY_LOAD,
        quiet=True,
    )

    report = _build_matrix(config).run()
    payload = report.to_payload()
    serialized = json.dumps(payload)
    fault_summary = payload["fault_summary"]

    assert report.outcome.value == "PASSED"
    assert payload["execution_profile"] == "fast"
    assert payload["fault_preset"] == "heavy_load"
    assert isinstance(fault_summary, dict)
    assert fault_summary["injected_count"] == 7
    assert fault_summary["observed_count"] == 4
    assert fault_summary["recovered_count"] == 3
    assert len(fault_summary["events"]) == 14
    assert "SOAK_FAULT_WORKER_LOSS_INJECTED" in serialized
    assert "SOAK_FAULT_RECOVERY_CONFIRMED" in serialized
    assert "TradeIntent" not in serialized
    assert "RiskReservation" not in serialized
    assert "ORDER_SUBMIT" not in serialized
