"""Unit tests for POST /api/v1/auth/start endpoint."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from apps.license_server.errors import ErrorCode
from apps.license_server.ratelimit import get_rate_limiter


@pytest.fixture(autouse=True)
def reset_limiter() -> None:
    get_rate_limiter().reset()


def _mock_db() -> object:
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    @contextmanager
    def fake_with_conn() -> object:
        yield mock_conn

    return fake_with_conn


def test_auth_start_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    app_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("apps.license_server.routes.auth.with_conn", _mock_db())

    pkce = "E9Melhoa2OwvFrGMTJguCH5rtx64KA34UZmbzurvi8U"
    payload = {
        "email": "trader@example.invalid",
        "pkce_challenge": pkce,
    }

    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        response = app_client.post("/api/v1/auth/start", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert "challenge_id" in data
    assert "expires_at" in data
    assert "OTP for tr***@example.invalid:" in caplog.text


def test_auth_start_invalid_pkce(app_client: TestClient) -> None:
    # Too short (<43)
    response = app_client.post(
        "/api/v1/auth/start",
        json={"email": "trader@example.invalid", "pkce_challenge": "too-short"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.AUTH_PKCE_INVALID.value

    # Contains padding '='
    bad_pkce = "E9Melhoa2OwvFrGMTJguCH5rtx64KA34UZmbzurvi8U="
    response = app_client.post(
        "/api/v1/auth/start",
        json={"email": "trader@example.invalid", "pkce_challenge": bad_pkce},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.AUTH_PKCE_INVALID.value

    # Contains invalid characters like '+'
    bad_pkce_chars = "E9Melhoa2OwvFrGMTJguCH5rtx64KA34UZmbzurvi8+abc"
    response = app_client.post(
        "/api/v1/auth/start",
        json={"email": "trader@example.invalid", "pkce_challenge": bad_pkce_chars},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.AUTH_PKCE_INVALID.value


def test_auth_start_invalid_email(app_client: TestClient) -> None:
    pkce = "E9Melhoa2OwvFrGMTJguCH5rtx64KA34UZmbzurvi8U"
    response = app_client.post(
        "/api/v1/auth/start",
        json={"email": "not-an-email", "pkce_challenge": pkce},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.AUTH_CHALLENGE_INVALID.value


def test_auth_start_rate_limit(monkeypatch: pytest.MonkeyPatch, app_client: TestClient) -> None:
    monkeypatch.setattr("apps.license_server.routes.auth.with_conn", _mock_db())

    pkce = "E9Melhoa2OwvFrGMTJguCH5rtx64KA34UZmbzurvi8U"
    payload = {
        "email": "rate.limit.test@example.invalid",
        "pkce_challenge": pkce,
    }

    # 3 attempts succeed
    for _ in range(3):
        res = app_client.post("/api/v1/auth/start", json=payload)
        assert res.status_code == 200

    # 4th attempt must be blocked
    blocked = app_client.post("/api/v1/auth/start", json=payload)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == ErrorCode.RATE_LIMITED.value
    assert "Retry-After" in blocked.headers


def test_auth_start_provider_failure_returns_503(
    monkeypatch: pytest.MonkeyPatch,
    app_client: TestClient,
) -> None:
    monkeypatch.setattr("apps.license_server.routes.auth.with_conn", _mock_db())

    mock_provider = MagicMock()
    mock_provider.send_otp.side_effect = RuntimeError("SMTP host down")
    monkeypatch.setattr(
        "apps.license_server.routes.auth.get_mail_provider",
        lambda _settings: mock_provider,
    )

    pkce = "E9Melhoa2OwvFrGMTJguCH5rtx64KA34UZmbzurvi8U"
    payload = {
        "email": "fail@example.invalid",
        "pkce_challenge": pkce,
    }

    response = app_client.post("/api/v1/auth/start", json=payload)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == ErrorCode.AUTH_SERVICE_UNAVAILABLE.value
