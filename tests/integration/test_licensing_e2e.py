"""Teste de integração ponta a ponta (E2E) de licenciamento contra servidor de staging.

Executa o fluxo completo de autenticação e licenciamento:
1. Descoberta de chaves públicas de lease via /.well-known/lease-keys
2. Início de login com e-mail e PKCE
3. Captura do código OTP via hook de teste
4. Submissão do OTP e recebimento de access/refresh tokens
5. Registro de identidade de dispositivo (DeviceIdentity)
6. Desafio criptográfico de posse de dispositivo (challenge/response com assinatura Ed25519)
7. Emissão de lease assinado
8. Validação e avaliação de autorização com LeaseVerifier para DERIV e IQ_OPTION
9. Validação de teto de dispositivos (segundo device_id -> AUTH_DEVICE_LIMIT)
10. Suspensão de licença e expiração via renovação
"""

from __future__ import annotations

import base64
import json
import os
import threading
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.auth_agent.fake_service import FakeIdentityServiceError, FakeIdentityServiceErrorCode
from apps.auth_agent.http_service import HttpIdentityService
from packages.identity import OtpCode, PkceMaterial
from packages.identity.models import DeviceIdentity
from packages.licensing import (
    AuthorizationReason,
    LeaseClaims,
    LeaseSigner,
    LeaseVerifier,
    SignedLease,
)

PRO_STRATEGY_PACKS = ("deriv-digits", "iqoption-rsi")


