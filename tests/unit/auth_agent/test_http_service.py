from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from apps.auth_agent.fake_service import (
    FakeIdentityServiceError,
    FakeIdentityServiceErrorCode,
)
from apps.auth_agent.http_service import HttpIdentityService
from apps.auth_agent.pinned_keys import PINNED_LEASE_KEYS
from packages.identity import OtpCode


class _MockServerHandler(BaseHTTPRequestHandler):
    # Class-level state shared across requests
    routes: dict[tuple[str, str], tuple[int, Any]] = {}
    delay_routes: set[tuple[str, str]] = set()
    last_headers: dict[str, str] = {}
    last_body: Any = None

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard logging to keep test output clean
        pass

    def _handle_request(self, method: str) -> None:
        key = (method, self.path)
        if key in self.delay_routes:
            time.sleep(0.5)

        _MockServerHandler.last_headers = {k: v for k, v in self.headers.items()}
        content_length = self.headers.get("Content-Length")
        if content_length:
            try:
                body_bytes = self.rfile.read(int(content_length))
                _MockServerHandler.last_body = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                _MockServerHandler.last_body = None
        else:
            _MockServerHandler.last_body = None

        if key in self.routes:
            status, payload = self.routes[key]
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
                body = json.dumps(payload).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_header("Content-Length", "0")
                self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({"error": {"code": "NOT_FOUND"}}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._handle_request("GET")

    def do_POST(self) -> None:
        self._handle_request("POST")


@pytest.fixture
def mock_server() -> Generator[tuple[str, type[_MockServerHandler]]]:
    server = HTTPServer(("127.0.0.1", 0), _MockServerHandler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    _MockServerHandler.routes = {}
    _MockServerHandler.delay_routes = set()
    _MockServerHandler.last_headers = {}
    _MockServerHandler.last_body = None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield base_url, _MockServerHandler

    server.shutdown()
    server.server_close()


def test_happy_path_full_flow(mock_server: tuple[str, type[_MockServerHandler]]) -> None:
    base_url, handler = mock_server
    service = HttpIdentityService(base_url, timeout=2.0)

    # 1. Start Login
    handler.routes[("POST", "/api/v1/auth/start")] = (
        200,
        {"challenge_id": "ch-100", "expires_at": "2026-09-14T00:00:00Z"},
    )
    start_resp = service.start_login("trader@example.com", "challenge-xyz")
    assert start_resp["challenge_id"] == "ch-100"
    assert handler.last_body == {"email": "trader@example.com", "pkce_challenge": "challenge-xyz"}

    # 2. Complete Login (Verify)
    handler.routes[("POST", "/api/v1/auth/verify")] = (
        200,
        {
            "access_token": "acc-token-123",
            "refresh_token": "ref-token-456",
            "expires_at": "2026-09-14T01:00:00Z",
        },
    )
    login_resp = service.complete_login("ch-100", OtpCode("123456"), "verifier-xyz")
    assert login_resp["access_token"] == "acc-token-123"
    assert login_resp["refresh_token"] == "ref-token-456"
    assert handler.last_body == {
        "challenge_id": "ch-100",
        "otp_code": "123456",
        "pkce_verifier": "verifier-xyz",
    }

    # 3. Refresh Session
    handler.routes[("POST", "/api/v1/auth/refresh")] = (
        200,
        {
            "access_token": "tok-789",
            "refresh_token": "ref-token-999",
            "expires_at": "2026-09-14T02:00:00Z",
        },
    )
    refresh_resp = service.refresh_session("ref-token-456")
    assert refresh_resp["access_token"] == "tok-789"
    assert handler.last_body == {"refresh_token": "ref-token-456"}

    # 4. Register Device (204 No Content)
    handler.routes[("POST", "/api/v1/device/register")] = (204, None)
    service.register_device("tok-789", "dev-001", "cHVibGljX2tleQ==")
    assert handler.last_headers.get("Authorization") == "Bearer " + "tok-789"
    assert handler.last_body == {"device_id": "dev-001", "public_key_b64": "cHVibGljX2tleQ=="}

    # 5. Create Device Challenge
    handler.routes[("POST", "/api/v1/device/challenge")] = (
        200,
        {
            "challenge_id": "dev-ch-555",
            "nonce_b64": "bm9uY2U=",
            "expires_at": "2026-09-14T00:05:00Z",
        },
    )
    challenge_resp = service.create_device_challenge("tok-789", "dev-001")
    assert challenge_resp["challenge_id"] == "dev-ch-555"
    assert handler.last_headers.get("Authorization") == "Bearer " + "tok-789"
    assert handler.last_body == {"device_id": "dev-001"}

    # 6. Issue Lease
    handler.routes[("POST", "/api/v1/lease/issue")] = (
        200,
        {
            "key_id": "tl-2026-09",
            "payload_b64": "cGF5bG9hZA==",
            "signature_b64": "c2lnbmF0dXJl",
        },
    )
    lease_resp = service.issue_lease("tok-789", "dev-001", "dev-ch-555", "c2lnbmF0dXJlX2I2NA==")
    assert lease_resp["key_id"] == "tl-2026-09"
    assert handler.last_headers.get("Authorization") == "Bearer " + "tok-789"

    # 7. Check Lease Revocation
    handler.routes[("GET", "/api/v1/lease/revoked/lease-123")] = (200, {"revoked": False})
    assert service.is_lease_revoked("lease-123") is False

    handler.routes[("GET", "/api/v1/lease/revoked/lease-revoked")] = (200, {"revoked": True})
    assert service.is_lease_revoked("lease-revoked") is True


@pytest.mark.parametrize(
    "error_payload,expected_code",
    [
        (
            {"error": {"code": "AUTH_CHALLENGE_INVALID"}},
            FakeIdentityServiceErrorCode.CHALLENGE_INVALID,
        ),
        (
            {"error": {"code": "AUTH_CHALLENGE_EXPIRED"}},
            FakeIdentityServiceErrorCode.CHALLENGE_EXPIRED,
        ),
        ({"error": {"code": "AUTH_OTP_INVALID"}}, FakeIdentityServiceErrorCode.OTP_INVALID),
        ({"error": {"code": "AUTH_PKCE_INVALID"}}, FakeIdentityServiceErrorCode.PKCE_INVALID),
        ({"error": {"code": "AUTH_TOKEN_INVALID"}}, FakeIdentityServiceErrorCode.TOKEN_INVALID),
        ({"error": {"code": "AUTH_REFRESH_REUSE"}}, FakeIdentityServiceErrorCode.TOKEN_REUSE),
        ({"error": {"code": "AUTH_DEVICE_INVALID"}}, FakeIdentityServiceErrorCode.DEVICE_INVALID),
        ({"error": {"code": "AUTH_DEVICE_REVOKED"}}, FakeIdentityServiceErrorCode.DEVICE_REVOKED),
        (
            {"error": {"code": "AUTH_DEVICE_PROOF_INVALID"}},
            FakeIdentityServiceErrorCode.DEVICE_PROOF_INVALID,
        ),
        ({"error": {"code": "AUTH_LICENSE_EXPIRED"}}, FakeIdentityServiceErrorCode.LICENSE_EXPIRED),
        ({"error": {"code": "AUTH_DEVICE_LIMIT"}}, FakeIdentityServiceErrorCode.DEVICE_LIMIT),
        # Without AUTH_ prefix
        ({"error": {"code": "LICENSE_EXPIRED"}}, FakeIdentityServiceErrorCode.LICENSE_EXPIRED),
        ({"error": {"code": "DEVICE_LIMIT"}}, FakeIdentityServiceErrorCode.DEVICE_LIMIT),
        # Unknown error code maps to UNAVAILABLE
        ({"error": {"code": "UNKNOWN_ERROR_XYZ"}}, FakeIdentityServiceErrorCode.UNAVAILABLE),
        # Flat format
        ({"error": "AUTH_OTP_INVALID"}, FakeIdentityServiceErrorCode.OTP_INVALID),
    ],
)
def test_error_code_mapping(
    mock_server: tuple[str, type[_MockServerHandler]],
    error_payload: dict[str, Any],
    expected_code: FakeIdentityServiceErrorCode,
) -> None:
    base_url, handler = mock_server
    service = HttpIdentityService(base_url, timeout=2.0)

    handler.routes[("POST", "/api/v1/auth/start")] = (400, error_payload)
    with pytest.raises(FakeIdentityServiceError) as exc_info:
        service.start_login("user@test.com", "ch")
    assert exc_info.value.code == expected_code


def test_server_error_500_maps_to_unavailable(
    mock_server: tuple[str, type[_MockServerHandler]],
) -> None:
    base_url, handler = mock_server
    service = HttpIdentityService(base_url, timeout=2.0)

    handler.routes[("POST", "/api/v1/auth/start")] = (500, {"message": "Internal error"})
    with pytest.raises(FakeIdentityServiceError) as exc_info:
        service.start_login("user@test.com", "ch")
    assert exc_info.value.code == FakeIdentityServiceErrorCode.UNAVAILABLE


def test_timeout_raises_unavailable(
    mock_server: tuple[str, type[_MockServerHandler]],
) -> None:
    base_url, handler = mock_server
    # Set short client timeout and add delay route on server
    service = HttpIdentityService(base_url, timeout=0.1)
    handler.delay_routes.add(("POST", "/api/v1/auth/start"))
    handler.routes[("POST", "/api/v1/auth/start")] = (200, {"challenge_id": "ch-1"})

    with pytest.raises(FakeIdentityServiceError) as exc_info:
        service.start_login("user@test.com", "ch")
    assert exc_info.value.code == FakeIdentityServiceErrorCode.UNAVAILABLE


def test_offline_server_raises_unavailable() -> None:
    # Port 1 is not open
    service = HttpIdentityService("http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(FakeIdentityServiceError) as exc_info:
        service.start_login("user@test.com", "ch")
    assert exc_info.value.code == FakeIdentityServiceErrorCode.UNAVAILABLE


def test_lease_verification_keys_includes_pinned_and_server(
    mock_server: tuple[str, type[_MockServerHandler]],
) -> None:
    base_url, handler = mock_server
    service = HttpIdentityService(base_url, timeout=2.0)

    # Mock server response with a dynamic key (32 bytes urlsafe b64)
    server_pubkey_raw = b"K" * 32
    server_pubkey_b64 = base64.urlsafe_b64encode(server_pubkey_raw).decode("ascii")
    handler.routes[("GET", "/.well-known/lease-keys")] = (
        200,
        {"dynamic-server-key": server_pubkey_b64},
    )

    keys = service.lease_verification_keys

    # Must contain the server key
    assert "dynamic-server-key" in keys
    assert keys["dynamic-server-key"] == server_pubkey_raw

    # Must ALWAYS contain the pinned key
    for pinned_id in PINNED_LEASE_KEYS:
        assert pinned_id in keys
        assert len(keys[pinned_id]) == 32


def test_lease_verification_keys_survives_server_failure() -> None:
    # Server is completely offline
    service = HttpIdentityService("http://127.0.0.1:1", timeout=0.2)
    keys = service.lease_verification_keys

    # Must not raise and must contain all pinned keys
    assert len(keys) >= len(PINNED_LEASE_KEYS)
    for pinned_id in PINNED_LEASE_KEYS:
        assert pinned_id in keys
        assert len(keys[pinned_id]) == 32
