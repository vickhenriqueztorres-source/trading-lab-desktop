from __future__ import annotations

import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.iqoption_connection_worker.server import IQOptionReadOnlyWorkerServer
from packages.brokers.iqoption.community_read_only import (
    IQOptionAccountMode,
    IQOptionCommunityReadOnlySession,
    IQOptionExternalError,
)
from packages.domain.market import (
    BrokerInstrumentAvailability,
    BrokerInstrumentCatalog,
    BrokerInstrumentProduct,
    BrokerMarketKind,
)
from packages.protocol import MessageType
from packages.security import SecretValue
from tests.unit.test_iqoption_community_read_only import FakeWebSocket, _messages


class PayoutSocket(FakeWebSocket):
    def __init__(self, *, echo_id: bool = False, respond: bool = True) -> None:
        super().__init__(_messages())
        self.echo_id = echo_id
        self.respond = respond
        self.quote_requests = 0

    def send(self, message: str) -> None:
        super().send(message)
        request = json.loads(message)
        body = request.get("msg")
        if not isinstance(body, dict) or body.get("name") != "get-initialization-data":
            return
        self.quote_requests += 1
        if self.respond:
            response = {
                "name": "initialization-data",
                "msg": {
                    "turbo": {
                        "actives": {
                            "1": {
                                "name": "front.EURUSD",
                                "enabled": True,
                                "is_suspended": False,
                                "option": {"profit": {"commission": "15"}},
                            }
                        }
                    }
                },
            }
            if self.echo_id:
                response["request_id"] = request["request_id"]
            self.messages.append(json.dumps(response))


class CatalogSocket(FakeWebSocket):
    def __init__(self, *, digital_responds: bool = True) -> None:
        super().__init__(_messages())
        self.digital_responds = digital_responds

    def send(self, message: str) -> None:
        super().send(message)
        request = json.loads(message)
        body = request.get("msg")
        if not isinstance(body, dict):
            return
        if body.get("name") == "get-initialization-data":
            self.messages.append(
                json.dumps(
                    {
                        "name": "initialization-data",
                        "msg": {
                            "turbo": {
                                "actives": {
                                    "777": {
                                        "name": "front.EURUSD",
                                        "enabled": True,
                                        "is_suspended": False,
                                        "option": {"profit": {"commission": "15"}},
                                    },
                                    "778": {
                                        "name": "front.EURUSD-OTC",
                                        "enabled": True,
                                        "is_suspended": True,
                                        "option": {"profit": {"commission": "14"}},
                                    },
                                }
                            },
                            "binary": {
                                "actives": {
                                    "777": {
                                        "name": "front.EURUSD",
                                        "enabled": False,
                                        "is_suspended": False,
                                    }
                                }
                            },
                        },
                    }
                )
            )
        elif body.get("name") == "get-underlying-list":
            if not self.digital_responds:
                return
            self.messages.append(
                json.dumps(
                    {
                        "name": "underlying-list",
                        "msg": {
                            "underlying": [
                                {
                                    "active_id": 777,
                                    "underlying": "EURUSD",
                                    "schedule": [{"open": 1, "close": 4_102_444_800}],
                                },
                                {
                                    "active_id": 901,
                                    "underlying": "XAUUSD-OTC",
                                    "schedule": [{"open": 1, "close": 2}],
                                },
                            ]
                        },
                    }
                )
            )


class LegacyDigitalCatalogSocket(CatalogSocket):
    """Model a broker generation that ignores correlated underlying-list."""

    def send(self, message: str) -> None:
        request = json.loads(message)
        body = request.get("msg")
        if (
            isinstance(body, dict)
            and body.get("name") == "get-underlying-list"
            and "request_id" in request
        ):
            FakeWebSocket.send(self, message)
            return
        super().send(message)


def session_for(socket: FakeWebSocket) -> IQOptionCommunityReadOnlySession:
    session = IQOptionCommunityReadOnlySession(
        "test@example.invalid",
        SecretValue.from_text(secrets.token_hex(16)),
        IQOptionAccountMode.PRACTICE,
        login=lambda *_: SecretValue.from_text(secrets.token_hex(16)),
        websocket_factory=lambda: socket,
    )
    session.connect()
    return session


@pytest.mark.parametrize("echo_id", [False, True])
def test_initialization_payout_accepts_global_or_correlated_response(echo_id: bool) -> None:
    socket = PayoutSocket(echo_id=echo_id)
    session = session_for(socket)
    try:
        assert session.get_binary_payout("EURUSD", timeout=0.2) == Decimal("0.85")
        assert socket.quote_requests == 1
        assert session.is_connected
    finally:
        session.close()