def _generate_test_device() -> tuple[DeviceIdentity, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    pub_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    device_id = f"dev-{uuid4()}"
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode("ascii")
    return DeviceIdentity(device_id=device_id, public_key_b64=pub_b64), key


class MockStagingServerHandler(BaseHTTPRequestHandler):
    """Implementa o contrato do servidor de licenças para staging e testes E2E."""

    signing_key = Ed25519PrivateKey.generate()
    key_id = "staging-key-test"
    last_otp: dict[str, str] = {}
    registered_devices: dict[str, set[str]] = {}  # user_id -> set of device_ids
    device_public_keys: dict[str, str] = {}
    active_challenges: dict[str, dict[str, Any]] = {}
    suspended_users: set[str] = set()

    @classmethod
    def get_pubkey_b64(cls) -> str:
        raw = cls.signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Silencia logs HTTP durante testes

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/.well-known/lease-keys":
            self._send_json(
                200,
                {
                    "keys": {
                        self.key_id: self.get_pubkey_b64(),
                    }
                },
            )
            return

        if parsed.path == "/__test__/last-otp":
            qs = parse_qs(parsed.query)
            email = qs.get("email", [""])[0]
            otp = self.last_otp.get(email, "")
            self._send_json(200, {"otp": otp})
            return

        if parsed.path.startswith("/api/v1/lease/revoked/"):
            self._send_json(200, {"revoked": False})
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len)) if content_len > 0 else {}

        if parsed.path == "/api/v1/auth/start":
            email = str(body.get("email", ""))
            generated_code = "".join(("6", "5", "4", "3", "2", "1"))
            self.last_otp[email] = generated_code
            challenge_id = f"chal-{uuid4()}"
            self.active_challenges[challenge_id] = {
                "email": email,
                "pkce_challenge": body.get("pkce_challenge", ""),
            }
            expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
            self._send_json(
                200,
                {
                    "challenge_id": challenge_id,
                    "expires_at": expires_at,
                    "user_id_preview": email,
                },
            )
            return

        if parsed.path in ("/api/v1/auth/otp", "/api/v1/auth/verify"):
            challenge_id = str(body.get("challenge_id", ""))

            otp_code = str(body.get("otp_code", ""))
            chal = self.active_challenges.get(challenge_id)
            if not chal:
                self._send_json(400, {"error": "AUTH_CHALLENGE_EXPIRED"})
                return
            expected_otp = self.last_otp.get(chal["email"], "")
            if otp_code != expected_otp:
                self._send_json(401, {"error": "AUTH_OTP_INVALID"})
                return
            token = f"token-{uuid4()}"
            self.active_challenges[token] = chal
            self._send_json(
                200,
                {
                    "access_token": token,
                    "refresh_token": f"refresh-{token}",
                    "token_type": "Bearer",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                    "user_id": chal["email"],
                },
            )
            return

        if parsed.path == "/api/v1/device/register":
            token = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
            chal = self.active_challenges.get(token)
            if not chal:
                self._send_json(401, {"error": "AUTH_UNAUTHORIZED"})
                return
            user_id = chal["email"]
            device_id = str(body.get("device_id", ""))
            public_key_b64 = str(body.get("public_key_b64", ""))

            current_devices = self.registered_devices.setdefault(user_id, set())
            if device_id not in current_devices and len(current_devices) >= 1:
                self._send_json(409, {"error": "AUTH_DEVICE_LIMIT"})
                return
            current_devices.add(device_id)
            self.device_public_keys[device_id] = public_key_b64
            self._send_json(200, {"status": "registered"})
            return

        if parsed.path == "/api/v1/device/challenge":
            device_id = str(body.get("device_id", ""))
            chal_id = f"devchal-{uuid4()}"
            nonce = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
            self.active_challenges[chal_id] = {"device_id": device_id, "nonce": nonce}
            self._send_json(
                200,
                {
                    "challenge_id": chal_id,
                    "nonce": nonce,
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                },
            )
            return

        if parsed.path == "/api/v1/lease/issue":
            token = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
            chal = self.active_challenges.get(token)
            user_id = chal["email"] if chal else "test@domain.com"
            if user_id in self.suspended_users:
                self._send_json(403, {"error": "AUTH_LICENSE_EXPIRED"})
                return

            device_id = str(body.get("device_id", ""))
            now = datetime.now(UTC)
            signer = LeaseSigner(self.key_id, self.signing_key)
            claims = LeaseClaims(
                format_version=1,
                lease_id=f"lease-{uuid4()}",
                user_id=user_id,
                device_id=device_id,
                issued_at=now,
                expires_at=now + timedelta(hours=24),
                plan="PRO",
                broker_access=("DERIV", "IQ_OPTION", "IQOPTION"),
                strategy_packs=PRO_STRATEGY_PACKS,
                real_mode_allowed=True,
                client_version_min="0.0.1",
                client_version_max="9.9.9",
                nonce=f"nonce-{uuid4()}",
            )

            signed = signer.sign(claims)
            self._send_json(
                200,
                {
                    "key_id": signed.key_id,
                    "payload_b64": signed.payload_b64,
                    "signature_b64": signed.signature_b64,
                },
            )
            return

        if parsed.path == "/api/v1/auth/refresh":
            token = body.get("refresh_token", "").replace("refresh-", "")
            chal = self.active_challenges.get(token)
            user_id = chal["email"] if chal else "test@domain.com"
            if user_id in self.suspended_users:
                self._send_json(403, {"error": "AUTH_LICENSE_EXPIRED"})
                return
            new_token = f"token-{uuid4()}"
            self.active_challenges[new_token] = chal if chal is not None else {}
            self._send_json(
                200,
                {
                    "access_token": new_token,
                    "refresh_token": f"refresh-{new_token}",
                    "token_type": "Bearer",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                    "user_id": user_id,
                },
            )
            return

        self._send_json(404, {"error": "not_found"})


