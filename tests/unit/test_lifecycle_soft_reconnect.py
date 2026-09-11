from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.core.execution_state import TransportSupervisor
from apps.core.health import HealthGate
from apps.core.lifecycle_service import CoreLifecycleService, CoreServiceState
from apps.core.worker_client import DeliveryCertainty, WorkerDispatchError
from packages.domain.market import BrokerClockSnapshot
from packages.observability import InMemoryEventSink
from packages.protocol import ProtocolErrorCode


class _Stop:
    @staticmethod
    def is_set() -> bool:
        return False

    @staticmethod
    def wait(_seconds: float) -> bool:
        return False


def _recovery_service() -> CoreLifecycleService:
    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._state = CoreServiceState.READY
    service._transport_supervisor = TransportSupervisor(initially_armed=True)
    service._iqoption_bot_reason = "TRANSPORT_DOWN"
    service._iqoption_session_invalidated = True
    service._iqoption_recovery_stop = _Stop()
    service._runtime = SimpleNamespace(
        event_sink=InMemoryEventSink(),
        reader=SimpleNamespace(list_nonterminal_orders=lambda: []),
    )
    service._iqoption_connection_safety = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(quarantine_active=False, retry_after_seconds=0)
    )
    return service


def test_ipc_loss_respawns_cached_session_without_http_login() -> None:
    service = _recovery_service()
    calls: list[bool] = []

    class DeadClient:
        is_ready = True

        @staticmethod
        def iqoption_reconnect_session() -> None:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_CONNECTION_LOST,
                DeliveryCertainty.NOT_SENT,
                "lost",
            )

    service._iqoption = SimpleNamespace(client=DeadClient())

    def connect(
        _mode: str,
        *,
        source: str,
        cached_only: bool,
    ) -> tuple[bool, bool, str]:
        assert source == "auto"
        calls.append(cached_only)
        return True, True, "IQOPTION_PRACTICE_CONNECTED"

    service.connect_iqoption_selected_account = connect  # type: ignore[method-assign]

    connected, _, _, _ = service._try_reconnect_iqoption_websocket()

    assert connected
    assert calls == [True]


def test_balance_timeout_after_reconnect_does_not_reconnect_healthy_transport() -> None:
    service = _recovery_service()
    service._runtime.health_gate = HealthGate()
    reconnects: list[str] = []
    transport_up: list[bool] = []
    monitor_starts: list[object] = []
    now = datetime.now(UTC)

    class Client:
        is_ready = True

        @staticmethod
        def iqoption_reconnect_session() -> None:
            reconnects.append("reconnect")

        @staticmethod
        def broker_balance() -> None:
            raise WorkerDispatchError(
                ProtocolErrorCode.IQOPTION_REQUEST_TIMEOUT,
                DeliveryCertainty.NOT_SENT,
                "balance timeout",
            )

        @staticmethod
        def broker_clock() -> BrokerClockSnapshot:
            return BrokerClockSnapshot(
                int(now.timestamp()),
                now,
                0.1,
                Decimal(0),
                source_age_seconds=0.1,
                connection_generation=2,
                sample_sequence=3,
            )

    service._iqoption = SimpleNamespace(client=Client())
    service._iqoption_auto_trader = SimpleNamespace(
        on_transport_up=lambda: transport_up.append(True)
    )
    service._start_iqoption_balance_monitor = lambda _runtime, _supervisor, balance: (
        monitor_starts.append(balance)
    )
    service._respawn_iqoption_from_cached_session = lambda: pytest.fail(
        "balance-only timeout must not respawn the worker"
    )

    connected, reason, retry_same, retry_after = service._try_reconnect_iqoption_websocket()

    assert connected is True
    assert reason == "IQOPTION_WEBSOCKET_SESSION_REUSED"
    assert retry_same is False
    assert retry_after == 0.0
    assert reconnects == ["reconnect"]
    assert monitor_starts == [None]
    assert transport_up == [True]
    assert service._iqoption_clock is not None
    gate = service._runtime.health_gate.state_for("IQ_OPTION", "IQOPTION_PRACTICE")
    assert gate.reason_code == "IQOPTION_BALANCE_STALE"


@pytest.mark.parametrize("cached_reason", ["IQOPTION_AUTH_FAILED", "IQOPTION_SSID_UNAVAILABLE"])
def test_cached_session_failure_allows_exactly_one_http_fallback(
    cached_reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _recovery_service()
    service._iqoption = None
    calls: list[bool] = []

    def connect(
        _mode: str,
        *,
        source: str,
        cached_only: bool,
    ) -> tuple[bool, bool, str]:
        assert source == "auto"
        calls.append(cached_only)
        if cached_only:
            return False, False, cached_reason
        service._iqoption_session_invalidated = False
        return True, True, "IQOPTION_PRACTICE_CONNECTED"

    service.connect_iqoption_selected_account = connect  # type: ignore[method-assign]
    monkeypatch.setattr("apps.core.lifecycle_service._IQOPTION_RECOVERY_DELAYS_SECONDS", (0.0,))

    service._iqoption_recovery_loop()

    assert calls == [True, False]
    assert calls.count(False) == 1