def test_dynamic_catalog_discovers_binary_turbo_digital_and_replaces_static_ids() -> None:
    session = session_for(CatalogSocket())
    try:
        catalog = session.get_instrument_catalog(timeout=0.5)
        assert isinstance(
            BrokerInstrumentCatalog.from_payload(catalog.to_payload()), BrokerInstrumentCatalog
        )
        evidence = {(item.broker_symbol, item.product): item for item in catalog.instruments}
        turbo = evidence[("EURUSD", BrokerInstrumentProduct.TURBO)]
        assert turbo.broker_id == "777"
        assert turbo.market_kind is BrokerMarketKind.REGULAR
        assert turbo.availability is BrokerInstrumentAvailability.OPEN
        assert turbo.analyzable and turbo.quotable and turbo.executable
        assert evidence[("EURUSD-OTC", BrokerInstrumentProduct.TURBO)].availability is (
            BrokerInstrumentAvailability.SUSPENDED
        )
        digital = evidence[("EURUSD", BrokerInstrumentProduct.DIGITAL)]
        assert digital.availability is BrokerInstrumentAvailability.OPEN
        assert digital.detectable and not digital.analyzable
        assert not digital.quotable and not digital.executable
        assert session._active_id("EURUSD") == 777
        with pytest.raises(IQOptionExternalError, match="IQOPTION_SYMBOL_UNSUPPORTED"):
            session._active_id("GBPUSD")
    finally:
        session.close()


def test_digital_catalog_uses_legacy_single_flight_request_without_request_id() -> None:
    socket = LegacyDigitalCatalogSocket()
    session = session_for(socket)
    try:
        catalog = session.get_instrument_catalog(timeout=0.5)
        assert any(item.product is BrokerInstrumentProduct.DIGITAL for item in catalog.instruments)
        request = next(
            item
            for item in socket.sent
            if isinstance(item.get("msg"), dict)
            and item["msg"].get("name") == "get-underlying-list"
        )
        assert "request_id" not in request
    finally:
        session.close()


def test_digital_catalog_timeout_does_not_discard_fresh_turbo_catalog() -> None:
    socket = CatalogSocket(digital_responds=False)
    session = session_for(socket)
    try:
        catalog = session.get_instrument_catalog(timeout=0.1)
        assert BrokerInstrumentProduct.DIGITAL in catalog.unavailable_products
        assert any(
            item.product is BrokerInstrumentProduct.TURBO and item.executable
            for item in catalog.instruments
        )
        assert not any(
            item.product is BrokerInstrumentProduct.DIGITAL for item in catalog.instruments
        )
        assert session.is_connected
        # The same missing product endpoint is not allowed to stall M1 market
        # traffic on every minute-level Binary catalogue refresh.
        session.get_instrument_catalog(timeout=0.1)
        underlying_requests = [
            item
            for item in socket.sent
            if isinstance(item.get("msg"), dict)
            and item["msg"].get("name") == "get-underlying-list"
        ]
        assert len(underlying_requests) == 1
    finally:
        session.close()


def test_late_global_payout_cannot_answer_a_new_request() -> None:
    socket = PayoutSocket(respond=False)
    session = session_for(socket)
    try:
        with pytest.raises(IQOptionExternalError, match="IQOPTION_REQUEST_TIMEOUT"):
            session.get_binary_payout("EURUSD", timeout=0.02)
        assert not session.is_connected
        with pytest.raises(IQOptionExternalError, match="IQOPTION_WEBSOCKET_UNAVAILABLE"):
            session.get_binary_payout("EURUSD", timeout=0.02)
        assert socket.quote_requests == 1
    finally:
        session.close()


@pytest.mark.parametrize("reason", ["IQOPTION_PAYOUT_UNAVAILABLE", "IQOPTION_RESPONSE_TOO_LARGE"])
def test_worker_preserves_allowlisted_external_error(reason: str) -> None:
    kind, payload = IQOptionReadOnlyWorkerServer._error_payload(reason)
    assert kind is MessageType.ERROR
    assert payload == {"reason_code": reason}


def test_unknown_external_error_never_becomes_invalid_ipc_or_leaks_text() -> None:
    kind, payload = IQOptionReadOnlyWorkerServer._error_payload("private raw exception")
    assert kind is MessageType.ERROR
    assert payload == {"reason_code": "IQOPTION_EXTERNAL_ERROR"}


def test_identical_order_and_radar_projections_do_not_rebuild_cells() -> None:
    from PySide6.QtWidgets import QApplication

    from apps.ui.components.iqoption_asset_radar import IqOptionAssetRadarWidget
    from packages.protocol import UiIqOptionAssetRank

    app = QApplication.instance() or QApplication([])
    widget = IqOptionAssetRadarWidget()
    rank = UiIqOptionAssetRank("EURUSD", "EUR/USD", "45", None, "NO_SIGNAL", False, "MONITORING")
    widget.update_ranking([rank])
    before = widget._table.item(0, 0)
    widget.update_ranking([rank])
    assert widget._table.item(0, 0) is before
    widget.update_ranking([replace(rank, rsi="72", direction="PUT", status="TRIGGERED")])
    assert widget._table.item(0, 1).text() == "72"
    widget.close()
    assert app is not None