@pytest.fixture(scope="module")
def staging_server() -> Generator[tuple[str, type[MockStagingServerHandler]]]:
    """Inicia um servidor local mock de staging caso TL_E2E_BASE_URL não esteja definido."""
    env_url = os.environ.get("TL_E2E_BASE_URL", "").strip()
    if env_url:
        yield env_url, MockStagingServerHandler
        return

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockStagingServerHandler)
    addr = server.server_address
    host = str(addr[0])
    port = int(addr[1])
    base_url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base_url, MockStagingServerHandler
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.integration
def test_licensing_e2e_full_flow(
    staging_server: tuple[str, type[MockStagingServerHandler]],
) -> None:
    """Executa o fluxo ponta a ponta de autenticação e licenciamento."""
    base_url, handler_cls = staging_server
    service = HttpIdentityService(base_url=base_url, timeout=5.0)

    # 1. Chaves públicas de lease
    pubkeys = service.lease_verification_keys
    assert len(pubkeys) >= 1

    # 2. Start Login
    test_email = f"trader-{uuid4().hex[:6]}@example.com"
    pkce = PkceMaterial.create()
    start_resp = service.start_login(test_email, pkce.challenge)
    assert "challenge_id" in start_resp
    challenge_id = str(start_resp["challenge_id"])

    # 3. Ler OTP (via hook de teste GET /__test__/last-otp?email= ou handler)
    otp_hook = os.environ.get("TL_E2E_OTP_HOOK")
    if otp_hook:
        import urllib.request

        url = f"{otp_hook}?email={test_email}"
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            otp_code = data.get("otp", "")
    else:
        otp_code = handler_cls.last_otp.get(test_email, "654321")

    assert len(otp_code) == 6

    # 4. Complete Login
    login_resp = service.complete_login(
        challenge_id,
        OtpCode(otp_code),
        pkce.verifier.reveal_text(),
    )
    assert "access_token" in login_resp
    access_token = str(login_resp["access_token"])

    # 5. Gerar e Registrar Device Identity
    dev1, dev1_key = _generate_test_device()
    service.register_device(access_token, dev1.device_id, dev1.public_key_b64)

    # 6. Desafio do Dispositivo e Assinatura
    dev_chal = service.create_device_challenge(access_token, dev1.device_id)
    dev_chal_id = str(dev_chal["challenge_id"])
    nonce = str(dev_chal["nonce"])
    signature = dev1_key.sign(nonce.encode("utf-8"))
    sig_b64 = base64.urlsafe_b64encode(signature).decode("ascii")

    # 7. Obter Lease
    raw_lease = service.issue_lease(access_token, dev1.device_id, dev_chal_id, sig_b64)
    assert "payload_b64" in raw_lease
    assert "signature_b64" in raw_lease
    signed_lease = SignedLease(
        key_id=str(raw_lease["key_id"]),
        payload_b64=str(raw_lease["payload_b64"]),
        signature_b64=str(raw_lease["signature_b64"]),
    )

    # 8. Validar e Avaliar com LeaseVerifier
    verifier = LeaseVerifier(pubkeys)
    claims = verifier.verify(signed_lease)
    assert claims.user_id == test_email
    assert claims.device_id == dev1.device_id

    # Avalia para cada corretora e cada strategy pack
    for broker in ("DERIV", "IQ_OPTION"):
        for spk in PRO_STRATEGY_PACKS:
            decision = verifier.evaluate(
                signed_lease,
                now=datetime.now(UTC),
                expected_user_id=test_email,
                expected_device_id=dev1.device_id,
                client_version="1.0.0",
                broker=broker,
                strategy_pack=spk,
                real_mode=True,
            )
            assert decision.new_entries_allowed is True
            assert decision.reason == AuthorizationReason.AUTHORIZED

    # 9. Tentar registrar um segundo dispositivo (deve falhar com AUTH_DEVICE_LIMIT)
    dev2, _ = _generate_test_device()
    with pytest.raises(FakeIdentityServiceError) as exc_info:
        service.register_device(access_token, dev2.device_id, dev2.public_key_b64)
    assert exc_info.value.code == FakeIdentityServiceErrorCode.DEVICE_LIMIT

    # 10. Suspender licença e confirmar que refresh retorna AUTH_LICENSE_EXPIRED
    handler_cls.suspended_users.add(test_email)
    refresh_token = str(login_resp.get("refresh_token", f"refresh-{access_token}"))
    with pytest.raises(FakeIdentityServiceError) as exc_refresh:
        service.refresh_session(refresh_token)
    assert exc_refresh.value.code == FakeIdentityServiceErrorCode.LICENSE_EXPIRED
