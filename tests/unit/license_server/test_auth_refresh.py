"""Unit tests for POST /api/v1/auth/refresh endpoint."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.license_server.errors import ErrorCode
from packages.identity import PkceMaterial, SessionTokens
from tests.unit.license_server.fake_db import FakeConnection, FakeDb


@pytest.fixture
def fake_db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def mock_db_ctx(monkeypatch: pytest.MonkeyPatch, fake_db: FakeDb) -> FakeDb:
    @contextmanager
    def _fake_with_conn() -> Any:
        yield FakeConnection(fake_db)

    monkeypatch.setattr("apps.license_server.routes.auth.with_conn", _fake_with_conn)
    return fake_db


def _login_helper(
    app_client: TestClient,
    fake_db: FakeDb,
    caplog: pytest.LogCaptureFixture,
    email: str,
) -> dict[str, str]:
    pkce = PkceMaterial.create()

    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        start_res = app_client.post(
            "/api/v1/auth/start",
            json={"email": email, "pkce_challenge": pkce.challenge},
        )
    assert start_res.status_code == 200
    challenge_id = start_res.json()["challenge_id"]

    match = re.search(r"OTP for [^:]+:\s*([0-9]{6})", caplog.text)
    assert match is not None
    otp_code = match.group(1)

    cust_id = UUID("33333333-3333-3333-3333-333333333333")
    fake_db.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": email,
        "name": "Refresh Tester",
        "country": "ES",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    fake_db.add_active_license(cust_id)

    verify_res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    assert verify_res.status_code == 200
    data: dict[str, str] = verify_res.json()
    return data


def test_auth_refresh_happy_path(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_data = _login_helper(app_client, mock_db_ctx, caplog, "trader.refresh@example.invalid")
    refresh_token = session_data["refresh_token"]

    refresh_res = app_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )

    assert refresh_res.status_code == 200
    new_data = refresh_res.json()
    assert new_data["user_id"] == session_data["user_id"]
    assert new_data["access_token"] != session_data["access_token"]
    assert new_data["refresh_token"] != session_data["refresh_token"]
    assert "access_expires_at" in new_data

    # Validates SessionTokens schema
    session_tokens = SessionTokens.from_external_payload(new_data)
    assert session_tokens.user_id == session_data["user_id"]


def test_auth_refresh_reuse_revokes_entire_family(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_data = _login_helper(app_client, mock_db_ctx, caplog, "trader.reuse@example.invalid")
    first_refresh = session_data["refresh_token"]

    # First rotation
    res1 = app_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert res1.status_code == 200
    second_refresh = res1.json()["refresh_token"]

    # Replaying first refresh token triggers AUTH_REFRESH_REUSE
    reuse_res = app_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert reuse_res.status_code == 401
    assert reuse_res.json()["error"]["code"] == ErrorCode.AUTH_REFRESH_REUSE.value

    # Subsequent refresh with second refresh token also fails
    res_after = app_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": second_refresh},
    )
    assert res_after.status_code == 401
    assert res_after.json()["error"]["code"] == ErrorCode.AUTH_TOKEN_INVALID.value


def test_auth_refresh_expired_token(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_data = _login_helper(
        app_client, mock_db_ctx, caplog, "trader.expiredtok@example.invalid"
    )
    refresh_token = session_data["refresh_token"]

    # Expire refresh token in database
    for tok in mock_db_ctx.api_tokens.values():
        if tok["kind"] == "refresh":
            tok["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)

    res = app_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == ErrorCode.AUTH_TOKEN_INVALID.value


def test_auth_refresh_license_expired_between_refreshes(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_data = _login_helper(
        app_client, mock_db_ctx, caplog, "trader.licexpired@example.invalid"
    )
    refresh_token = session_data["refresh_token"]

    # License expires before user refreshes
    for lic in mock_db_ctx.licenses:
        lic["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)

    res = app_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == ErrorCode.AUTH_LICENSE_EXPIRED.value
