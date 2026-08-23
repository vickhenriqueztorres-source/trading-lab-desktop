from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import (
    ReadOnlyWorkerSpec,
    ReadOnlyWorkerSupervisor,
)
from apps.deriv_worker.demo_session import DemoReadOnlyDerivSession
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.mapper import map_demo_balance
from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.schema import DerivWorkerError
from packages.protocol import EndpointRole


def test_demo_balance_uses_decimal_then_exact_minor_units_and_stream_updates() -> None:
    transport = FakeDerivTransport(
        demo_authenticated=True,
        demo_balance="10000.25",
        demo_currency="usd",
    )
    session = DemoReadOnlyDerivSession(transport)

    first = session.account_balance()
    transport.emit_balance("9999.75", "USD")
    updated = session.account_balance()

    assert first.balance_minor_units == 1_000_025
    assert first.currency == "USD"
    assert updated.balance_minor_units == 999_975
    assert transport.trading_write_requests == 0


def test_balance_mapper_rejects_float_and_unsupported_precision() -> None:
    observed = datetime(2026, 8, 21, tzinfo=UTC)
    with pytest.raises(DerivWorkerError) as float_error:
        map_demo_balance(
            {"msg_type": "balance", "balance": {"balance": 10.5, "currency": "USD"}},
            observed,
        )
    assert float_error.value.reason_code == "DERIV_SCHEMA_INCOMPATIBLE"

    with pytest.raises(DerivWorkerError) as precision_error:
        map_demo_balance(
            {
                "msg_type": "balance",
                "balance": {"balance": Decimal("10.001"), "currency": "USD"},
            },
            observed,
        )
    assert precision_error.value.reason_code == "DERIV_BALANCE_PRECISION_UNSUPPORTED"


def test_demo_timeout_does_not_reuse_single_use_otp_or_retry_blindly() -> None:
    transport = FakeDerivTransport(
        FakeDerivScenario.DISCONNECT,
        demo_authenticated=True,
    )
    session = DemoReadOnlyDerivSession(transport)

    with pytest.raises(DerivWorkerError) as captured:
        session.account_balance()

    assert captured.value.reason_code == "DERIV_NETWORK_ERROR"
    assert transport.operation_counts[DerivOperation.BALANCE] == 1
    assert transport.reconnect_count == 0


def test_demo_connect_validates_existing_socket_and_reauth_is_explicit() -> None:
    transport = FakeDerivTransport(demo_authenticated=True)
    session = DemoReadOnlyDerivSession(transport)

    session.connect()
    assert transport.reconnect_count == 0

    with pytest.raises(DerivWorkerError) as captured:
        session.reconnect()
    assert captured.value.reason_code == "DERIV_DEMO_REAUTH_REQUIRED"


def test_fake_demo_subprocess_announces_read_only_and_serves_clock_and_balance() -> None:
    supervisor = ReadOnlyWorkerSupervisor(
        HealthGate(),
        ReadOnlyWorkerSpec(
            module="apps.deriv_worker",
            role=EndpointRole.DERIV_WORKER,
            broker="DERIV",
            extra_arguments=("--deriv-transport", "fake-demo"),
        ),
    )
    supervisor.start()
    try:
        client = supervisor.client
        balance = client.broker_balance()
        clock = client.broker_clock()

        assert client.capabilities.connection_mode == "DEMO_AUTH_READ_ONLY"
        assert client.capabilities.can_submit_orders is False
        assert balance.balance_minor_units == 1_000_000
        assert balance.account_type == "DEMO"
        assert clock.is_synced
    finally:
        supervisor.shutdown()
