"""Unit tests for POST /api/v1/auth/verify endpoint."""

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


def test_auth_verify_happy_path(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "trader.verify@example.invalid"
    pkce = PkceMaterial.create()

    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        start_res = app_client.post(
            "/api/v1/auth/start",
            json={"email": email, "pkce_challenge": pkce.challenge},
        )
    assert start_res.status_code == 200
    challenge_id = start_res.json()["challenge_id"]

    # Extract OTP from log
    match = re.search(r"OTP for [^:]+:\s*([0-9]{6})", caplog.text)
    assert match is not None
    otp_code = match.group(1)

    # Customer pays and receives an active license
    # First customer upsert will resolve customer_id: we can pre-create or let verify create
    # Let's see: verify creates customer, then checks license.
    # So we simulate the customer already existing with an active license:
    cust_id = UUID("11111111-1111-1111-1111-111111111111")
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": email,
        "name": "Verified Trader",
        "country": "ES",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    mock_db_ctx.add_active_license(cust_id)

    # Verify
    verify_res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )

    assert verify_res.status_code == 200
    data = verify_res.json()
    assert data["user_id"] == str(cust_id)
    assert "access_token" in data
    assert "refresh_token" in data
    assert "access_expires_at" in data

    # Must conform byte-for-byte with packages.identity.SessionTokens
    session_tokens = SessionTokens.from_external_payload(data)
    assert session_tokens.user_id == str(cust_id)
    assert session_tokens.access_token.reveal_text() == data["access_token"]
    assert session_tokens.refresh_token.reveal_text() == data["refresh_token"]


def test_auth_verify_wrong_otp_and_lockout_after_5_attempts(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
) -> None:
    email = "attempts.trader@example.invalid"
    pkce = PkceMaterial.create()

    start_res = app_client.post(
        "/api/v1/auth/start",
        json={"email": email, "pkce_challenge": pkce.challenge},
    )
    challenge_id = start_res.json()["challenge_id"]

    # Attempts 1 to 5: 401 AUTH_OTP_INVALID
    for _ in range(5):
        res = app_client.post(
            "/api/v1/auth/verify",
            json={
                "challenge_id": challenge_id,
                "otp_code": "000000",
                "pkce_verifier": pkce.verifier.reveal_text(),
            },
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == ErrorCode.AUTH_OTP_INVALID.value

    # Attempt 6: Lockout -> 400 AUTH_CHALLENGE_INVALID
    res6 = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": "000000",
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    assert res6.status_code == 400
    assert res6.json()["error"]["code"] == ErrorCode.AUTH_CHALLENGE_INVALID.value


def test_auth_verify_invalid_pkce(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "pkce.trader@example.invalid"
    pkce = PkceMaterial.create()

    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        start_res = app_client.post(
            "/api/v1/auth/start",
            json={"email": email, "pkce_challenge": pkce.challenge},
        )
    challenge_id = start_res.json()["challenge_id"]

    match = re.search(r"OTP for [^:]+:\s*([0-9]{6})", caplog.text)
    assert match is not None
    otp_code = match.group(1)

    # Provide mismatched PKCE verifier
    wrong_pkce = PkceMaterial.create()
    res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": wrong_pkce.verifier.reveal_text(),
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == ErrorCode.AUTH_PKCE_INVALID.value


def test_auth_verify_challenge_already_consumed(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "replay.trader@example.invalid"
    pkce = PkceMaterial.create()

    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        start_res = app_client.post(
            "/api/v1/auth/start",
            json={"email": email, "pkce_challenge": pkce.challenge},
        )
    challenge_id = start_res.json()["challenge_id"]

    match = re.search(r"OTP for [^:]+:\s*([0-9]{6})", caplog.text)
    assert match is not None
    otp_code = match.group(1)

    cust_id = UUID("22222222-2222-2222-2222-222222222222")
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": email,
        "name": None,
        "country": None,
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    mock_db_ctx.add_active_license(cust_id)

    # First verify succeeds
    res1 = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    assert res1.status_code == 200

    # Replay verify fails
    res2 = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    assert res2.status_code == 400
    assert res2.json()["error"]["code"] == ErrorCode.AUTH_CHALLENGE_INVALID.value


def test_auth_verify_expired_challenge(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "expired.trader@example.invalid"
    pkce = PkceMaterial.create()

    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        start_res = app_client.post(
            "/api/v1/auth/start",
            json={"email": email, "pkce_challenge": pkce.challenge},
        )
    challenge_id = start_res.json()["challenge_id"]

    match = re.search(r"OTP for [^:]+:\s*([0-9]{6})", caplog.text)
    assert match is not None
    otp_code = match.group(1)

    # Force challenge expiration
    mock_db_ctx.otp_challenges[challenge_id]["expires_at"] = datetime.now(UTC) - timedelta(
        seconds=1
    )

    res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == ErrorCode.AUTH_CHALLENGE_EXPIRED.value


def test_auth_verify_customer_without_active_license_returns_403(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "unlicensed.trader@example.invalid"
    pkce = PkceMaterial.create()

    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        start_res = app_client.post(
            "/api/v1/auth/start",
            json={"email": email, "pkce_challenge": pkce.challenge},
        )
    challenge_id = start_res.json()["challenge_id"]

    match = re.search(r"OTP for [^:]+:\s*([0-9]{6})", caplog.text)
    assert match is not None
    otp_code = match.group(1)

    # User enters valid OTP and PKCE, but has no license
    res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )

    assert res.status_code == 403
    err = res.json()["error"]
    assert err["code"] == ErrorCode.AUTH_LICENSE_EXPIRED.value
    assert "Tu acceso no está activo" in err["message"]
