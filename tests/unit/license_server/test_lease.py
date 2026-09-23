"""Unit tests for /api/v1/lease endpoints."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
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

    monkeypatch.setattr("apps.license_server.routes.lease.with_conn", _fake_with_conn)
    monkeypatch.setattr("apps.license_server.dependencies.with_conn", _fake_with_conn)
    return fake_db


def test_lease_issue_missing_auth(app_client: TestClient) -> None:
    res = app_client.post(
        "/api/v1/lease/issue",
        json={
            "device_id": "dev-1",
            "challenge_id": str(uuid4()),
            "signature_b64": "sig",
        },
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == ErrorCode.AUTH_TOKEN_INVALID.value


def test_lease_issue_malformed_challenge_id(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
) -> None:
    cust_id = uuid4()
    tokens = issue_family(FakeConnection(mock_db_ctx), cust_id)
    headers = {"Authorization": f"Bearer {tokens.access_token}"}

    res = app_client.post(
        "/api/v1/lease/issue",
        json={
            "device_id": "dev-1",
            "challenge_id": "not-a-valid-uuid",
            "signature_b64": "sig",
        },
        headers=headers,
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_PROOF_INVALID.value


def test_lease_revoked_endpoint_fails_closed(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
) -> None:
    # 1. Malformed UUID fails closed -> revoked: True
    res_malformed = app_client.get("/api/v1/lease/revoked/not-a-uuid")
    assert res_malformed.status_code == 200
    assert res_malformed.json() == {"revoked": True}

    # 2. Non-existent UUID fails closed -> revoked: True
    unknown_id = str(uuid4())
    res_unknown = app_client.get(f"/api/v1/lease/revoked/{unknown_id}")
    assert res_unknown.status_code == 200
    assert res_unknown.json() == {"revoked": True}

    # 3. Active lease in db -> revoked: False
    active_lease_id = str(uuid4())
    mock_db_ctx.leases[active_lease_id] = {
        "lease_id": active_lease_id,
        "customer_id": uuid4(),
        "device_id": "dev-1",
        "issued_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC),
        "revoked": False,
        "created_at": datetime.now(UTC),
    }
    res_active = app_client.get(f"/api/v1/lease/revoked/{active_lease_id}")
    assert res_active.status_code == 200
    assert res_active.json() == {"revoked": False}

    # 4. Revoked lease in db -> revoked: True
    revoked_lease_id = str(uuid4())
    mock_db_ctx.leases[revoked_lease_id] = {
        "lease_id": revoked_lease_id,
        "customer_id": uuid4(),
        "device_id": "dev-1",
        "issued_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC),
        "revoked": True,
        "created_at": datetime.now(UTC),
    }
    res_rev = app_client.get(f"/api/v1/lease/revoked/{revoked_lease_id}")
    assert res_rev.status_code == 200
    assert res_rev.json() == {"revoked": True}
