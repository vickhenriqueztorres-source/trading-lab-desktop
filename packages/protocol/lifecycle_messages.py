from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.security import SecretValue

_HEX_CHARS = 64
_MAX_STATE_CHARS = 64
_MAX_PROCESSES = 6
_PROCESS_ROLES = frozenset(
    {"AUTH_AGENT", "CORE", "SIMULATED_WORKER", "DERIV_WORKER", "IQOPTION_WORKER", "UI"}
)
_RESTARTABLE_ROLES = frozenset({"AUTH_AGENT", "DERIV_WORKER", "IQOPTION_WORKER"})


class LifecycleHandshakeStatus(StrEnum):
    OK = "OK"
    DENIED = "DENIED"


def _invalid() -> ProtocolError:
    return ProtocolError(
        ProtocolErrorCode.LIFECYCLE_IPC_INVALID_MESSAGE,
        "lifecycle IPC payload is invalid",
    )


def _exact(payload: Mapping[str, object], fields: set[str]) -> None:
    if set(payload) != fields:
        raise _invalid()


def _string(payload: Mapping[str, object], field: str, maximum: int = 128) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise _invalid()
    return value.strip()


def _optional_string(payload: Mapping[str, object], field: str, maximum: int = 128) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise _invalid()
    return value.strip()


def _hex(value: str) -> str:
    if len(value) != _HEX_CHARS:
        raise _invalid()
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise _invalid() from exc
    return value


