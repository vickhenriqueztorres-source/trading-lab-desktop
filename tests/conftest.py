from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from packages.domain.models import Broker, Direction, Money, OrderRequest


@pytest.fixture
def order_request() -> OrderRequest:
    return OrderRequest(
        correlation_id="corr-test-001",
        broker=Broker.DERIV,
        account_id="demo-account-1",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1_000, "USD"),
        strategy_id="strategy-test",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
