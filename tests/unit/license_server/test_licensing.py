"""Unit tests for customer license verification and enforcement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.license_server.errors import ApiError, ErrorCode
from apps.license_server.licensing import active_license, require_active_license
from tests.unit.license_server.fake_db import FakeConnection, FakeDb


@pytest.fixture
def fake_db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def conn(fake_db: FakeDb) -> FakeConnection:
    return FakeConnection(fake_db)


def test_active_license_found(conn: FakeConnection, fake_db: FakeDb) -> None:
    cust_id = uuid4()
    fake_db.add_active_license(cust_id, plan="PRO", real_mode_allowed=True)

    lic = active_license(conn, cust_id)
    assert lic is not None
    assert lic.customer_id == cust_id
    assert lic.plan == "PRO"
    assert lic.real_mode_allowed is True
    assert lic.broker_access == ("DERIV", "IQ_OPTION")

    # require_active_license returns it directly
    required = require_active_license(conn, cust_id)
    assert required.id == lic.id


def test_active_license_missing(conn: FakeConnection) -> None:
    cust_id = uuid4()
    assert active_license(conn, cust_id) is None

    with pytest.raises(ApiError) as exc_info:
        require_active_license(conn, cust_id)
    assert exc_info.value.code == ErrorCode.AUTH_LICENSE_EXPIRED
    assert exc_info.value.http_status == 403
    assert "Tu acceso no está activo" in exc_info.value.message


def test_license_expired(conn: FakeConnection, fake_db: FakeDb) -> None:
    cust_id = uuid4()
    now = datetime.now(UTC)
    fake_db.add_active_license(
        cust_id,
        starts_at=now - timedelta(days=60),
        expires_at=now - timedelta(seconds=1),
    )

    assert active_license(conn, cust_id) is None
    with pytest.raises(ApiError) as exc_info:
        require_active_license(conn, cust_id)
    assert exc_info.value.code == ErrorCode.AUTH_LICENSE_EXPIRED


def test_license_future_start(conn: FakeConnection, fake_db: FakeDb) -> None:
    cust_id = uuid4()
    now = datetime.now(UTC)
    fake_db.add_active_license(
        cust_id,
        starts_at=now + timedelta(days=1),
        expires_at=now + timedelta(days=31),
    )

    assert active_license(conn, cust_id) is None
    with pytest.raises(ApiError) as exc_info:
        require_active_license(conn, cust_id)
    assert exc_info.value.code == ErrorCode.AUTH_LICENSE_EXPIRED


def test_license_not_active_status(conn: FakeConnection, fake_db: FakeDb) -> None:
    cust_id = uuid4()
    lic = fake_db.add_active_license(cust_id)
    lic["status"] = "suspended"

    assert active_license(conn, cust_id) is None
    with pytest.raises(ApiError) as exc_info:
        require_active_license(conn, cust_id)
    assert exc_info.value.code == ErrorCode.AUTH_LICENSE_EXPIRED
