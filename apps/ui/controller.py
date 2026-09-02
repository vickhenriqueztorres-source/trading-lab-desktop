from __future__ import annotations

import threading
from collections.abc import Callable

from apps.ui.ipc_client import UiIpcClient, UiIpcError
from packages.protocol import (
    UiCommandAck,
    UiDigitRiskConfig,
    UiGenerateDiagnosticResponse,
    UiIqOptionLoginAck,
    UiIqOptionRiskConfig,
    UiProjectionSnapshot,
    UiUpdateDigitRiskConfigAck,
)


class UiController:
    """Poll-driven controller with an immutable latest snapshot."""

    def __init__(
        self,
        client: UiIpcClient,
        *,
        poll_interval: float = 0.5,
        on_update: Callable[[UiProjectionSnapshot | None, bool], None] | None = None,
    ) -> None:
        if not 0.1 <= poll_interval <= 10:
            raise ValueError("UI poll interval is outside bounds")
        self._client = client
        self._poll_interval = poll_interval
        self._on_update = on_update
        self._lock = threading.Lock()
        self._snapshot: UiProjectionSnapshot | None = None
        self._connected = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def snapshot(self) -> UiProjectionSnapshot | None:
        with self._lock:
            return self._snapshot

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def start(self) -> UiProjectionSnapshot:
        snapshot = self.refresh()
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._poll, name="ui-projection-poll", daemon=True
            )
            self._thread.start()
        return snapshot

    def refresh(self) -> UiProjectionSnapshot:
        snapshot = self._client.projection()
        self._set_state(snapshot, True)
        return snapshot

    def safe_stop(self) -> UiCommandAck:
        ack = self._client.safe_stop()
        self.refresh()
        return ack

    def resume(self) -> UiCommandAck:
        ack = self._client.resume()
        self.refresh()
        return ack

    def request_safe_close(self) -> UiCommandAck:
        return self._client.request_shutdown()

    def generate_diagnostic(self) -> UiGenerateDiagnosticResponse:
        return self._client.generate_diagnostic()

    def connect_deriv_demo(self) -> UiCommandAck:
        ack = self._client.connect_deriv_demo()
        self.refresh()
        return ack

    def update_digit_risk_config(self, config: UiDigitRiskConfig) -> UiUpdateDigitRiskConfigAck:
        ack = self._client.update_digit_risk_config(config)
        self.refresh()
        return ack

    def reset_digit_test_session(self) -> UiCommandAck:
        ack = self._client.reset_digit_test_session()
        self.refresh()
        return ack

    def login_iqoption(self, account_mode: str) -> UiIqOptionLoginAck:
        ack = self._client.login_iqoption(account_mode)
        if ack.connected:
            self.refresh()
        return ack

    def update_iqoption_risk_config(self, config: UiIqOptionRiskConfig) -> UiCommandAck:
        ack = self._client.update_iqoption_risk_config(config)
        self.refresh()
        return ack

    def control_iqoption_bot(self, enabled: bool) -> UiCommandAck:
        ack = self._client.control_iqoption_bot(enabled)
        self.refresh()
        return ack

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        self._client.close()

    def _poll(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self.refresh()
            except UiIpcError:
                self._set_state(self.snapshot, False)
                # A transient timeout must not permanently freeze the dashboard.  The
                # serialized IPC client reconnects on the next request, so keep this
                # bounded poll loop alive until the UI is explicitly stopped.
                continue

    def _set_state(self, snapshot: UiProjectionSnapshot | None, connected: bool) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._connected = connected
        if self._on_update is not None:
            self._on_update(snapshot, connected)
