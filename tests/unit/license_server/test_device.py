"""Unit tests for /api/v1/device endpoints."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from apps.license_server.errors import ErrorCode
from apps.license_server.tokens import issue_family
from tests.unit.license_server.fake_db import FakeConnection, FakeDb


@pytest.fixture
def fake_db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def mock_db_ctx(monkeypatch: pytest.MonkeyPatch, fake_db: FakeDb) -> FakeDb:
    @contextmanager
    def _fake_with_conn() -> Any:
        yield FakeConnection(fake_db)

    monkeypatch.setattr("apps.license_server.routes.device.with_conn", _fake_with_conn)
    monkeypatch.setattr("apps.license_server.dependencies.with_conn", _fake_with_conn)
    return fake_db


def _auth_header(conn: FakeConnection, customer_id: UUID) -> dict[str, str]:
    tokens = issue_family(conn, customer_id)
    return {"Authorization": f"Bearer {tokens.access_token}"}


def test_device_register_missing_auth(app_client: TestClient) -> None:
    res = app_client.post(
        "/api/v1/device/register",
        json={"device_id": "dev-1", "public_key_b64": "invalid"},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == ErrorCode.AUTH_TOKEN_INVALID.value


def test_device_register_invalid_public_key(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
) -> None:
    cust_id = uuid4()
    headers = _auth_header(FakeConnection(mock_db_ctx), cust_id)

    # Invalid b64
    res = app_client.post(
        "/api/v1/device/register",
        json={"device_id": "dev-1", "public_key_b64": "not-valid-base64!"},
        headers=headers,
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_INVALID.value

    # Valid b64 but wrong length (10 bytes instead of 32)
    short_b64 = base64.urlsafe_b64encode(b"0123456789").decode("ascii")
    res2 = app_client.post(
        "/api/v1/device/register",
        json={"device_id": "dev-1", "public_key_b64": short_b64},
        headers=headers,
    )
    assert res2.status_code == 400
    assert res2.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_INVALID.value


def test_device_register_different_key_for_same_id(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
) -> None:
    cust_id = uuid4()
    mock_db_ctx.add_active_license(cust_id)
    headers = _auth_header(FakeConnection(mock_db_ctx), cust_id)

    key1 = Ed25519PrivateKey.generate()
    pub1_b64 = base64.urlsafe_b64encode(
        key1.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode("ascii")

    # Register first time
    res = app_client.post(
        "/api/v1/device/register",
        json={"device_id": "dev-shared", "public_key_b64": pub1_b64},
        headers=headers,
    )
    assert res.status_code == 204

    # Try registering same device_id with different key
    key2 = Ed25519PrivateKey.generate()
    pub2_b64 = base64.urlsafe_b64encode(
        key2.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode("ascii")

    res2 = app_client.post(
        "/api/v1/device/register",
        json={"device_id": "dev-shared", "public_key_b64": pub2_b64},
        headers=headers,
    )
    assert res2.status_code == 400
    assert res2.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_INVALID.value


def test_device_challenge_device_not_found(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
) -> None:
    cust_id = uuid4()
    headers = _auth_header(FakeConnection(mock_db_ctx), cust_id)

    res = app_client.post(
        "/api/v1/device/challenge",
        json={"device_id": "unknown-device"},
        headers=headers,
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_INVALID.value


def test_device_challenge_device_other_customer(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
) -> None:
    cust1 = uuid4()
    cust2 = uuid4()
    mock_db_ctx.add_active_license(cust1)
    mock_db_ctx.add_active_license(cust2)

    headers1 = _auth_header(FakeConnection(mock_db_ctx), cust1)
    headers2 = _auth_header(FakeConnection(mock_db_ctx), cust2)

    dev_key = Ed25519PrivateKey.generate()
    raw_key = dev_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    pub_b64 = base64.urlsafe_b64encode(raw_key).decode("ascii")

    app_client.post(
        "/api/v1/device/register",
        json={"device_id": "cust1-dev", "public_key_b64": pub_b64},
        headers=headers1,
    )

    # Cust2 tries to challenge cust1's device
    res = app_client.post(
        "/api/v1/device/challenge",
        json={"device_id": "cust1-dev"},
        headers=headers2,
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_INVALID.value
