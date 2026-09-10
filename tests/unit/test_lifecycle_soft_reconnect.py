from types import SimpleNamespace

import pytest

from apps.core.execution_state import TransportSupervisor
from apps.core.lifecycle_service import CoreLifecycleService, CoreServiceState
from apps.core.worker_client import DeliveryCertainty, WorkerDispatchError
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
