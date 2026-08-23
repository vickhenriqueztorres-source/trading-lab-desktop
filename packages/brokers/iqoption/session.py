from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

from apps.iqoption_worker.schema import IQOptionErrorCategory, IQOptionWorkerError
from packages.brokers.iqoption.fake_transport import FakeIQOptionTransport
from packages.brokers.iqoption.validators import validate_iqoption_account
from packages.domain.market import BrokerAccountBalance, BrokerClockSnapshot


class IQOptionPracticeSession:
    """Manages an authenticated IQ Option Practice session."""

    def __init__(
        self,
        transport: FakeIQOptionTransport,
        *,
        user_id: int | None = 987654321,
    ) -> None:
        self._transport = transport
        self._user_id = user_id
        self._connected = False
        self._balance: BrokerAccountBalance | None = None
        self._clock: BrokerClockSnapshot | None = None

    @property
    def transport(self) -> FakeIQOptionTransport:
        return self._transport

    @property
    def is_connected(self) -> bool:
        return self._connected and self._transport.is_connected

    def connect(self) -> None:
        response = self._transport.request("auth", {"ssid": "dummy-practice-session"})
        if not response.get("isSuccessful"):
            raise IQOptionWorkerError(
                IQOptionErrorCategory.ACCOUNT_MODE_FORBIDDEN,
                "IQOPTION_AUTH_FAILED",
                response.get("message", "Authentication failed"),
            )
        result = response.get("result", {})
        validate_iqoption_account(result)
        self._connected = True

    def get_balance(self) -> BrokerAccountBalance:
        if not self._connected:
            raise IQOptionWorkerError(
                IQOptionErrorCategory.NETWORK_ERROR,
                "IQOPTION_DISCONNECTED",
            )
        response = self._transport.request("get-balances", {})
        validate_iqoption_account(response)

        raw_balance = str(response.get("balance", "10000.00"))
        currency = str(response.get("currency", "USD")).upper()
        decimal_balance = Decimal(raw_balance)
        minor_units = int(decimal_balance * Decimal(100))

        balance = BrokerAccountBalance(
            balance_minor_units=minor_units,
            currency=currency,
            account_type="DEMO",
            observed_at_utc=datetime.now(UTC),
        )
        self._balance = balance
        return balance

    def get_clock(self) -> BrokerClockSnapshot:
        if not self._connected:
            raise IQOptionWorkerError(
                IQOptionErrorCategory.NETWORK_ERROR,
                "IQOPTION_DISCONNECTED",
            )
        start_time = time.monotonic()
        response = self._transport.request("get_server_time", {})
        end_time = time.monotonic()

        server_epoch = int(response.get("server_time", int(time.time())))
        rtt = end_time - start_time
        local_now = datetime.now(UTC)
        estimated_offset = Decimal(server_epoch) - Decimal(local_now.timestamp())

        clock = BrokerClockSnapshot(
            server_epoch=server_epoch,
            local_received_at=local_now,
            round_trip_seconds=rtt,
            estimated_offset_seconds=estimated_offset,
        )
        self._clock = clock
        return clock

    def close(self) -> None:
        self._connected = False
        self._transport.close()
