from __future__ import annotations

import secrets
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from apps.auth_agent.server import AuthAgentServer
from apps.core.auth_client import AuthAgentIpcClient, AuthIpcAuthenticationError
from packages.identity import OtpCode
from packages.protocol import (
    PROTOCOL_VERSION,
    AuthHandshakeRequest,
    AuthLoginStatus,
    AuthMode,
    EndpointRole,
    Envelope,
    MessageType,
)
from packages.security import SecretValue


def _runtime_otp() -> OtpCode:
    return OtpCode(f"{secrets.randbelow(1_000_000):06d}")


def _running_server(
    tmp_path: Path,
) -> tuple[AuthAgentServer, threading.Thread, SecretValue, OtpCode]:
    token = SecretValue.from_text(secrets.token_hex(32))
    otp = _runtime_otp()
    server = AuthAgentServer(
        token,
        tmp_path / "auth-contract",
        force_simulation=True,
        test_otp=otp,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, token, otp


def test_auth_ipc_rejects_wrong_session_token_then_accepts_mutual_handshake(
    tmp_path: Path,
) -> None:
    server, thread, token, _ = _running_server(tmp_path)
    wrong_token = SecretValue.from_text(secrets.token_hex(32))

    with pytest.raises(
        AuthIpcAuthenticationError,
        match="AUTH_IPC_AUTHENTICATION_FAILED",
    ):
        AuthAgentIpcClient.connect("127.0.0.1", server.port, wrong_token)

    client = AuthAgentIpcClient.connect("127.0.0.1", server.port, token)
    assert client.is_ready
    assert client.shutdown()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_auth_ipc_login_and_authorization_return_only_reduced_payloads(tmp_path: Path) -> None:
    server, thread, token, otp = _running_server(tmp_path)
    client = AuthAgentIpcClient.connect("127.0.0.1", server.port, token)
    try:
        challenge = client.start_login("ipc-user@example.invalid")
        assert challenge.status is AuthLoginStatus.CHALLENGE_CREATED

        wrong = _runtime_otp()
        while wrong.value == otp.value:
            wrong = _runtime_otp()
        invalid = client.submit_otp(challenge.challenge_id, wrong)
        assert invalid.status is AuthLoginStatus.INVALID_CODE
        assert invalid.user_id_preview is None

        authorized = client.submit_otp(challenge.challenge_id, otp)
        assert authorized.status is AuthLoginStatus.AUTHORIZED
        assert authorized.user_id_preview is not None
        assert len(authorized.user_id_preview) == 12

        decision = client.check_authorization("DERIV", "strategy-test")
        assert decision.allowed is True
        assert decision.reason_code == "AUTHORIZED"
        assert set(decision.to_payload()) == {
            "allowed",
            "lease_expires_at_utc",
            "reason_code",
        }

        renewed = client.renew()
        assert renewed.allowed is True
        assert renewed.reason_code == "AUTHORIZED"

        real_decision = client.check_authorization(
            "DERIV",
            "strategy-test",
            mode=AuthMode.REAL,
        )
        assert real_decision.allowed is False
        assert real_decision.reason_code == "HG_REAL_MODE_DISABLED"

        status = client.status()
        assert status.lease_active is True
        assert status.device_id is not None
    finally:
        client.shutdown()
        thread.join(timeout=2.0)


def test_auth_ipc_models_and_envelopes_redact_sensitive_payloads(tmp_path: Path) -> None:
    del tmp_path
    token = SecretValue.from_text(secrets.token_hex(32))
    request = AuthHandshakeRequest(token, "1.0.0", secrets.token_hex(32))
    envelope = Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        causation_id=None,
        source=EndpointRole.CORE,
        target=EndpointRole.AUTH_AGENT,
        message_type=MessageType.AUTH_HANDSHAKE_REQUEST,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload=request.to_payload(),
    )

    assert token.reveal_text() not in repr(request)
    assert token.reveal_text() not in repr(envelope)
    assert "payload=<redacted>" in repr(envelope)


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows fallback is tested elsewhere")
def test_auth_server_simulation_contract_is_platform_independent(tmp_path: Path) -> None:
    server, thread, token, _ = _running_server(tmp_path)
    client = AuthAgentIpcClient.connect("127.0.0.1", server.port, token)
    assert client.status().auth_state == "REAUTH_REQUIRED"
    client.shutdown()
    thread.join(timeout=2.0)
