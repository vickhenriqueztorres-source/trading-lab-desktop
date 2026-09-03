"""R-VEND-1..3: execute the vendored component with fake wire I/O, never external IQ."""

import json
import logging
import ssl
import uuid
from decimal import Decimal

import pytest
from pydantic import SecretStr
from strategy_lab.collect.credentials import Credentials
from strategy_lab.collect.iq_client import IQClientError, _VendorBackend


@pytest.fixture
def backend():
    """R-VEND-3: ephemeral synthetic credentials, not stored in a fixture/file."""
    value = uuid.uuid4().hex
    backend = _VendorBackend(
        Credentials(username=SecretStr(value), password=SecretStr(value)),
        timeout_s=1,
    )
    yield backend
    backend.close()


def test_tls_verified_and_no_legacy_connection(backend, monkeypatch):
    """R-VEND-1: HTTPS TLS, bounded request, no redirect; legacy connect is denied."""
    calls = []
    monkeypatch.setattr(backend._api.session, "request", lambda **kw: calls.append(kw))
    backend._api.login(backend._api.username, backend._api.password)
    assert calls[0]["verify"] is True
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["timeout"] == (10, 1)
    assert backend._api.session.verify is True
    with pytest.raises(IQClientError, match="IQ_READ_ONLY_VIOLATION"):
        backend._api.connect()


@pytest.mark.parametrize(
    "name,payload",
    [
        ("buyV3", {}),
        ("buy", {}),
        ("sell", {}),
        ("subscribeMessage", {}),
        ("sendMessage", {"name": "binary-options.open-option", "version": "1.0"}),
        ("sendMessage", {"name": "get-balances", "version": "1.0"}),
        ("sendMessage", {"name": "get-candles", "version": "1.0"}),
        ("authenticate", {"ssid": "public_test_marker", "protocol": 3, "extra": True}),
    ],
)
def test_no_financial_or_unrecognized_message_emitted(backend, monkeypatch, name, payload):
    """R-VEND-3: rejects writes/unknown reads before WS send, including vendor builders."""
    calls = []
    monkeypatch.setattr(backend._ws, "send", calls.append)
    with pytest.raises(IQClientError, match="IQ_READ_ONLY_VIOLATION"):
        backend._send(name, payload)
    assert calls == []


def test_http_denies_everything_except_exact_login(backend, monkeypatch):
    """R-VEND-3: no logout POST/account modification/redirect host through resources."""
    calls = []
    monkeypatch.setattr(backend._api.session, "request", lambda **kw: calls.append(kw))
    for url in ("https://example.invalid", "https://auth.iqoption.com/api/v1.0/logout"):
        with pytest.raises(IQClientError):
            backend._http(url=url, method="POST")
    with pytest.raises(IQClientError):
        backend._api.logout()
    assert calls == []


def test_callbacks_decode_decimal_and_never_keep_balance(backend):
    """R-VEND-3/I-8: price floats decoded straight to Decimal; account data not retained."""
    backend._expected = "candles"
    backend._on_message(None, '{"name":"candles","msg":{"candles":[{"close":1.070123456789}]}}')
    assert backend._reply[0]["close"] == Decimal("1.070123456789")
    previous = backend._reply
    backend._on_message(None, '{"name":"balances","msg":{"private":"ignore"}}')
    assert backend._reply is previous


def test_late_request_id_does_not_satisfy_current_request(backend):
    """R-VEND-3: a mismatched response cannot satisfy the current read."""
    backend._expected = "candles"
    backend._last_request_id = "9"
    backend._on_message(None, '{"name":"candles","request_id":"8","msg":{"candles":[]}}')
    assert not backend._received.is_set()


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        '{"name":"candles","msg":null}',
        '{"name":"candles","msg":{"candles":[NaN]}}',
        '{"name":"candles","name":"balances","msg":{}}',
    ],
)
def test_malformed_message_fail_closed(backend, raw):
    """R-VEND-3: errors wake waiters and never create a valid reply."""
    backend._expected = "candles"
    backend._on_message(None, raw)
    assert backend._error == "IQ_PROTOCOL_INVALID"
    with pytest.raises(IQClientError, match="IQ_PROTOCOL_INVALID"):
        backend._wait(backend._received)


def test_disconnect_does_not_login_again(backend, monkeypatch):
    """R-VEND-3: no automatic authentication loop or retry after loss."""
    calls = []
    monkeypatch.setattr(backend._ws, "send", calls.append)
    backend._on_close(None, 1006, object())
    with pytest.raises(IQClientError, match="IQ_SOCKET_FAILED"):
        backend._send("api_option_init_all", "")
    assert calls == []


