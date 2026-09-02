from __future__ import annotations

import threading
from typing import cast

from apps.ui.controller import UiController
from apps.ui.ipc_client import UiIpcClient, UiIpcUnavailable
from packages.protocol import UiIqOptionLoginAck, UiProjectionSnapshot


class _TransientProjectionClient:
    def __init__(
        self,
        initial: UiProjectionSnapshot,
        updated: UiProjectionSnapshot,
    ) -> None:
        self._initial = initial
        self._updated = updated
        self.calls = 0
        self.closed = False

    def projection(self) -> UiProjectionSnapshot:
        self.calls += 1
        if self.calls == 1:
            return self._initial
        if self.calls == 2:
            raise UiIpcUnavailable()
        return self._updated

    def close(self) -> None:
        self.closed = True


def test_projection_poll_recovers_after_transient_ipc_failure() -> None:
    initial = cast(UiProjectionSnapshot, object())
    updated = cast(UiProjectionSnapshot, object())
    client = _TransientProjectionClient(initial, updated)
    recovered = threading.Event()
    connectivity: list[bool] = []

    def observe(snapshot: UiProjectionSnapshot | None, connected: bool) -> None:
        connectivity.append(connected)
        if snapshot is updated and connected:
            recovered.set()

    controller = UiController(
        cast(UiIpcClient, client),
        poll_interval=0.1,
        on_update=observe,
    )
    try:
        assert controller.start() is initial
        assert recovered.wait(1.0)
        assert controller.snapshot is updated
        assert controller.connected is True
        assert client.calls >= 3
        assert False in connectivity
        assert connectivity[-1] is True
    finally:
        controller.stop()

    assert client.closed is True


def test_iqoption_login_refreshes_balance_projection_before_returning() -> None:
    initial = cast(UiProjectionSnapshot, object())
    connected = cast(UiProjectionSnapshot, object())

    class Client:
        projection_calls = 0

        def login_iqoption(self, account_mode: str) -> UiIqOptionLoginAck:
            assert account_mode == "practice"
            return UiIqOptionLoginAck(True, True, "IQOPTION_PRACTICE_CONNECTED")

        def projection(self) -> UiProjectionSnapshot:
            self.projection_calls += 1
            return initial if self.projection_calls == 1 else connected

        def close(self) -> None:
            return None

    client = Client()
    controller = UiController(cast(UiIpcClient, client))
    controller.refresh()

    ack = controller.login_iqoption("practice")

    assert ack.connected is True
    assert controller.snapshot is connected
    assert client.projection_calls == 2
