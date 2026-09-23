from __future__ import annotations

import threading
from collections.abc import Callable

from apps.ui.ipc_client import UiIpcClient, UiIpcError
from packages.protocol import (
    UiAuthActivateKeyAck,
    UiAuthSignOutAck,
    UiAuthStartLoginAck,
    UiAuthStatusResponse,
    UiAuthSubmitOtpAck,
    UiCommandAck,
    UiDigitRiskConfig,
    UiGenerateDiagnosticResponse,
    UiIqOptionLoginAck,
    UiIqOptionRiskConfig,
    UiProjectionSnapshot,
    UiResolveOrderAck,
    UiUpdateDigitRiskConfigAck,
)


class UiController:
    """Poll-driven controller with an immutable latest snapshot."""

    def __init__(
        self,
        client: UiIpcClient,
        *,
        poll_interval: float = 1.0,
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
        self._cached_auth_status: UiAuthStatusResponse | None = None
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

    @property
    def cached_auth_status(self) -> UiAuthStatusResponse | None:
        with self._lock:
            return self._cached_auth_status

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

    def login_iqoption(
        self,
        account_mode: str,
        *,
        source: str = "manual",
    ) -> UiIqOptionLoginAck:
        ack = (
            self._client.login_iqoption(account_mode)
            if source == "manual"
            else self._client.login_iqoption(account_mode, source=source)
        )
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

    def auth_start_login(self, email: str) -> UiAuthStartLoginAck:
        return self._client.auth_start_login(email)

    def auth_submit_otp(self, challenge_id: str, code: str) -> UiAuthSubmitOtpAck:
        ack = self._client.auth_submit_otp(challenge_id, code)
        if ack.status == "AUTHORIZED":
            self.refresh()
        return ack

    def auth_activate_key(self, product_key: str) -> UiAuthActivateKeyAck:
        ack = self._client.auth_activate_key(product_key)
        if ack.status in ("AUTHORIZED", "OFFLINE_AUTHORIZED") or ack.ok:
            self.refresh()
        return ack

    def auth_status(self) -> UiAuthStatusResponse:
        return self._client.auth_status()

    def auth_sign_out(self) -> UiAuthSignOutAck:
        ack = self._client.auth_sign_out()
        self.refresh()
        return ack

    def resolve_order(
        self,
        order_id: str,
        action: str,
        *,
        realized_pnl_minor_units: int = 0,
        broker_order_id: str | None = None,
        reason: str = "MANUAL_OPERATOR_RESOLUTION",
        operator: str = "OPERATOR",
    ) -> UiResolveOrderAck:
        ack = self._client.resolve_order(
            order_id,
            action,
            realized_pnl_minor_units=realized_pnl_minor_units,
            broker_order_id=broker_order_id,
            reason=reason,
            operator=operator,
        )
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
        poll_count = 0
        while not self._stop.wait(self._poll_interval):
            poll_count += 1
            try:
                self.refresh()
            except UiIpcError:
                self._set_state(self.snapshot, False)
                # A transient timeout must not permanently freeze the dashboard.  The
                # serialized IPC client reconnects on the next request, so keep this
                # bounded poll loop alive until the UI is explicitly stopped.
                continue

            # Refresh auth status every 10 poll intervals (~5s) or on initial startup
            if self._cached_auth_status is None or poll_count % 10 == 0:
                try:
                    auth = self._client.auth_status()
                    with self._lock:
                        self._cached_auth_status = auth
                except Exception:
                    pass

    def _set_state(self, snapshot: UiProjectionSnapshot | None, connected: bool) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._connected = connected
        if self._on_update is not None:
            self._on_update(snapshot, connected)
