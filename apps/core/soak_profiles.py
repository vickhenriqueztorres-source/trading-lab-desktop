from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

MAX_SCHEDULED_FAULTS = 32
MAX_SCHEDULE_CYCLE = 10_000


class SoakProfile(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    EXTENDED = "extended"
    CHAOS = "chaos"


class FaultPreset(StrEnum):
    NONE = "none"
    INTERMITTENT_CRASH = "intermittent_crash"
    SLEEP_RESUME_GAP = "sleep_resume_gap"
    HEAVY_LOAD = "heavy_load"


class FaultType(StrEnum):
    WORKER_KILL = "WORKER_KILL"
    SUSPENSION_GAP = "SUSPENSION_GAP"
    BACKPRESSURE = "BACKPRESSURE"


class FaultObservationState(StrEnum):
    INJECTED = "INJECTED"
    OBSERVED = "OBSERVED"
    RECOVERED = "RECOVERED"


@dataclass(frozen=True, slots=True)
class SoakProfileSettings:
    duration_seconds: float
    max_cycles: int
    sample_limit: int

    def __post_init__(self) -> None:
        if not 0.1 <= self.duration_seconds <= 3_600.0:
            raise ValueError("soak profile duration is outside the bounded range")
        if not 10 <= self.max_cycles <= MAX_SCHEDULE_CYCLE:
            raise ValueError("soak profile cycles are outside the bounded range")
        if not 1 <= self.sample_limit <= 256:
            raise ValueError("soak profile sample limit is outside the bounded range")


PROFILE_SETTINGS: dict[SoakProfile, SoakProfileSettings] = {
    SoakProfile.FAST: SoakProfileSettings(0.1, 10, 16),
    SoakProfile.STANDARD: SoakProfileSettings(5.0, 100, 64),
    SoakProfile.EXTENDED: SoakProfileSettings(300.0, 6_000, 128),
    SoakProfile.CHAOS: SoakProfileSettings(30.0, 1_000, 128),
}


@dataclass(frozen=True, slots=True)
class ScheduledFault:
    cycle: int
    fault_type: FaultType


@dataclass(frozen=True, slots=True)
class FaultSchedule:
    kill_worker_at_cycles: tuple[int, ...] = ()
    inject_suspension_at_cycles: tuple[int, ...] = ()
    inject_backpressure_at_cycles: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        total = 0
        for cycles in (
            self.kill_worker_at_cycles,
            self.inject_suspension_at_cycles,
            self.inject_backpressure_at_cycles,
        ):
            if tuple(sorted(set(cycles))) != cycles:
                raise ValueError("fault schedule cycles must be sorted and unique")
            if any(cycle < 1 or cycle > MAX_SCHEDULE_CYCLE for cycle in cycles):
                raise ValueError("fault schedule cycle is outside the bounded range")
            total += len(cycles)
        if total > MAX_SCHEDULED_FAULTS:
            raise ValueError("fault schedule exceeds the bounded event limit")

    @property
    def total_faults(self) -> int:
        return (
            len(self.kill_worker_at_cycles)
            + len(self.inject_suspension_at_cycles)
            + len(self.inject_backpressure_at_cycles)
        )

    def events(self) -> tuple[ScheduledFault, ...]:
        events = (
            *(ScheduledFault(cycle, FaultType.WORKER_KILL) for cycle in self.kill_worker_at_cycles),
            *(
                ScheduledFault(cycle, FaultType.SUSPENSION_GAP)
                for cycle in self.inject_suspension_at_cycles
            ),
            *(
                ScheduledFault(cycle, FaultType.BACKPRESSURE)
                for cycle in self.inject_backpressure_at_cycles
            ),
        )
        return tuple(sorted(events, key=lambda event: (event.cycle, event.fault_type.value)))

    def events_for(self, fault_type: FaultType) -> tuple[ScheduledFault, ...]:
        return tuple(event for event in self.events() if event.fault_type is fault_type)

    def to_payload(self) -> dict[str, object]:
        return {
            "kill_worker_at_cycles": list(self.kill_worker_at_cycles),
            "inject_suspension_at_cycles": list(self.inject_suspension_at_cycles),
            "inject_backpressure_at_cycles": list(self.inject_backpressure_at_cycles),
            "total_faults": self.total_faults,
        }


@dataclass(frozen=True, slots=True)
class FaultObservation:
    scenario_id: str
    cycle: int
    fault_type: FaultType
    state: FaultObservationState
    reason_code: str

    def __post_init__(self) -> None:
        if not self.scenario_id or self.cycle <= 0 or not self.reason_code:
            raise ValueError("fault observation requires scenario, cycle and reason code")

    def to_payload(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "cycle": self.cycle,
            "fault_type": self.fault_type.value,
            "state": self.state.value,
            "reason_code": self.reason_code,
        }


def profile_settings(profile: SoakProfile) -> SoakProfileSettings:
    return PROFILE_SETTINGS[profile]


def default_fault_preset(profile: SoakProfile) -> FaultPreset:
    if profile is SoakProfile.CHAOS:
        return FaultPreset.HEAVY_LOAD
    return FaultPreset.NONE


def fault_schedule_for(preset: FaultPreset, max_cycles: int) -> FaultSchedule:
    if not 10 <= max_cycles <= MAX_SCHEDULE_CYCLE:
        raise ValueError("fault preset cycle horizon is outside the bounded range")

    def cycle_at(numerator: int, denominator: int) -> int:
        return max(1, min(max_cycles, max_cycles * numerator // denominator))

    if preset is FaultPreset.NONE:
        return FaultSchedule()
    if preset is FaultPreset.INTERMITTENT_CRASH:
        return FaultSchedule(
            kill_worker_at_cycles=(cycle_at(1, 3), cycle_at(2, 3)),
        )
    if preset is FaultPreset.SLEEP_RESUME_GAP:
        return FaultSchedule(inject_suspension_at_cycles=(cycle_at(1, 2),))
    return FaultSchedule(
        kill_worker_at_cycles=(cycle_at(1, 4), cycle_at(3, 4)),
        inject_suspension_at_cycles=(cycle_at(1, 2),),
        inject_backpressure_at_cycles=tuple(cycle_at(index, 5) for index in range(1, 5)),
    )
