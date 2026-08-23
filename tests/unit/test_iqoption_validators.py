from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.iqoption_worker.schema import IQOptionErrorCategory, IQOptionWorkerError
from packages.brokers.iqoption.validators import (
    PRACTICE_BALANCE_TYPE,
    REAL_BALANCE_TYPE,
    validate_iqoption_account,
    validate_iqoption_order_command,
)
from packages.domain.models import Broker, Direction, Money, OrderCommand


def test_validate_iqoption_account_practice_success() -> None:
    practice_payload = {
        "balance_type": PRACTICE_BALANCE_TYPE,
        "is_demo": True,
        "account_type": "practice",
        "balance": "10000.00",
        "currency": "USD",
    }
    # Must not raise
    validate_iqoption_account(practice_payload)


def test_validate_iqoption_account_real_balance_type_fails_closed() -> None:
    real_payload = {
        "balance_type": REAL_BALANCE_TYPE,
        "is_demo": False,
        "account_type": "real",
        "balance": "5000.00",
        "currency": "USD",
    }
    with pytest.raises(IQOptionWorkerError) as exc:
        validate_iqoption_account(real_payload)
    assert exc.value.category == IQOptionErrorCategory.ACCOUNT_MODE_FORBIDDEN
    assert exc.value.reason_code == "IQOPTION_REAL_ACCOUNT_FORBIDDEN"


def test_validate_iqoption_account_invalid_mode_fails_closed() -> None:
    invalid_payload = {
        "balance_type": 2,
        "account_type": "tournament",
    }
    with pytest.raises(IQOptionWorkerError) as exc:
        validate_iqoption_account(invalid_payload)
    assert exc.value.category == IQOptionErrorCategory.ACCOUNT_MODE_FORBIDDEN
    assert exc.value.reason_code == "IQOPTION_PRACTICE_ACCOUNT_REQUIRED"


def test_validate_iqoption_order_command_success() -> None:
    command = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-001",
        order_id="order-001",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_123",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    validate_iqoption_order_command(command)


def test_validate_iqoption_order_command_mismatched_broker() -> None:
    command = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-002",
        order_id="order-002",
        broker=Broker.DERIV,
        account_id="PRACTICE_123",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    with pytest.raises(IQOptionWorkerError) as exc:
        validate_iqoption_order_command(command)
    assert exc.value.reason_code == "IQOPTION_BROKER_MISMATCH"


def test_validate_iqoption_order_command_real_account_id_forbidden() -> None:
    command = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-003",
        order_id="order-003",
        broker=Broker.IQ_OPTION,
        account_id="REAL_ACCOUNT_999",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    with pytest.raises(IQOptionWorkerError) as exc:
        validate_iqoption_order_command(command)
    assert exc.value.reason_code == "IQOPTION_REAL_ACCOUNT_FORBIDDEN"
