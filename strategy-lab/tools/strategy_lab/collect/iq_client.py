"""Read-only vendor boundary (R-VEND-1..3).

Only this module imports iqoptionapi. The legacy stable_api reconnect/trading/logging
paths are NOT used. Reuse vendor HTTP login and candle/catalog channel builders with
fail-closed request guards, bounded waits and price-only Decimal decoding.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import random
import re
import ssl
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Never, Protocol, cast, runtime_checkable

from primitives import Candle
from pydantic import ValidationError

from strategy_lab.collect.credentials import Credentials, load_credentials

__all__ = ["Candle", "FakeIQClient", "IQClient", "IQClientProtocol", "InvalidCandleError"]

LAB_ROOT = Path(__file__).resolve().parents[3]
VENDOR_ROOT = LAB_ROOT / "vendor" / "iqoptionapi"
ASSET_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,39}", re.ASCII)
PRICE_FIELDS = ("from", "to", "open", "max", "min", "close", "volume")
NUMBER_PATTERN = re.compile(r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", re.ASCII)


class IQClientError(RuntimeError):
    """Stable non-sensitive reason; never include upstream exception strings."""


def _reject_constant(value: str) -> Never:
    raise ValueError("IQ_JSON_NONFINITE")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("IQ_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _decode_json(text: str) -> Any:
    return json.loads(
        text,
        parse_float=Decimal,
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_object,
    )


def _safe_price_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {"invalid_row": True}
    result: dict[str, object] = {}
    for key in PRICE_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if type(value) is int or (
            isinstance(value, (str, Decimal))
            and len(str(value)) <= 96
            and NUMBER_PATTERN.fullmatch(str(value))
        ):
            result[key] = str(value) if isinstance(value, Decimal) else value
        else:
            result[key] = "<invalid>"
    return result


class InvalidCandleError(IQClientError):
    def __init__(self, payload: object) -> None:
        # Unknown keys and arbitrary text may contain PII even in a price field.
        self.payload = _safe_price_payload(payload)
        super().__init__("IQ_INVALID_CANDLE")


@runtime_checkable
class IQClientProtocol(Protocol):
    def login(self) -> None: ...
    def logout(self) -> None: ...
    def fetch_candles(self, asset: str, tf_s: int, n: int, end_ts: int) -> list[Candle]: ...
    def fetch_payout(self, asset: str) -> Decimal | None: ...
    def list_assets(self) -> list[str]: ...


class Backend(Protocol):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def catalog(self) -> object: ...
    def candles(self, active_id: int, tf_s: int, n: int, end_ts: int) -> object: ...


def _utc_epoch() -> int:
    return int(datetime.now(UTC).timestamp())


def _decimal(value: object) -> Decimal:
    # JSON wire decoding uses parse_float=Decimal; never round money through float.
    if type(value) is not int and not isinstance(value, (str, Decimal)):
        raise ValueError("IQ_INVALID_NUMBER")
    text = str(value)
    if len(text) > 96 or not NUMBER_PATTERN.fullmatch(text):
        raise ValueError("IQ_INVALID_NUMBER")
    number = Decimal(text)
    if not number.is_finite() or abs(number.adjusted()) > 100:
        raise ValueError("IQ_INVALID_NUMBER")
    return number


def _integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ValueError("IQ_INVALID_INTEGER")
    return value


def validate_asset(asset: str) -> str:
    if not isinstance(asset, str) or not ASSET_PATTERN.fullmatch(asset):
        raise IQClientError("IQ_INVALID_ASSET")
    return asset


def convert_candle(payload: object, *, tf_s: int, end_ts: int, now_ts: int) -> Candle:
    """Validate the entire row; metadata is explicitly excluded, never prices."""
    try:
        if not isinstance(payload, dict):
            raise ValueError
        ts = _integer(payload["from"])
        if "to" in payload and _integer(payload["to"]) != ts + tf_s:
            raise ValueError
        candle = Candle(
            ts=ts,
            o=_decimal(payload["open"]),
            h=_decimal(payload["max"]),
            l=_decimal(payload["min"]),
            c=_decimal(payload["close"]),
            tick_vol=_integer(payload["volume"]),
        )
        if ts % tf_s or ts + tf_s > end_ts or ts >= now_ts // 60 * 60 - 60:
            raise ValueError
        if ts + tf_s > now_ts // tf_s * tf_s:
            raise ValueError
        return candle
    except (KeyError, ValueError, TypeError, InvalidOperation, ValidationError):
        raise InvalidCandleError(payload) from None


def parse_catalog(raw: object) -> dict[str, tuple[int, Decimal | None]]:
    """Use turbo (M1) only; never silently substitute a different product's payout."""
    try:
        if not isinstance(raw, dict):
            raise ValueError
        actives = raw["result"]["turbo"]["actives"]
        if not isinstance(actives, dict) or not actives or len(actives) > 10000:
            raise ValueError
        result: dict[str, tuple[int, Decimal | None]] = {}
        for identifier, row in actives.items():
            if (
                not isinstance(identifier, str)
                or not identifier.isascii()
                or not identifier.isdigit()
            ):
                raise ValueError
            active_id = int(identifier)
            if active_id <= 0 or not isinstance(row, dict):
                raise ValueError
            name = validate_asset(row["name"].removeprefix("front."))
            if name in result:
                raise ValueError
            commission = row.get("option", {}).get("profit", {}).get("commission")
            payout = None
            if commission is not None:
                percent = _decimal(commission)
                if not 0 <= percent <= 100:
                    raise ValueError
                payout = (Decimal(100) - percent) / Decimal(100)
            result[name] = (active_id, payout)
        return result
    except (KeyError, ValueError, TypeError, AttributeError, InvalidOperation):
        raise IQClientError("IQ_INVALID_CATALOG") from None


