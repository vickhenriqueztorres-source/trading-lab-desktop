from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from urllib.parse import quote, urlsplit

from apps.deriv_worker.mapper import map_account_balance
from apps.deriv_worker.public_session import PublicDerivSession
from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError
from apps.deriv_worker.validators import (
    DERIV_HOST,
    validate_deriv_account,
    validate_deriv_ws_url,
)
from apps.deriv_worker.websocket_client import (
    DerivReadTransport,
    DerivWebSocketClient,
    ReadOnlyRetryPolicy,
)
from packages.domain.market import (
    BrokerAccountBalance,
    BrokerCapabilities,
    BrokerConnectionMode,
    MarketDataHealthState,
)
from packages.domain.models import Broker

DERIV_REST_BASE = f"https://{DERIV_HOST}"


@dataclass(frozen=True, slots=True)
class SecretValue:
    _value: str

    def __post_init__(self) -> None:
        if not self._value:
            raise ValueError("secret value cannot be empty")

    def reveal_for_transport(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue([REDACTED])"

    def __str__(self) -> str:
        return "[REDACTED]"


class DerivAccessTokenProvider(Protocol):
    def get_access_token(self) -> SecretValue: ...


class EnvironmentDerivTokenProvider:
    """Explicit external-demo bootstrap; the value is never logged or serialized."""

    def __init__(self, variable_name: str = "DUALTRADE_DERIV_DEMO_TOKEN") -> None:
        self._variable_name = variable_name

    def get_access_token(self) -> SecretValue:
        value = os.environ.get(self._variable_name)
        if value is None or not value.strip():
            raise DerivWorkerError(
                DerivErrorCategory.AUTH_FAILED,
                "DERIV_DEMO_TOKEN_UNAVAILABLE",
            )
        return SecretValue(value.strip())


class DerivOptionsRestPort(Protocol):
    def get_accounts(self, token: SecretValue, app_id: str) -> Mapping[str, object]: ...

    def request_otp(
        self, token: SecretValue, app_id: str, account_id: str
    ) -> Mapping[str, object]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise DerivWorkerError(
            DerivErrorCategory.NETWORK_ERROR,
            "DERIV_REST_REDIRECT_FORBIDDEN",
        )


class DerivOptionsRestClient:
    """Minimal authenticated REST client for account discovery and demo OTP only."""

    def __init__(self, *, timeout: float = 5.0) -> None:
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect())

    def get_accounts(self, token: SecretValue, app_id: str) -> Mapping[str, object]:
        return self._request(
            "GET",
            "/trading/v1/options/accounts",
            token,
            app_id,
        )

    def request_otp(self, token: SecretValue, app_id: str, account_id: str) -> Mapping[str, object]:
        safe_account = quote(account_id, safe="")
        return self._request(
            "POST",
            f"/trading/v1/options/accounts/{safe_account}/otp",
            token,
            app_id,
        )

    def _request(
        self,
        method: str,
        path: str,
        token: SecretValue,
        app_id: str,
    ) -> Mapping[str, object]:
        if not app_id:
            raise ValueError("Deriv App ID is required")
        url = f"{DERIV_REST_BASE}{path}"
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != DERIV_HOST:
            raise DerivWorkerError(
                DerivErrorCategory.NETWORK_ERROR,
                "DERIV_REST_HOST_FORBIDDEN",
            )
        request = urllib.request.Request(
            url,
            method=method,
            headers={
                "Authorization": f"Bearer {token.reveal_for_transport()}",
                "Deriv-App-ID": app_id,
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                parsed_body = json.loads(
                    response.read().decode("utf-8"),
                    parse_float=Decimal,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"non-finite JSON number: {value}")
                    ),
                )
        except DerivWorkerError:
            raise
        except urllib.error.HTTPError as exc:
            category = (
                DerivErrorCategory.AUTH_FAILED
                if exc.code in {401, 403}
                else DerivErrorCategory.SERVER_ERROR
            )
            raise DerivWorkerError(category, f"DERIV_{category.value}") from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise DerivWorkerError(
                DerivErrorCategory.NETWORK_ERROR,
                "DERIV_NETWORK_ERROR",
            ) from exc
        if not isinstance(parsed_body, dict):
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        return parsed_body


