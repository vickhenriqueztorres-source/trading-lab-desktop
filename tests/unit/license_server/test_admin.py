"""Unit tests for the admin panel (/admin)."""

from __future__ import annotations

import base64
import logging
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from apps.license_server.admin_auth import sign_csrf, sign_session
from apps.license_server.errors import ErrorCode
from apps.license_server.settings import Settings
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

    monkeypatch.setattr("apps.license_server.routes.admin.with_conn", _fake_with_conn)
    monkeypatch.setattr("apps.license_server.routes.auth.with_conn", _fake_with_conn)
    monkeypatch.setattr("apps.license_server.routes.device.with_conn", _fake_with_conn)
    monkeypatch.setattr("apps.license_server.routes.lease.with_conn", _fake_with_conn)
    monkeypatch.setattr("apps.license_server.dependencies.with_conn", _fake_with_conn)
    return fake_db


def _pubkey_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii")


def test_unauthenticated_admin_redirects(app_client: TestClient) -> None:
    res = app_client.get("/admin", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/admin/login"


def test_admin_login_wrong_email_does_not_send_email(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    csrf = sign_csrf(test_settings.admin_session_secret)
    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        res = app_client.post(
            "/admin/login",
            data={"email": "attacker@evil.com", "csrf_token": csrf},
            follow_redirects=False,
        )
    assert res.status_code == 303
    assert "challenge_id=" in res.headers["location"]
    # Verify no OTP was logged for attacker
    assert "attacker@evil.com" not in caplog.text


def test_admin_login_success(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    csrf = sign_csrf(test_settings.admin_session_secret)

    with caplog.at_level(logging.INFO, logger="license_server.mail"):
        res = app_client.post(
            "/admin/login",
            data={"email": test_settings.admin_email, "csrf_token": csrf},
            follow_redirects=False,
        )
    assert res.status_code == 303
    location = res.headers["location"]
    match_cid = re.search(r"challenge_id=([a-f0-9\-]+)", location)
    assert match_cid is not None
    challenge_id = match_cid.group(1)

    match_otp = re.search(r"OTP for [^:]+:\s*([0-9]{6})", caplog.text)
    assert match_otp is not None
    otp_code = match_otp.group(1)

    # Verify OTP
    verify_res = app_client.post(
        "/admin/login/verify",
        data={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert verify_res.status_code == 303
    assert verify_res.headers["location"] == "/admin"
    assert "admin_session" in verify_res.cookies

    # Access dashboard with cookie
    dash_res = app_client.get(
        "/admin",
        cookies={"admin_session": verify_res.cookies["admin_session"]},
    )
    assert dash_res.status_code == 200
    assert "Trading Lab Admin" in dash_res.text


def test_admin_create_customer_and_active_license(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    test_settings: Settings,
) -> None:
    admin_tok = sign_session(test_settings.admin_email, test_settings.admin_session_secret)
    csrf = sign_csrf(test_settings.admin_session_secret)

    post_res = app_client.post(
        "/admin/customers/new",
        data={
            "email": "newbie.trader@example.invalid",
            "name": "Newbie Trader",
            "country": "ES",
            "days": 30,
            "real_mode_allowed": "true",
            "notes": "Test customer account",
            "csrf_token": csrf,
        },
        cookies={"admin_session": admin_tok},
        follow_redirects=False,
    )
    assert post_res.status_code == 303
    loc = post_res.headers["location"]
    assert "/admin/customers/" in loc

    # Detail page
    detail_res = app_client.get(loc, cookies={"admin_session": admin_tok})
    assert detail_res.status_code == 200
    assert "newbie.trader@example.invalid" in detail_res.text
    assert "Newbie Trader" in detail_res.text
    assert "Liberado (Real + Demo)" in detail_res.text


def test_admin_renew_license_logic(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    test_settings: Settings,
) -> None:
    admin_tok = sign_session(test_settings.admin_email, test_settings.admin_session_secret)
    csrf = sign_csrf(test_settings.admin_session_secret)

    now = datetime.now(UTC)
    cust_id = uuid4()
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": "renew.trader@example.invalid",
        "name": "Renew Trader",
        "country": "BR",
        "notes": None,
        "created_at": now,
        "updated_at": now,
    }

    # Case 1: License already expired 5 days ago
    past_exp = now - timedelta(days=5)
    lic1 = mock_db_ctx.add_active_license(cust_id, expires_at=past_exp)

    renew_res1 = app_client.post(
        f"/admin/customers/{cust_id}/renew",
        data={"csrf_token": csrf},
        cookies={"admin_session": admin_tok},
        follow_redirects=False,
    )
    assert renew_res1.status_code == 303
    # New expiry should be approximately now + 30 days (NOT past_exp + 30 days)
    delta1 = (lic1["expires_at"] - now).total_seconds()
    assert 29 * 86400 <= delta1 <= 31 * 86400

    # Case 2: License active, expiring in 10 days
    future_exp = now + timedelta(days=10)
    lic1["expires_at"] = future_exp
    renew_res2 = app_client.post(
        f"/admin/customers/{cust_id}/renew",
        data={"csrf_token": csrf},
        cookies={"admin_session": admin_tok},
        follow_redirects=False,
    )
    assert renew_res2.status_code == 303
    delta2 = (lic1["expires_at"] - now).total_seconds()
    assert 39 * 86400 <= delta2 <= 41 * 86400


def test_admin_suspend_and_reactivate(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    test_settings: Settings,
) -> None:
    admin_tok = sign_session(test_settings.admin_email, test_settings.admin_session_secret)
    csrf = sign_csrf(test_settings.admin_session_secret)

    cust_id = uuid4()
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": "suspend.trader@example.invalid",
        "name": "Suspend Trader",
        "country": "BR",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    mock_db_ctx.add_active_license(cust_id)
    tokens = issue_family(FakeConnection(mock_db_ctx), cust_id)

    # Customer refresh works initially
    ref1 = app_client.post("/api/v1/auth/refresh", json={"refresh_token": tokens.refresh_token})
    assert ref1.status_code == 200
    active_refresh = ref1.json()["refresh_token"]

    # Admin suspends customer
    suspend_res = app_client.post(
        f"/admin/customers/{cust_id}/suspend",
        data={"csrf_token": csrf},
        cookies={"admin_session": admin_tok},
        follow_redirects=False,
    )
    assert suspend_res.status_code == 303

    # Client refresh fails with 403 AUTH_LICENSE_EXPIRED or 401 token revoked
    ref2 = app_client.post("/api/v1/auth/refresh", json={"refresh_token": active_refresh})
    assert ref2.status_code in {401, 403}

    # Admin reactivates
    react_res = app_client.post(
        f"/admin/customers/{cust_id}/reactivate",
        data={"csrf_token": csrf},
        cookies={"admin_session": admin_tok},
        follow_redirects=False,
    )
    assert react_res.status_code == 303
    # License is active again
    assert mock_db_ctx.licenses[0]["status"] == "active"


def test_admin_release_device_allows_new_registration(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    test_settings: Settings,
) -> None:
    admin_tok = sign_session(test_settings.admin_email, test_settings.admin_session_secret)
    csrf = sign_csrf(test_settings.admin_session_secret)

    cust_id = uuid4()
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": "device.trader@example.invalid",
        "name": "Device Trader",
        "country": "BR",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    mock_db_ctx.add_active_license(cust_id)
    tokens = issue_family(FakeConnection(mock_db_ctx), cust_id)
    headers = {"Authorization": f"Bearer {tokens.access_token}"}

    key1 = Ed25519PrivateKey.generate()
    dev1_id = "device-pc-1"
    reg1 = app_client.post(
        "/api/v1/device/register",
        json={"device_id": dev1_id, "public_key_b64": _pubkey_b64(key1)},
        headers=headers,
    )
    assert reg1.status_code == 204

    # Second device rejected due to max_devices = 1
    key2 = Ed25519PrivateKey.generate()
    dev2_id = "device-pc-2"
    reg2 = app_client.post(
        "/api/v1/device/register",
        json={"device_id": dev2_id, "public_key_b64": _pubkey_b64(key2)},
        headers=headers,
    )
    assert reg2.status_code == 403
    assert reg2.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_LIMIT.value

    # Admin releases device 1
    rel_res = app_client.post(
        f"/admin/customers/{cust_id}/devices/{dev1_id}/release",
        data={"csrf_token": csrf},
        cookies={"admin_session": admin_tok},
        follow_redirects=False,
    )
    assert rel_res.status_code == 303

    # Now device 2 registration succeeds
    reg2_after = app_client.post(
        "/api/v1/device/register",
        json={"device_id": dev2_id, "public_key_b64": _pubkey_b64(key2)},
        headers=headers,
    )
    assert reg2_after.status_code == 204


def test_admin_toggle_real_mode(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    test_settings: Settings,
) -> None:
    admin_tok = sign_session(test_settings.admin_email, test_settings.admin_session_secret)
    csrf = sign_csrf(test_settings.admin_session_secret)

    cust_id = uuid4()
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": "realmode.trader@example.invalid",
        "name": "Real Mode Trader",
        "country": "BR",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    lic = mock_db_ctx.add_active_license(cust_id, real_mode_allowed=False)
    assert lic["real_mode_allowed"] is False

    # Toggle to True
    res1 = app_client.post(
        f"/admin/customers/{cust_id}/toggle-real-mode",
        data={"csrf_token": csrf},
        cookies={"admin_session": admin_tok},
        follow_redirects=False,
    )
    assert res1.status_code == 303
    assert lic["real_mode_allowed"] is True

    # Toggle back to False
    res2 = app_client.post(
        f"/admin/customers/{cust_id}/toggle-real-mode",
        data={"csrf_token": csrf},
        cookies={"admin_session": admin_tok},
        follow_redirects=False,
    )
    assert res2.status_code == 303
    assert lic["real_mode_allowed"] is False


def test_admin_audit_page(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    test_settings: Settings,
) -> None:
    admin_tok = sign_session(test_settings.admin_email, test_settings.admin_session_secret)

    mock_db_ctx.audit_logs.append(
        {
            "customer_id": None,
            "actor": test_settings.admin_email,
            "action": "admin_test_event",
            "details": {"test": "data"},
            "created_at": datetime.now(UTC),
        }
    )

    res = app_client.get("/admin/audit", cookies={"admin_session": admin_tok})
    assert res.status_code == 200
    assert "admin_test_event" in res.text
    assert "Trilha de Auditoria Global" in res.text


def test_admin_csrf_rejection(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    test_settings: Settings,
) -> None:
    admin_tok = sign_session(test_settings.admin_email, test_settings.admin_session_secret)

    # Missing or invalid CSRF token
    res = app_client.post(
        "/admin/customers/new",
        data={
            "email": "badcsrf@example.invalid",
            "csrf_token": "completely-invalid-csrf-token",
        },
        cookies={"admin_session": admin_tok},
    )
    assert res.status_code == 400