class IQClient:
    """One serial read-only session, no retries, no shared state with the bot."""

    def __init__(
        self,
        *,
        backend: Backend | None = None,
        credential_provider: Callable[[], Credentials] = load_credentials,
        pause: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0.5, 2.0),
        now: Callable[[], int] = _utc_epoch,
    ) -> None:
        self._backend = backend
        self._credential_provider = credential_provider
        self._pause = pause
        self._jitter = jitter
        self._now = now
        self._connected = False
        self._called = False
        self._lock = threading.Lock()
        self._assets: dict[str, tuple[int, Decimal | None]] = {}

    def _paced(self, action: Callable[[], Any]) -> Any:
        if self._called:
            delay = self._jitter()
            if not 0.5 <= delay <= 2.0:
                raise IQClientError("IQ_INVALID_PAUSE")
            self._pause(delay)
        self._called = True
        try:
            return action()
        except IQClientError:
            raise
        except Exception:
            raise IQClientError("IQ_COLLECTION_FAILED") from None

    def _require(self) -> Backend:
        if not self._connected or self._backend is None:
            raise IQClientError("IQ_NOT_CONNECTED")
        return self._backend

    def login(self) -> None:
        with self._lock:
            if self._connected:
                return
            try:
                if self._backend is None:
                    self._backend = _VendorBackend(self._credential_provider())
                self._paced(self._backend.connect)
                self._connected = True
            except Exception:
                self._connected = False
                if self._backend is not None:
                    try:
                        self._backend.close()
                    except Exception:
                        raise IQClientError("IQ_SHUTDOWN_INCOMPLETE") from None
                raise IQClientError("IQ_LOGIN_FAILED") from None

    def logout(self) -> None:
        with self._lock:
            try:
                if self._backend is not None:
                    self._backend.close()  # local shutdown; never delayed by rate pacing
            finally:
                self._connected = False
                self._assets.clear()

    def list_assets(self) -> list[str]:
        with self._lock:
            backend = self._require()
            self._assets = parse_catalog(self._paced(backend.catalog))
            return sorted(self._assets)

    def fetch_payout(self, asset: str) -> Decimal | None:
        validate_asset(asset)
        with self._lock:
            backend = self._require()
            catalog = parse_catalog(self._paced(backend.catalog))
            if asset not in catalog:
                raise IQClientError("IQ_ASSET_UNAVAILABLE")
            self._assets = catalog
            return catalog[asset][1]

    def fetch_candles(self, asset: str, tf_s: int, n: int, end_ts: int) -> list[Candle]:
        validate_asset(asset)
        if type(tf_s) is not int or tf_s < 60 or tf_s > 86400 or tf_s % 60:
            raise IQClientError("IQ_INVALID_TIMEFRAME")
        if type(n) is not int or not 1 <= n <= 1000:
            raise IQClientError("IQ_INVALID_COUNT")
        if type(end_ts) is not int or not 0 < end_ts <= self._now() // 60 * 60 - 60:
            raise IQClientError("IQ_INVALID_END")
        with self._lock:
            backend = self._require()
            if not self._assets:
                self._assets = parse_catalog(self._paced(backend.catalog))
            if asset not in self._assets:
                raise IQClientError("IQ_ASSET_UNAVAILABLE")
            active_id = self._assets[asset][0]
            raw = self._paced(lambda: backend.candles(active_id, tf_s, n, end_ts))
            if not isinstance(raw, list) or not 0 < len(raw) <= n:
                raise IQClientError("IQ_INVALID_CANDLE_BATCH")
            result = [
                convert_candle(row, tf_s=tf_s, end_ts=end_ts, now_ts=self._now()) for row in raw
            ]
            if any(left.ts >= right.ts for left, right in zip(result, result[1:], strict=False)):
                raise IQClientError("IQ_CANDLE_ORDER_INVALID")
            return result


