from __future__ import annotations

import threading
from typing import cast

from apps.ui.controller import UiController
from apps.ui.ipc_client import UiIpcClient, UiIpcUnavailable
from packages.protocol import UiProjectionSnapshot


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