def test_send_error_does_not_retry_and_has_no_secret(backend, monkeypatch):
    """R-VEND-3/I-8: exception payload is not forwarded; exactly one attempted send."""
    calls = []
    marker = uuid.uuid4().hex

    def broken(data):
        calls.append(data)
        raise RuntimeError(marker)

    monkeypatch.setattr(backend._ws, "send", broken)
    with pytest.raises(IQClientError) as caught:
        backend._send("api_option_init_all", "")
    assert marker not in str(caught.value)
    assert len(calls) == 1


def test_timeout_uses_monotonic_and_closes(backend, monkeypatch):
    """R-VEND-3: deadline expires without waiting wall time; client must be recreated."""
    clock = iter([0, 0, 2])
    monkeypatch.setattr("strategy_lab.collect.iq_client.time.monotonic", lambda: next(clock))

    class Never:
        def wait(self, timeout):
            return False

    with pytest.raises(IQClientError, match="IQ_COLLECTION_TIMEOUT"):
        backend._wait(Never())
    assert backend._error == "IQ_COLLECTION_TIMEOUT"


def test_connect_uses_actual_vendor_login_builder_with_fake_wire(backend, monkeypatch, caplog):
    """R-VEND-3: TLS websocket, auth proof, no profile/financial routes, no leaked logs."""
    marker = uuid.uuid4().hex
    calls = []
    ws_options = []

    class Response:
        status_code = 200
        cookies = {"ssid": marker}

        def close(self):
            pass

    monkeypatch.setattr(backend._api.session, "request", lambda **kw: Response())

    def run(**kwargs):
        ws_options.append(kwargs)
        backend._on_open(None)

    def send(text):
        frame = json.loads(text)
        calls.append(frame["name"])
        backend._on_message(None, '{"name":"authenticated","msg":true}')

    monkeypatch.setattr(backend._ws, "run_forever", run)
    monkeypatch.setattr(backend._ws, "send", send)
    caplog.set_level(logging.DEBUG)
    backend.connect()
    assert calls == ["authenticate"]
    assert ws_options[0]["sslopt"] == {"cert_reqs": ssl.CERT_REQUIRED, "check_hostname": True}
    assert ws_options[0]["reconnect"] == 0
    assert backend._api.password == backend._api.username == ""
    assert marker not in caplog.text
    backend.close()
    assert not backend._thread.is_alive()


def test_vendor_candle_and_catalog_builders(backend, monkeypatch):
    """R-VEND-3: actual vendor builders use mapped active ID, never send order frames."""
    frames = []
    backend._authenticated.set()

    def send(text):
        frame = json.loads(text)
        frames.append(frame)
        if frame["name"] == "api_option_init_all":
            reply = {"name": "api_option_init_all_result", "msg": {"result": {}}}
        else:
            reply = {"name": "candles", "msg": {"candles": []}}
        backend._on_message(None, json.dumps(reply))

    monkeypatch.setattr(backend._ws, "send", send)
    assert backend.catalog() == {"result": {}}
    assert backend.candles(76, 60, 1000, 1700060040) == []
    assert frames[1]["msg"]["body"]["active_id"] == 76
    assert frames[1]["msg"]["body"]["count"] == 1000
    assert len(frames) == 2


def test_error_callback_does_not_log_arbitrary_payload(backend, caplog):
    """R-VEND-3/I-8: even DEBUG root logging does not receive untrusted error contents."""
    marker = uuid.uuid4().hex
    caplog.set_level(logging.DEBUG)
    backend._on_error(None, RuntimeError(marker))
    assert marker not in caplog.text
    assert backend._error == "IQ_SOCKET_FAILED"


def test_clock_patch_does_not_invent_broker_timestamp(backend):
    """R-VEND-1/I-2: source timestamp remains absent until externally supplied."""
    clock = backend._api.timesync
    assert clock.server_timestamp is None
    clock.server_timestamp = 1700000040999
    assert clock.server_timestamp == 1700000040
    assert type(clock.server_timestamp) is int
    assert clock.server_datetime.utcoffset().total_seconds() == 0


def test_shutdown_error_detects_live_thread(backend):
    """R-VEND-3: a remaining socket thread is an error, not claimed as clean shutdown."""

    class Alive:
        def join(self, timeout):
            pass

        def is_alive(self):
            return True

    backend._thread = Alive()
    try:
        with pytest.raises(IQClientError, match="IQ_SHUTDOWN_INCOMPLETE"):
            backend.close()
    finally:
        backend._thread = None