class FakeIQClient(IQClient):
    """Fixture-backed fake. Synthetic and recorded provenance remain distinguishable."""

    def __init__(self, fixture: Path, **kwargs: Any) -> None:
        super().__init__(backend=_FixtureBackend(fixture), **kwargs)


class _FixtureBackend:
    def __init__(self, fixture: Path) -> None:
        if fixture.stat().st_size > 4 * 1024 * 1024:
            raise IQClientError("IQ_FIXTURE_TOO_LARGE")
        try:
            self.data = _decode_json(fixture.read_text(encoding="utf-8"))
        except (ValueError, RecursionError):
            raise IQClientError("IQ_FIXTURE_INVALID") from None
        if (
            not isinstance(self.data, dict)
            or self.data.get("schema_version") != 1
            or self.data.get("provenance") not in {"synthetic", "recorded"}
        ):
            raise IQClientError("IQ_FIXTURE_INVALID")
        if self.data["provenance"] == "recorded":
            digest = self.data.get("sha256")
            unsigned = {key: value for key, value in self.data.items() if key != "sha256"}
            try:
                canonical = json.dumps(
                    unsigned,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            except (TypeError, ValueError):
                raise IQClientError("IQ_FIXTURE_INVALID") from None
            if digest != hashlib.sha256(canonical).hexdigest():
                raise IQClientError("IQ_FIXTURE_INTEGRITY_FAILED")
        self.calls: list[str] = []

    def connect(self) -> None:
        self.calls.append("login")

    def close(self) -> None:
        self.calls.append("logout")

    def catalog(self) -> object:
        self.calls.append("catalog")
        payout = self.data.get("payout_return_ratio")
        commission = None if payout is None else str((1 - _decimal(payout)) * 100)
        return {
            "result": {
                "turbo": {
                    "actives": {
                        "1": {
                            "name": "front." + self.data["asset"],
                            "option": {"profit": {"commission": commission}},
                        }
                    }
                }
            }
        }

    def candles(self, active_id: int, tf_s: int, n: int, end_ts: int) -> object:
        self.calls.append("candles")
        if active_id != 1 or tf_s != self.data["tf_s"]:
            raise IQClientError("IQ_FIXTURE_SERIES_MISMATCH")
        return cast(object, [row for row in self.data["candles"] if row["from"] < end_ts][-n:])


class _VendorBackend:
    """Audited use of vendor resource/channel builders; legacy connect is unreachable.

    Any is confined to the untyped vendor/HTTP boundary. All outgoing actions are
    allowlisted here, all domain values are validated above. No auth auto-retry.
    """

    def __init__(self, credentials: Credentials, *, timeout_s: float = 30) -> None:
        if not 1 <= timeout_s <= 60:
            raise IQClientError("IQ_INVALID_TIMEOUT")
        from strategy_lab.collect.vendor_integrity import verify_vendor

        verify_vendor(VENDOR_ROOT)
        for name, module in tuple(sys.modules.items()):
            if name == "iqoptionapi" or name.startswith("iqoptionapi."):
                source = getattr(module, "__file__", None)
                if source is None or not Path(source).resolve().is_relative_to(VENDOR_ROOT):
                    raise IQClientError("IQ_VENDOR_IMPORT_CONFLICT")
        # Public package layout comes from this Lab's verified snapshot, not site-packages.
        sys.path.insert(0, str(VENDOR_ROOT.parent))
        try:
            vendor = importlib.import_module("iqoptionapi.api")
            ws_vendor = importlib.import_module("iqoptionapi.ws.client")
        finally:
            sys.path.remove(str(VENDOR_ROOT.parent))
        for name in ("iqoptionapi", "websocket", "urllib3", "requests"):
            logger = logging.getLogger(name)
            logger.handlers[:] = [logging.NullHandler()]
            logger.propagate = False
        importlib.import_module("websocket").enableTrace(False)
        self._timeout = timeout_s
        self._api: Any = vendor.IQOptionAPI(
            "iqoption.com",
            credentials.username.get_secret_value(),
            credentials.password.get_secret_value(),
            auto_logout=False,
        )
        self._api.send_websocket_request = self._send
        self._api.send_http_request_v2 = self._http
        self._api.send_http_request = self._deny
        self._api.connect = self._deny
        self._api.start_websocket = self._deny
        self._api.close = self.close
        self._api.websocket_client = ws_vendor.WebsocketClient(self._api)
        self._ws: Any = self._api.websocket
        self._ws.on_open = self._on_open
        self._ws.on_message = self._on_message
        self._ws.on_error = self._on_error
        self._ws.on_close = self._on_close
        self._opened = threading.Event()
        self._authenticated = threading.Event()
        self._received = threading.Event()
        self._expected: str | None = None
        self._reply: object = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._request_counter = 0
        self._last_request_id = ""

    @staticmethod
    def _deny(*args: object, **kwargs: object) -> None:
        raise IQClientError("IQ_READ_ONLY_VIOLATION")

    def _http(self, *, url: str, method: str, **kwargs: object) -> Any:
        if (method, url) != ("POST", "https://auth.iqoption.com/api/v2/login"):
            self._deny()
        if set(kwargs) - {"data", "headers"}:
            self._deny()
        try:
            return self._api.session.request(
                method=method,
                url=url,
                **kwargs,
                timeout=(10, self._timeout),
                allow_redirects=False,
                verify=True,
            )
        except Exception:
            raise IQClientError("IQ_AUTH_TRANSPORT_FAILED") from None

    def _send(
        self,
        name: str,
        msg: object,
        request_id: str = "",
        no_force_send: bool = True,
    ) -> None:
        allowed = (
            name == "authenticate" and isinstance(msg, dict) and set(msg) == {"ssid", "protocol"}
        )
        allowed |= name == "api_option_init_all" and msg == ""
        allowed |= (
            name == "sendMessage"
            and isinstance(msg, dict)
            and msg.get("name") == "get-candles"
            and msg.get("version") == "2.0"
        )
        if not allowed:
            self._deny()
        if self._error:
            raise IQClientError(self._error)
        self._request_counter += 1
        self._last_request_id = str(self._request_counter)
        try:
            self._ws.send(
                json.dumps(
                    {
                        "name": name,
                        "msg": msg,
                        "request_id": self._last_request_id,
                    },
                    separators=(",", ":"),
                )
            )
        except Exception:
            self._error = "IQ_SEND_FAILED"
            raise IQClientError(self._error) from None

    def _on_open(self, ws: object) -> None:
        # Bound socket writes too, not only the subsequent response wait.
        if self._ws.sock is not None:
            self._ws.sock.settimeout(self._timeout)
        self._opened.set()

    def _on_error(self, ws: object, error: object) -> None:
        self._error = "IQ_SOCKET_FAILED"
        self._received.set()
        self._opened.set()
        self._authenticated.set()

    def _on_close(self, ws: object, status: object, message: object) -> None:
        self._on_error(ws, None)  # no reconnect, no upstream reason payload

    def _on_message(self, ws: object, message: object) -> None:
        try:
            if not isinstance(message, str) or len(message) > 4 * 1024 * 1024:
                raise ValueError
            raw = _decode_json(message)
            if not isinstance(raw, dict):
                raise ValueError
            name = raw.get("name")
            if name == "authenticated":
                if raw.get("msg") is not True:
                    self._error = "IQ_AUTH_REJECTED"
                self._authenticated.set()
            elif name == self._expected:
                # Some IQ endpoints omit request_id; only one request is ever outstanding.
                if raw.get("request_id") not in (None, "", self._last_request_id):
                    return
                payload = raw.get("msg")
                if name == "candles":
                    if not isinstance(payload, dict):
                        raise ValueError
                    self._reply = payload["candles"]
                else:
                    self._reply = payload
                self._received.set()
            # Profile, balance, orders and unsolicited messages are never retained.
        except (ValueError, TypeError, KeyError, RecursionError):
            self._error = "IQ_PROTOCOL_INVALID"
            self._received.set()
            self._authenticated.set()

    def _wait(self, event: threading.Event) -> None:
        deadline = time.monotonic() + self._timeout
        while not event.wait(min(0.05, max(0, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                self._error = "IQ_COLLECTION_TIMEOUT"
                self.close()
                raise IQClientError(self._error)
        if self._error:
            raise IQClientError(self._error)

    def connect(self) -> None:
        # A terminal backend is not reused: constructing a new IQClient is explicit.
        if self._thread is not None:
            raise IQClientError("IQ_SESSION_ALREADY_USED")
        self._thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={
                "sslopt": {"cert_reqs": ssl.CERT_REQUIRED, "check_hostname": True},
                "ping_interval": 20,
                "ping_timeout": 10,
                "reconnect": 0,
            },
            name="strategy-lab-iq-readonly",
            daemon=True,
        )
        self._thread.start()
        self._wait(self._opened)
        response = self._api.login(self._api.username, self._api.password)
        try:
            if response.status_code != 200 or not response.cookies.get("ssid"):
                raise IQClientError("IQ_AUTH_REJECTED_OR_CHALLENGE")
            self._send("authenticate", {"ssid": response.cookies["ssid"], "protocol": 3})
            self._wait(self._authenticated)
        finally:
            response.close()
            self._api.username = ""
            self._api.password = ""

    def _query(self, expected: str, send: Callable[[], Any]) -> object:
        if self._error or not self._authenticated.is_set():
            raise IQClientError("IQ_NOT_CONNECTED")
        self._expected = expected
        self._reply = None
        self._received.clear()
        try:
            send()
            self._wait(self._received)
            return self._reply
        finally:
            self._expected = None
            self._reply = None

    def catalog(self) -> object:
        return self._query("api_option_init_all_result", self._api.get_api_option_init_all)

    def candles(self, active_id: int, tf_s: int, n: int, end_ts: int) -> object:
        return self._query("candles", lambda: self._api.getcandles(active_id, tf_s, n, end_ts))

    def close(self) -> None:
        self._expected = None
        self._reply = None
        self._api.username = ""
        self._api.password = ""
        self._api.session.cookies.clear()
        self._api.session.close()
        self._ws.close(timeout=2)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                raise IQClientError("IQ_SHUTDOWN_INCOMPLETE")
