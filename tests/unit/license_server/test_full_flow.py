"""End-to-end integration and flow unit tests for license server.

Covers start -> verify -> register device -> challenge -> sign nonce -> issue lease,
and verifies client-side acceptance using LeaseVerifier and evaluate().
"""

from __future__ import annotations

import base64
import logging
import re
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from apps.license_server.entitlements import PRO_BROKERS, PRO_STRATEGY_PACKS
from apps.license_server.errors import ErrorCode
from packages.identity import PkceMaterial
from packages.licensing import AuthorizationReason, SignedLease
from packages.licensing.lease import LeaseVerifier
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
    monkeypatch.setattr("apps.license_server.routes.device.with_conn", _fake_with_conn)
    monkeypatch.setattr("apps.license_server.routes.lease.with_conn", _fake_with_conn)
    monkeypatch.setattr("apps.license_server.dependencies.with_conn", _fake_with_conn)
    return fake_db


def _pubkey_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii")


def test_full_flow_happy_path(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "fullflow.trader@example.invalid"
    pkce = PkceMaterial.create()

    # 1. Start Login
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

    # Pre-create customer & active license with all pro strategy packs
    cust_id = UUID("44444444-4444-4444-4444-444444444444")
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": email,
        "name": "Full Flow Trader",
        "country": "ES",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    lic = mock_db_ctx.add_active_license(cust_id, plan="PRO", real_mode_allowed=False)
    lic["strategy_packs"] = PRO_STRATEGY_PACKS
    lic["broker_access"] = PRO_BROKERS

    # 2. Verify Login
    verify_res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    assert verify_res.status_code == 200
    token_data = verify_res.json()
    user_id = token_data["user_id"]
    access_token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    # 3. Generate client device identity
    device_priv = Ed25519PrivateKey.generate()
    device_pub_raw = device_priv.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    device_pub_b64 = base64.urlsafe_b64encode(device_pub_raw).decode("ascii")
    device_id = f"tl-dev-{secrets.token_hex(6)}"

    # 4. Register device
    reg_res = app_client.post(
        "/api/v1/device/register",
        json={"device_id": device_id, "public_key_b64": device_pub_b64},
        headers=headers,
    )
    assert reg_res.status_code == 204

    # Idempotent re-registration succeeds
    reg_repeat = app_client.post(
        "/api/v1/device/register",
        json={"device_id": device_id, "public_key_b64": device_pub_b64},
        headers=headers,
    )
    assert reg_repeat.status_code == 204

    # 5. Device Challenge
    chal_res = app_client.post(
        "/api/v1/device/challenge",
        json={"device_id": device_id},
        headers=headers,
    )
    assert chal_res.status_code == 200
    chal_data = chal_res.json()
    dev_challenge_id = chal_data["challenge_id"]
    nonce_b64 = chal_data["nonce_b64"]

    # 6. Sign raw nonce with device private key
    nonce_raw = base64.urlsafe_b64decode(nonce_b64)
    signature_raw = device_priv.sign(nonce_raw)
    signature_b64 = base64.urlsafe_b64encode(signature_raw).decode("ascii")

    # 7. Issue Lease
    issue_res = app_client.post(
        "/api/v1/lease/issue",
        json={
            "device_id": device_id,
            "challenge_id": dev_challenge_id,
            "signature_b64": signature_b64,
        },
        headers=headers,
    )
    assert issue_res.status_code == 200
    lease_data = issue_res.json()
    assert "key_id" in lease_data
    assert "payload_b64" in lease_data
    assert "signature_b64" in lease_data

    # 8. Check Lease Revocation Status
    signed = SignedLease(
        key_id=lease_data["key_id"],
        payload_b64=lease_data["payload_b64"],
        signature_b64=lease_data["signature_b64"],
    )

    # 9. Verify lease with client's LeaseVerifier
    keys_res = app_client.get("/.well-known/lease-keys")
    assert keys_res.status_code == 200
    server_keys_map = keys_res.json()["keys"]
    assert signed.key_id in server_keys_map
    server_pub_raw = base64.urlsafe_b64decode(server_keys_map[signed.key_id])

    verifier = LeaseVerifier({signed.key_id: server_pub_raw})
    claims = verifier.verify(signed)
    assert claims.user_id == user_id
    assert claims.device_id == device_id
    assert claims.plan == "PHASE0_PRACTICE"

    # 10. Evaluate authorization
    decision = verifier.evaluate(
        signed,
        now=datetime.now(UTC),
        expected_user_id=user_id,
        expected_device_id=device_id,
        client_version="0.0.1",
        broker="DERIV",
        strategy_pack=PRO_STRATEGY_PACKS[0],
    )
    assert decision.reason == AuthorizationReason.AUTHORIZED
    assert decision.new_entries_allowed is True
    assert decision.lease_id == claims.lease_id

    # 11. Check revocation endpoint returns false for active lease
    rev_res = app_client.get(f"/api/v1/lease/revoked/{claims.lease_id}")
    assert rev_res.status_code == 200
    assert rev_res.json() == {"revoked": False}


def test_second_device_blocked_by_device_limit(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "limit.trader@example.invalid"
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

    cust_id = UUID("55555555-5555-5555-5555-555555555555")
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": email,
        "name": "Limit Trader",
        "country": "ES",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    mock_db_ctx.add_active_license(cust_id, plan="PRO")  # max_devices = 1

    verify_res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    access_token = verify_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    # Register first device (succeeds)
    dev1_key = Ed25519PrivateKey.generate()
    dev1_pub = _pubkey_b64(dev1_key)
    res1 = app_client.post(
        "/api/v1/device/register",
        json={"device_id": "device-1", "public_key_b64": dev1_pub},
        headers=headers,
    )
    assert res1.status_code == 204

    # Register second device (fails: max_devices=1 exceeded)
    dev2_key = Ed25519PrivateKey.generate()
    dev2_pub = _pubkey_b64(dev2_key)
    res2 = app_client.post(
        "/api/v1/device/register",
        json={"device_id": "device-2", "public_key_b64": dev2_pub},
        headers=headers,
    )

    assert res2.status_code == 403
    err = res2.json()["error"]
    assert err["code"] == ErrorCode.AUTH_DEVICE_LIMIT.value
    assert "Tu licencia ya está activa en otro equipo." in err["message"]


def test_revoked_device_blocked(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "revoked.trader@example.invalid"
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

    cust_id = UUID("66666666-6666-6666-6666-666666666666")
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": email,
        "name": "Revoked Trader",
        "country": "ES",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    mock_db_ctx.add_active_license(cust_id)

    verify_res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    access_token = verify_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    dev_key = Ed25519PrivateKey.generate()
    dev_pub = _pubkey_b64(dev_key)
    device_id = "device-to-revoke"

    # Register device
    res_reg = app_client.post(
        "/api/v1/device/register",
        json={"device_id": device_id, "public_key_b64": dev_pub},
        headers=headers,
    )
    assert res_reg.status_code == 204

    # System marks device as revoked
    mock_db_ctx.devices[device_id]["revoked"] = True

    # Re-registration attempt returns 403 AUTH_DEVICE_REVOKED
    res_re_reg = app_client.post(
        "/api/v1/device/register",
        json={"device_id": device_id, "public_key_b64": dev_pub},
        headers=headers,
    )
    assert res_re_reg.status_code == 403
    assert res_re_reg.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_REVOKED.value

    # Challenge attempt returns 403 AUTH_DEVICE_REVOKED
    chal_res = app_client.post(
        "/api/v1/device/challenge",
        json={"device_id": device_id},
        headers=headers,
    )
    assert chal_res.status_code == 403
    assert chal_res.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_REVOKED.value


def test_invalid_signature_fails_proof_of_possession(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "badsig.trader@example.invalid"
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

    cust_id = UUID("77777777-7777-7777-7777-777777777777")
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": email,
        "name": "Bad Sig Trader",
        "country": "ES",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    mock_db_ctx.add_active_license(cust_id)

    verify_res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    access_token = verify_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    dev_key = Ed25519PrivateKey.generate()
    dev_pub = _pubkey_b64(dev_key)
    device_id = "device-badsig"

    app_client.post(
        "/api/v1/device/register",
        json={"device_id": device_id, "public_key_b64": dev_pub},
        headers=headers,
    )

    chal_res = app_client.post(
        "/api/v1/device/challenge",
        json={"device_id": device_id},
        headers=headers,
    )
    dev_challenge_id = chal_res.json()["challenge_id"]

    # Sign WRONG data
    bogus_signature = dev_key.sign(b"completely-wrong-data")
    bogus_sig_b64 = base64.urlsafe_b64encode(bogus_signature).decode("ascii")

    issue_res = app_client.post(
        "/api/v1/lease/issue",
        json={
            "device_id": device_id,
            "challenge_id": dev_challenge_id,
            "signature_b64": bogus_sig_b64,
        },
        headers=headers,
    )
    assert issue_res.status_code == 400
    assert issue_res.json()["error"]["code"] == ErrorCode.AUTH_DEVICE_PROOF_INVALID.value


def test_lease_expires_at_matches_short_license(
    app_client: TestClient,
    mock_db_ctx: FakeDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = "shortlic.trader@example.invalid"
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

    cust_id = UUID("88888888-8888-8888-8888-888888888888")
    mock_db_ctx.customers[str(cust_id)] = {
        "id": str(cust_id),
        "email": email,
        "name": "Short Lic Trader",
        "country": "ES",
        "notes": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }

    # License expires in exactly 2 hours
    now = datetime.now(UTC)
    short_expiry = now + timedelta(hours=2)
    mock_db_ctx.add_active_license(cust_id, expires_at=short_expiry)

    verify_res = app_client.post(
        "/api/v1/auth/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": otp_code,
            "pkce_verifier": pkce.verifier.reveal_text(),
        },
    )
    access_token = verify_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    dev_key = Ed25519PrivateKey.generate()
    dev_pub = _pubkey_b64(dev_key)
    device_id = "device-shortlic"

    app_client.post(
        "/api/v1/device/register",
        json={"device_id": device_id, "public_key_b64": dev_pub},
        headers=headers,
    )

    chal_res = app_client.post(
        "/api/v1/device/challenge",
        json={"device_id": device_id},
        headers=headers,
    )
    dev_challenge_id = chal_res.json()["challenge_id"]
    nonce_b64 = chal_res.json()["nonce_b64"]

    signature = dev_key.sign(base64.urlsafe_b64decode(nonce_b64))
    signature_b64 = base64.urlsafe_b64encode(signature).decode("ascii")

    issue_res = app_client.post(
        "/api/v1/lease/issue",
        json={
            "device_id": device_id,
            "challenge_id": dev_challenge_id,
            "signature_b64": signature_b64,
        },
        headers=headers,
    )
    assert issue_res.status_code == 200

    signed = SignedLease(
        key_id=issue_res.json()["key_id"],
        payload_b64=issue_res.json()["payload_b64"],
        signature_b64=issue_res.json()["signature_b64"],
    )

    keys_res = app_client.get("/.well-known/lease-keys")
    server_pub_raw = base64.urlsafe_b64decode(keys_res.json()["keys"][signed.key_id])
    verifier = LeaseVerifier({signed.key_id: server_pub_raw})
    claims = verifier.verify(signed)

    # Claims expires_at must be capped by license
    # (at most 2 hours + a few seconds from now, NOT 7 days)
    delta_seconds = (claims.expires_at - now).total_seconds()
    assert 7100 <= delta_seconds <= 7300  # approximately 2 hours (7200 seconds)
