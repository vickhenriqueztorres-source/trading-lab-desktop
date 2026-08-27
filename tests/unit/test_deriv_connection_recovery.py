from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.core.lifecycle_service import CoreLifecycleService, CoreServiceState


class _OldTelemetry:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def stop(self) -> None:
        self._events.append("telemetry.stop")


class _OldSupervisor:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def shutdown(self, _grace: float) -> None:
        self._events.append("worker.shutdown")


class _Runtime:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.health_gate = object()

    def detach_deriv_worker(self) -> None:
        self._events.append("runtime.detach")

    def resume_new_entries(self) -> bool:
        self._events.append("runtime.resume")
        return True


def test_authenticated_recovery_replaces_worker_then_reattaches_financial_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class ReplacementSupervisor:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("worker.construct")

        def start(self) -> None:
            events.append("worker.start")

        def shutdown(self, _grace: float) -> None:
            events.append("replacement.shutdown")

    service = CoreLifecycleService(
        tmp_path,
        ("simulated", "deriv_read_only"),
        force_auth_simulation=True,
        deriv_transport="live-demo",
    )
    service._state = CoreServiceState.DEGRADED
    service._runtime = _Runtime(events)  # type: ignore[assignment]
    service._deriv = _OldSupervisor(events)  # type: ignore[assignment]
    service._deriv_telemetry = _OldTelemetry(events)  # type: ignore[assignment]
    service._safe_stop = False

    monkeypatch.setattr(
        "apps.core.lifecycle_service.ReadOnlyWorkerSupervisor",
        ReplacementSupervisor,
    )
    monkeypatch.setattr(
        service,
        "_start_deriv_telemetry",
        lambda _runtime, _supervisor: events.append("telemetry.start"),
    )
    monkeypatch.setattr(
        service,
        "_activate_deriv_financial_runtime",
        lambda _runtime, _supervisor: events.append("financial.attach"),
    )

    assert service._recover_deriv_connection_once() is True
    assert events == [
        "telemetry.stop",
        "runtime.detach",
        "worker.shutdown",
        "worker.construct",
        "worker.start",
        "telemetry.start",
        "financial.attach",
    ]
    assert service.state is CoreServiceState.READY
    assert service.safe_stop_active is True
    assert "runtime.resume" not in events
    assert service._restart_counts["DERIV_WORKER"] == 1


def test_recovery_loop_uses_bounded_backoff_until_success(tmp_path: Path) -> None:
    service = CoreLifecycleService(
        tmp_path,
        ("simulated", "deriv_read_only"),
        force_auth_simulation=True,
        deriv_transport="live-demo",
    )
    attempts: list[int] = []
    waits: list[float] = []

    class _StopEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, seconds: float) -> bool:
            waits.append(seconds)
            return False

    service._deriv_recovery_stop = _StopEvent()  # type: ignore[assignment]

    def recover() -> bool:
        attempts.append(len(attempts) + 1)
        return len(attempts) == 3

    service._recover_deriv_connection_once = recover  # type: ignore[method-assign]
    service._deriv_recovery_loop()

    assert attempts == [1, 2, 3]
    assert waits == [0.0, 1.0, 2.0]


def test_account_connection_retries_transient_worker_startup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class _Vault:
        def __init__(self, _path: Path) -> None:
            pass

        def load(self) -> object:
            return SimpleNamespace(account_type="demo")

    class _RetryEvent:
        def wait(self, seconds: float) -> bool:
            events.append(f"wait:{seconds}")
            return False

    class _ReplacementSupervisor:
        starts = 0

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("replacement.construct")

        def start(self) -> None:
            type(self).starts += 1
            events.append(f"replacement.start:{type(self).starts}")
            if type(self).starts == 1:
                raise RuntimeError("IPC_HANDSHAKE_TIMEOUT")

        def shutdown(self, _grace: float) -> None:
            events.append("replacement.shutdown")

    service = CoreLifecycleService(
        tmp_path,
        ("simulated", "deriv_read_only"),
        force_auth_simulation=True,
    )
    service._state = CoreServiceState.READY
    service._runtime = _Runtime(events)  # type: ignore[assignment]
    service._deriv = _OldSupervisor(events)  # type: ignore[assignment]
    service._deriv_telemetry = _OldTelemetry(events)  # type: ignore[assignment]
    service._deriv_recovery_stop = _RetryEvent()  # type: ignore[assignment]

    monkeypatch.setattr("apps.core.lifecycle_service.DerivCredentialVault", _Vault)
    monkeypatch.setattr(
        "apps.core.lifecycle_service.ReadOnlyWorkerSupervisor",
        _ReplacementSupervisor,
    )
    monkeypatch.setattr(
        service,
        "_stop_deriv_financial_runtime",
        lambda _runtime: events.append("financial.stop"),
    )
    monkeypatch.setattr(
        service,
        "_start_deriv_telemetry",
        lambda _runtime, _supervisor: events.append("telemetry.start"),
    )
    monkeypatch.setattr(
        service,
        "_activate_deriv_financial_runtime",
        lambda _runtime, _supervisor: events.append("financial.attach"),
    )

    accepted, reason = service.connect_deriv_selected_account()

    assert accepted is True
    assert reason == "DERIV_DEMO_CONNECTED"
    assert events.count("replacement.construct") == 2
    assert "replacement.start:1" in events
    assert "replacement.start:2" in events
    assert "wait:1.0" in events
    assert events[-2:] == ["telemetry.start", "financial.attach"]