@pytest.mark.parametrize("recovery", [True, False])
def test_dead_broker_with_live_ipc_replaces_worker_and_stays_disarmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovery: bool
) -> None:
    from apps.core.lifecycle_service import CoreLifecycleService
    from apps.core.worker_client import DeliveryCertainty, WorkerDispatchError
    from apps.core.worker_supervisor import WorkerHealthState
    from packages.domain.market import BrokerAccountBalance, BrokerClockSnapshot
    from packages.protocol import ProtocolErrorCode

    made = []

    class Client:
        dead = False
        capabilities = SimpleNamespace(connection_mode="DEMO_AUTH_FINANCIAL")

        def broker_balance(self):
            if self.dead:
                raise WorkerDispatchError(
                    ProtocolErrorCode.IQOPTION_WEBSOCKET_UNAVAILABLE,
                    DeliveryCertainty.NOT_SENT,
                    "session unavailable",
                )
            return BrokerAccountBalance(10000, "USD", "DEMO", datetime.now(UTC))

        def broker_clock(self):
            now = datetime.now(UTC)
            return BrokerClockSnapshot(int(now.timestamp()), now, 0.01, Decimal(0))

    class Supervisor:
        health_state = WorkerHealthState.READY
        process = None

        def __init__(self, *args, **kwargs):
            self.client = Client()
            self.stopped = False
            made.append(self)

        def start(self):
            return self.client

        def shutdown(self, grace_seconds):
            self.stopped = True

    monkeypatch.setattr("apps.core.lifecycle_service.ReadOnlyWorkerSupervisor", Supervisor)
    monkeypatch.setattr(
        "apps.core.lifecycle_service.IQOptionCredentialVault",
        lambda *_: SimpleNamespace(configured_account_mode=lambda: "practice"),
    )
    monkeypatch.setattr("apps.core.lifecycle_service._IQOPTION_RECOVERY_DELAYS_SECONDS", (0.0,))
    service = CoreLifecycleService(tmp_path, ("simulated",), force_auth_simulation=True)
    service.start()
    runtime = service._require_runtime()
    attached = []
    monkeypatch.setattr(
        runtime, "attach_iqoption_worker", lambda client, **_: attached.append(client)
    )
    monkeypatch.setattr(service._iqoption_auto_trader, "start", lambda: None)
    try:
        assert service.connect_iqoption_selected_account("practice")[1]
        old = made[0]
        old.client.dead = True
        service._iqoption_bot_armed = True
        if recovery:
            service._request_iqoption_recovery("IQOPTION_WEBSOCKET_UNAVAILABLE")
            service._iqoption_startup_recovery_thread.join(timeout=3)
            assert not service._iqoption_startup_recovery_thread.is_alive()
        else:
            assert service.connect_iqoption_selected_account("practice")[1]
        assert len(made) == 2
        assert old.stopped
        assert attached == [old.client, made[1].client]
        assert not service._iqoption_bot_armed
        assert not service._iqoption_session_invalidated
        # A delayed callback from the dead generation cannot restart the new one.
        service._request_iqoption_recovery_from(old, ProtocolErrorCode.WORKER_CRASHED)
        assert service._iqoption is made[1]
    finally:
        service.emergency_shutdown()


def test_auto_candidate_telemetry_is_linear_not_cartesian() -> None:
    from apps.core.iqoption_auto_trader import IqOptionAutoTrader
    from apps.core.iqoption_risk_config import IqOptionRiskConfig
    from tests.unit.test_iqoption_auto_trader import (
        FakeClient,
        FakeRuntime,
        _falling_prices,
        _make_candles,
        explicit_signal_catalog,
    )

    symbols = ("EURUSD", "GBPUSD", "USDJPY", "EURUSD-OTC")
    runtime = FakeRuntime()
    client = FakeClient(_make_candles(_falling_prices()))
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="AUTO", strategy_id="AUTO"),
        catalog_provider=lambda: explicit_signal_catalog(symbols),
        operator_armed=lambda: False,
    )
    trader._symbols_for_cycle = lambda _: tuple((s, s) for s in symbols)
    trader._evaluate_cycle()
    summaries = [f for n, f in runtime.events if f.get("phase") == "CANDIDATE_RESOLUTION_SUMMARY"]
    assert len(summaries) == len(symbols)
    assert all(f["rejected_count"] == len(symbols) - 1 for f in summaries)
    assert not any(
        f.get("phase") == "CANDIDATE_RESOLUTION" and f.get("stage_rejected") == "ASSET_MISMATCH"
        for _, f in runtime.events
    )
    assert runtime.requests == []