class DemoDerivSession:
    def __init__(
        self,
        token_provider: DerivAccessTokenProvider,
        rest_client: DerivOptionsRestPort,
        app_id: str,
        *,
        expected_account_type: str = "demo",
        transport_factory: Callable[[str], DerivReadTransport] | None = None,
        on_forbidden_real: Callable[[str], None] = lambda _reason: None,
    ) -> None:
        self._token_provider = token_provider
        self._rest = rest_client
        self._app_id = app_id
        if expected_account_type not in {"demo", "real"}:
            raise ValueError("Deriv account type is invalid")
        self._expected_account_type = expected_account_type
        self._transport_factory = transport_factory or (
            lambda url: DerivWebSocketClient(
                url,
                demo_authenticated=True,
                account_type=self._expected_account_type,
            )
        )
        self._on_forbidden_real = on_forbidden_real
        self._transport: DerivReadTransport | None = None
        self.account_id: str | None = None

    @property
    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            broker=Broker.DERIV,
            connection_mode=BrokerConnectionMode.DEMO_AUTH_READ_ONLY,
            authenticated=self._transport is not None,
            can_trade=False,
            supports_ticks=True,
            supports_tick_history=True,
            supports_candles=True,
            supports_active_symbols=True,
            supports_contract_metadata=True,
            supports_server_time=True,
            supported_timeframes=(
                60,
                120,
                180,
                300,
                600,
                900,
                1800,
                3600,
                7200,
                14400,
                28800,
                86400,
            ),
        )

    def open(self, explicit_account_id: str) -> DerivReadTransport:
        if not explicit_account_id:
            raise ValueError("explicit demo account_id is required")
        token = self._token_provider.get_access_token()
        accounts_payload = self._rest.get_accounts(token, self._app_id)
        accounts = accounts_payload.get("data")
        if not isinstance(accounts, list):
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        selected: Mapping[str, object] | None = None
        for item in accounts:
            if not isinstance(item, dict):
                raise DerivWorkerError(
                    DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                    "DERIV_SCHEMA_INCOMPATIBLE",
                )
            if item.get("account_id") == explicit_account_id:
                selected = item
        if selected is None:
            raise DerivWorkerError(
                DerivErrorCategory.AUTH_FAILED,
                "DERIV_DEMO_ACCOUNT_NOT_FOUND",
            )
        try:
            validate_deriv_account(
                selected,
                expected_account_type=self._expected_account_type,
            )
        except DerivWorkerError:
            self._on_forbidden_real("DERIV_ACCOUNT_TYPE_MISMATCH")
            raise
        otp_payload = self._rest.request_otp(token, self._app_id, explicit_account_id)
        data = otp_payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("url"), str):
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            )
        try:
            url = validate_deriv_ws_url(
                str(data["url"]),
                expected_account_type=self._expected_account_type,
            )
        except DerivWorkerError as exc:
            if exc.reason_code == "DERIV_REAL_WS_FORBIDDEN":
                self._on_forbidden_real(exc.reason_code)
            raise
        transport = self._transport_factory(url)
        transport.reconnect()
        self._transport = transport
        self.account_id = explicit_account_id
        return transport

    def close(self) -> None:
        transport = self._transport
        self._transport = None
        self.account_id = None
        if transport is not None:
            transport.close()


class DemoReadOnlyDerivSession(PublicDerivSession):
    """Authenticated account reader for market data, server clock and balance."""

    def __init__(
        self,
        transport: DerivReadTransport,
        account_id: str = "VRTC_DEMO",
        *,
        account_type: str = "demo",
    ) -> None:
        super().__init__(
            transport,
            retry_policy=ReadOnlyRetryPolicy(
                max_attempts=1,
                base_delay_seconds=0.05,
                max_delay_seconds=0.05,
            ),
            # The OTP websocket is single-use and therefore must not be blindly
            # reconnected.  Give the initial authenticated reads enough time to
            # survive normal internet jitter instead of failing at the public
            # session's intentionally aggressive two-second default.
            request_timeout=8.0,
        )
        self.account_id = account_id
        if account_type not in {"demo", "real"}:
            raise ValueError("Deriv account type is invalid")
        self.account_type = account_type
        self._balance: BrokerAccountBalance | None = None
        self._balance_subscribed = False

    def connect(self) -> None:
        """Validate an already OTP-authenticated transport without reusing its URL."""

        self.health = MarketDataHealthState.WARMING_UP
        self.ping()
        self.last_clock = self.clock()
        self.symbols = self.active_symbols()
        self.health = MarketDataHealthState.HEALTHY

    def reconnect(self) -> None:
        self.health = MarketDataHealthState.DISCONNECTED
        raise DerivWorkerError(
            DerivErrorCategory.AUTH_FAILED,
            "DERIV_DEMO_REAUTH_REQUIRED",
        )

    @property
    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            broker=Broker.DERIV,
            connection_mode=(
                BrokerConnectionMode.DEMO_AUTH_READ_ONLY
                if self.account_type == "demo"
                else BrokerConnectionMode.REAL_AUTH_READ_ONLY
            ),
            authenticated=True,
            can_trade=False,
            supports_ticks=True,
            supports_tick_history=True,
            supports_candles=True,
            supports_active_symbols=True,
            supports_contract_metadata=True,
            supports_server_time=True,
            supported_timeframes=super().capabilities.supported_timeframes,
        )

    def account_balance(self) -> BrokerAccountBalance:
        latest = self._transport.receive_account(timeout=0.0)
        while latest is not None:
            self._balance = map_account_balance(latest, self._now(), self.account_type)
            latest = self._transport.receive_account(timeout=0.0)
        if not self._balance_subscribed:
            response = self._read_request(
                DerivOperation.BALANCE,
                {"balance": 1, "subscribe": 1},
            )
            self._balance = map_account_balance(response, self._now(), self.account_type)
            self._balance_subscribed = True
        if self._balance is None:
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_BALANCE_UNAVAILABLE",
            )
        return self._balance