@dataclass(frozen=True, slots=True, repr=False)
class LifecycleHandshakeRequest:
    session_token: SecretValue
    client_nonce: str
    client_version: str

    def to_payload(self) -> dict[str, object]:
        return {
            "client_nonce": self.client_nonce,
            "client_version": self.client_version,
            "session_token": self.session_token.reveal_text(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> LifecycleHandshakeRequest:
        _exact(payload, {"client_nonce", "client_version", "session_token"})
        return cls(
            SecretValue.from_text(_hex(_string(payload, "session_token", _HEX_CHARS))),
            _hex(_string(payload, "client_nonce", _HEX_CHARS)),
            _string(payload, "client_version", 32),
        )

    def __repr__(self) -> str:
        return "LifecycleHandshakeRequest(<redacted>)"


@dataclass(frozen=True, slots=True)
class LifecycleHandshakeResponse:
    status: LifecycleHandshakeStatus
    core_version: str
    server_nonce: str | None
    server_proof: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "core_version": self.core_version,
            "server_nonce": self.server_nonce,
            "server_proof": self.server_proof,
            "status": self.status.value,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> LifecycleHandshakeResponse:
        _exact(payload, {"core_version", "server_nonce", "server_proof", "status"})
        try:
            status = LifecycleHandshakeStatus(_string(payload, "status", 16))
        except ValueError as exc:
            raise _invalid() from exc
        core_version = _string(payload, "core_version", 32)
        nonce = _optional_string(payload, "server_nonce", _HEX_CHARS)
        proof = _optional_string(payload, "server_proof", _HEX_CHARS)
        if status is LifecycleHandshakeStatus.OK:
            if nonce is None or proof is None:
                raise _invalid()
            _hex(nonce)
            _hex(proof)
        elif nonce is not None or proof is not None:
            raise _invalid()
        return cls(status, core_version, nonce, proof)


@dataclass(frozen=True, slots=True)
class LifecycleProcessStatus:
    role: str
    pid: int | None
    is_alive: bool
    exit_code: int | None
    state: str
    restarts_count: int

    def __post_init__(self) -> None:
        if self.role not in _PROCESS_ROLES:
            raise ValueError("unsupported lifecycle process role")
        if self.pid is not None and self.pid <= 0:
            raise ValueError("process pid must be positive")
        if self.is_alive != (self.pid is not None and self.exit_code is None):
            raise ValueError("process liveness fields are inconsistent")
        if not self.state or len(self.state) > _MAX_STATE_CHARS:
            raise ValueError("process state is invalid")
        if self.restarts_count < 0:
            raise ValueError("restart count cannot be negative")

    def to_payload(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "is_alive": self.is_alive,
            "pid": self.pid,
            "restarts_count": self.restarts_count,
            "role": self.role,
            "state": self.state,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> LifecycleProcessStatus:
        _exact(payload, {"exit_code", "is_alive", "pid", "restarts_count", "role", "state"})
        pid = payload.get("pid")
        exit_code = payload.get("exit_code")
        alive = payload.get("is_alive")
        restarts = payload.get("restarts_count")
        if (
            (pid is not None and (type(pid) is not int or pid <= 0))
            or (exit_code is not None and type(exit_code) is not int)
            or not isinstance(alive, bool)
            or type(restarts) is not int
            or restarts < 0
        ):
            raise _invalid()
        try:
            return cls(
                role=_string(payload, "role", 32),
                pid=pid,
                is_alive=alive,
                exit_code=exit_code,
                state=_string(payload, "state", _MAX_STATE_CHARS),
                restarts_count=restarts,
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class CoreLifecycleStatusResponse:
    core_state: str
    safe_stop_active: bool
    processes: tuple[LifecycleProcessStatus, ...]
    ui_shutdown_requested: bool = False

    def __post_init__(self) -> None:
        if not self.core_state or len(self.core_state) > _MAX_STATE_CHARS:
            raise ValueError("core lifecycle state is invalid")
        if not 1 <= len(self.processes) <= _MAX_PROCESSES:
            raise ValueError("lifecycle process count is outside bounds")
        roles = [item.role for item in self.processes]
        if len(roles) != len(set(roles)):
            raise ValueError("lifecycle process roles must be unique")

    def to_payload(self) -> dict[str, object]:
        return {
            "core_state": self.core_state,
            "processes": [item.to_payload() for item in self.processes],
            "safe_stop_active": self.safe_stop_active,
            "ui_shutdown_requested": self.ui_shutdown_requested,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> CoreLifecycleStatusResponse:
        _exact(
            payload,
            {"core_state", "processes", "safe_stop_active", "ui_shutdown_requested"},
        )
        safe_stop = payload.get("safe_stop_active")
        shutdown_requested = payload.get("ui_shutdown_requested")
        raw_processes = payload.get("processes")
        if (
            not isinstance(safe_stop, bool)
            or not isinstance(shutdown_requested, bool)
            or not isinstance(raw_processes, Sequence)
            or isinstance(raw_processes, str | bytes)
            or not 1 <= len(raw_processes) <= _MAX_PROCESSES
        ):
            raise _invalid()
        parsed: list[LifecycleProcessStatus] = []
        for item in raw_processes:
            if not isinstance(item, Mapping):
                raise _invalid()
            parsed.append(LifecycleProcessStatus.from_payload(item))
        try:
            return cls(
                _string(payload, "core_state", _MAX_STATE_CHARS),
                safe_stop,
                tuple(parsed),
                shutdown_requested,
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class CoreDrainRequest:
    timeout_milliseconds: int

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_milliseconds <= 10_000:
            raise ValueError("drain timeout is outside bounds")

    def to_payload(self) -> dict[str, object]:
        return {"timeout_milliseconds": self.timeout_milliseconds}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> CoreDrainRequest:
        _exact(payload, {"timeout_milliseconds"})
        value = payload.get("timeout_milliseconds")
        if type(value) is not int:
            raise _invalid()
        try:
            return cls(value)
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class CoreDrainResponse:
    drained: bool
    pending_events: int

    def __post_init__(self) -> None:
        if self.pending_events < 0:
            raise ValueError("pending event count cannot be negative")

    def to_payload(self) -> dict[str, object]:
        return {"drained": self.drained, "pending_events": self.pending_events}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> CoreDrainResponse:
        _exact(payload, {"drained", "pending_events"})
        drained = payload.get("drained")
        pending = payload.get("pending_events")
        if not isinstance(drained, bool) or type(pending) is not int or pending < 0:
            raise _invalid()
        return cls(drained, pending)


@dataclass(frozen=True, slots=True)
class CoreRestartComponentRequest:
    role: str

    def __post_init__(self) -> None:
        if self.role not in _RESTARTABLE_ROLES:
            raise ValueError("component is not restartable through lifecycle IPC")

    def to_payload(self) -> dict[str, object]:
        return {"role": self.role}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> CoreRestartComponentRequest:
        _exact(payload, {"role"})
        try:
            return cls(_string(payload, "role", 32))
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class CoreRestartComponentResponse:
    accepted: bool
    reason_code: str

    def to_payload(self) -> dict[str, object]:
        return {"accepted": self.accepted, "reason_code": self.reason_code}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> CoreRestartComponentResponse:
        _exact(payload, {"accepted", "reason_code"})
        accepted = payload.get("accepted")
        if not isinstance(accepted, bool):
            raise _invalid()
        return cls(accepted, _string(payload, "reason_code", 64))
