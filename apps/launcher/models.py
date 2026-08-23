from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class LauncherLifecycleState(StrEnum):
    UNINITIALIZED = "UNINITIALIZED"
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class ManagedProcessRole(StrEnum):
    AUTH_AGENT = "AUTH_AGENT"
    CORE = "CORE"
    SIMULATED_WORKER = "SIMULATED_WORKER"
    DERIV_WORKER = "DERIV_WORKER"
    IQOPTION_WORKER = "IQOPTION_WORKER"
    UI = "UI"


@dataclass(frozen=True, slots=True)
class ProcessStatusSnapshot:
    role: ManagedProcessRole
    pid: int | None
    is_alive: bool
    exit_code: int | None
    state: str
    restarts_count: int

    def __post_init__(self) -> None:
        if self.pid is not None and self.pid <= 0:
            raise ValueError("process pid must be positive")
        if self.is_alive != (self.pid is not None and self.exit_code is None):
            raise ValueError("process liveness is inconsistent")
        if not self.state or len(self.state) > 64:
            raise ValueError("process state is invalid")
        if self.restarts_count < 0:
            raise ValueError("restart count cannot be negative")


@dataclass(frozen=True, slots=True)
class LauncherSnapshot:
    overall_state: LauncherLifecycleState
    profile_dir: str
    processes: Mapping[ManagedProcessRole, ProcessStatusSnapshot]
    uptime_seconds: float

    def __post_init__(self) -> None:
        if not self.profile_dir or "\x00" in self.profile_dir:
            raise ValueError("profile directory is invalid")
        if self.uptime_seconds < 0:
            raise ValueError("uptime cannot be negative")
        copied = dict(self.processes)
        if set(copied) != set(ManagedProcessRole):
            raise ValueError("launcher snapshot must contain every managed role")
        if any(role is not item.role for role, item in copied.items()):
            raise ValueError("process snapshot role key does not match value")
        object.__setattr__(self, "processes", MappingProxyType(copied))