@pytest.mark.parametrize("oversized", [False, True])
def test_real_loopback_websocket_accepts_large_catalogue_but_enforces_bound(
    monkeypatch: pytest.MonkeyPatch, oversized: bool
) -> None:
    import threading

    from websockets.sync.server import serve

    from packages.brokers.iqoption.community_read_only import (
        IQOPTION_MAX_MESSAGE_BYTES,
        _websocket_factory,
    )

    names = []

    def handler(connection):
        try:
            for raw in connection:
                request = json.loads(raw)
                names.append(request["name"])
                if request["name"] == "authenticate":
                    for item in _messages():
                        connection.send(json.dumps(item))
                elif (
                    isinstance(request.get("msg"), dict)
                    and request["msg"].get("name") == "get-initialization-data"
                ):
                    fake = PayoutSocket()
                    fake.send(raw)
                    response = json.loads(fake.messages[-1])
                    response["padding"] = "x" * (
                        IQOPTION_MAX_MESSAGE_BYTES if oversized else 2 * 1024 * 1024
                    )
                    connection.send(json.dumps(response))
        except Exception:
            # The oversized case deliberately closes the peer with code 1009.
            if not oversized:
                raise

    with serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(
            "packages.brokers.iqoption.community_read_only.IQOPTION_WEBSOCKET_URLS",
            (f"ws://127.0.0.1:{server.socket.getsockname()[1]}",),
        )
        session = IQOptionCommunityReadOnlySession(
            "test@example.invalid",
            SecretValue.from_text(secrets.token_hex(16)),
            IQOptionAccountMode.PRACTICE,
            login=lambda *_: SecretValue.from_text(secrets.token_hex(16)),
            websocket_factory=_websocket_factory,
        )
        try:
            session.connect()
            if oversized:
                with pytest.raises(IQOptionExternalError, match="IQOPTION_RESPONSE_TOO_LARGE"):
                    session.get_binary_payout("EURUSD")
                assert not session.is_connected
            else:
                assert session.get_binary_payout("EURUSD") == Decimal("0.85")
                assert session.is_connected
        finally:
            session.close()
            server.shutdown()
            thread.join(timeout=2)
        assert not thread.is_alive()
    assert set(names) <= {"authenticate", "sendMessage", "timesync", "heartbeat"}


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("name", "front.EURUSD-OTC", "IQOPTION_PAYOUT_UNAVAILABLE"),
        ("enabled", False, "IQOPTION_ACTIVE_UNAVAILABLE"),
        ("is_suspended", True, "IQOPTION_ACTIVE_SUSPENDED"),
    ],
)
def test_global_payout_still_checks_exact_asset_and_availability(field, value, reason) -> None:
    class InvalidSocket(PayoutSocket):
        def send(self, raw):
            count = self.quote_requests
            super().send(raw)
            if self.quote_requests > count:
                response = json.loads(self.messages[-1])
                response["msg"]["turbo"]["actives"]["1"][field] = value
                self.messages[-1] = json.dumps(response)

    session = session_for(InvalidSocket())
    try:
        with pytest.raises(IQOptionExternalError, match=reason):
            session.get_binary_payout("EURUSD", timeout=0.2)
        assert session.is_connected
    finally:
        session.close()


def test_global_payout_wrong_correlation_is_ignored() -> None:
    class ForeignSocket(PayoutSocket):
        def send(self, raw):
            count = self.quote_requests
            super().send(raw)
            if self.quote_requests > count:
                response = json.loads(self.messages[-1])
                response["request_id"] = "another-generation"
                self.messages[-1] = json.dumps(response)

    session = session_for(ForeignSocket())
    try:
        with pytest.raises(IQOptionExternalError, match="IQOPTION_REQUEST_TIMEOUT"):
            session.get_binary_payout("EURUSD", timeout=0.05)
    finally:
        session.close()


def test_order_projection_reuses_cells_and_renders_new_settlement() -> None:
    from PySide6.QtWidgets import QApplication

    from apps.ui.components.order_table import OrderTableView
    from packages.protocol.ui_messages import OrderSummary

    app = QApplication.instance() or QApplication([])
    widget = OrderTableView()
    order = OrderSummary(
        order_id="order-1",
        broker="IQ_OPTION",
        symbol="EURUSD",
        direction="CALL",
        amount_minor_units=100,
        currency="USD",
        state="OPEN",
        created_at_utc=datetime.now(UTC),
    )
    widget.update_orders([order])
    first = widget._table.item(0, 0)
    for _ in range(100):
        widget.update_orders([order])
    assert widget._table.item(0, 0) is first
    widget.update_orders([replace(order, state="SETTLED", realized_pnl_minor_units=85)])
    assert "WON" in widget._table.item(0, 5).text()
    widget.update_orders([])
    assert widget.order_count == 0
    widget.close()
    assert app is not None
