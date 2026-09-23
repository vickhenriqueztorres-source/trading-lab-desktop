from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.auth_agent import AuthAgent, AuthAgentState, FakeIdentityService
from apps.auth_agent.pinned_keys import PINNED_LEASE_KEYS
from packages.licensing import (
    AuthorizationReason,
    LeaseClaims,
    LeaseSigner,
    LeaseVerifier,
    decode_product_key,
    encode_product_key,
)
from packages.security import SimulatedUserScopedVault
from scripts.gerar_licenca import generate_license


@pytest.fixture
def keypair() -> tuple[Ed25519PrivateKey, str, bytes]:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "test-key-1"
    return private_key, key_id, public_bytes


def test_encode_and_decode_product_key_roundtrip(
    keypair: tuple[Ed25519PrivateKey, str, bytes],
) -> None:
    private_key, key_id, _ = keypair
    signer = LeaseSigner(key_id, private_key)

    now = datetime.now(UTC)
    claims = LeaseClaims(
        format_version=1,
        lease_id=str(uuid4()),
        user_id="Cliente VIP",
        device_id="*",
        issued_at=now,
        expires_at=now + timedelta(days=365),
        plan="PRO",
        broker_access=("DERIV", "IQ_OPTION"),
        strategy_packs=("core", "iqoption-radar"),
        real_mode_allowed=True,
        client_version_min="0.0.1",
        client_version_max="999.99.99",
        nonce="test-nonce-1234",
    )
    signed = signer.sign(claims)

    product_key = encode_product_key(signed, prefix="TLKEY-PRO-")
    assert product_key.startswith("TLKEY-PRO-")

    decoded = decode_product_key(product_key)
    assert decoded.key_id == key_id
    assert decoded.payload_b64 == signed.payload_b64
    assert decoded.signature_b64 == signed.signature_b64


def test_decode_product_key_from_json(keypair: tuple[Ed25519PrivateKey, str, bytes]) -> None:
    private_key, key_id, _ = keypair
    signer = LeaseSigner(key_id, private_key)

    now = datetime.now(UTC)
    claims = LeaseClaims(
        format_version=1,
        lease_id=str(uuid4()),
        user_id="Cliente JSON",
        device_id="*",
        issued_at=now,
        expires_at=now + timedelta(days=30),
        plan="PRO",
        broker_access=("DERIV", "IQ_OPTION"),
        strategy_packs=("core",),
        real_mode_allowed=True,
        client_version_min="0.0.1",
        client_version_max="99.0.0",
        nonce="nonce-json",
    )
    signed = signer.sign(claims)

    json_text = signed.to_bytes().decode("utf-8")
    decoded = decode_product_key(json_text)
    assert decoded.key_id == key_id
    assert decoded.payload_b64 == signed.payload_b64


def test_decode_product_key_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="empty"):
        decode_product_key("   ")

    with pytest.raises(ValueError):
        decode_product_key("TLKEY-PRO-invalid-base64-content!@#$%")

    with pytest.raises(ValueError):
        decode_product_key("{not-valid-json}")


def test_lease_verifier_allows_long_term_and_wildcard_device(
    keypair: tuple[Ed25519PrivateKey, str, bytes],
) -> None:
    private_key, key_id, pub_bytes = keypair
    signer = LeaseSigner(key_id, private_key)
    verifier = LeaseVerifier({key_id: pub_bytes}, allow_long_term=True)

    now = datetime.now(UTC)
    claims = LeaseClaims(
        format_version=1,
        lease_id=str(uuid4()),
        user_id="Cliente Long Term",
        device_id="*",
        issued_at=now,
        expires_at=now + timedelta(days=3650),  # 10 anos
        plan="PRO",
        broker_access=("DERIV", "IQ_OPTION"),
        strategy_packs=("core", "iqoption-radar"),
        real_mode_allowed=True,
        client_version_min="0.0.1",
        client_version_max="999.99.99",
        nonce="nonce-long",
    )
    signed = signer.sign(claims)

    verified = verifier.verify(signed)
    assert verified.user_id == "Cliente Long Term"
    assert verified.device_id == "*"

    # Avaliação com qualquer device_id deve funcionar por causa do wildcard
    decision = verifier.evaluate(
        signed,
        now=now,
        expected_user_id="Cliente Long Term",
        expected_device_id="arbitrary-hardware-id-xyz",
        client_version="1.9.11",
        broker="DERIV",
        strategy_pack="core",
        real_mode=True,
    )
    assert decision.new_entries_allowed is True
    assert decision.reason == AuthorizationReason.AUTHORIZED


def test_auth_agent_activate_product_key_and_restore_cycle(
    keypair: tuple[Ed25519PrivateKey, str, bytes],
) -> None:
    private_key, key_id, pub_bytes = keypair
    signer = LeaseSigner(key_id, private_key)
    verifier = LeaseVerifier({key_id: pub_bytes})

    now = datetime.now(UTC)
    claims = LeaseClaims(
        format_version=1,
        lease_id=str(uuid4()),
        user_id="Trader Offline",
        device_id="*",
        issued_at=now,
        expires_at=now + timedelta(days=90),
        plan="PRO",
        broker_access=("DERIV", "IQ_OPTION"),
        strategy_packs=("core", "strategy-test"),
        real_mode_allowed=True,
        client_version_min="0.0.1",
        client_version_max="999.99.99",
        nonce="nonce-agent",
    )
    signed = signer.sign(claims)
    product_key = encode_product_key(signed)

    vault = SimulatedUserScopedVault("windows-user")
    service = FakeIdentityService(now=lambda: now)
    agent = AuthAgent(
        service,
        vault,
        verifier,
        client_version="1.0.0",
        now=lambda: now,
    )

    decision = agent.activate_product_key(product_key)
    assert decision.new_entries_allowed is True
    assert agent.state == AuthAgentState.OFFLINE_AUTHORIZED

    # Simula reinicialização do aplicativo: novo agente com mesmo vault
    restarted_agent = AuthAgent(
        service,
        vault,
        verifier,
        client_version="1.0.0",
        now=lambda: now,
    )
    startup_decision = restarted_agent.restore()
    assert startup_decision.new_entries_allowed is True
    assert restarted_agent.state == AuthAgentState.OFFLINE_AUTHORIZED


def test_generate_license_script_produces_valid_pinned_key() -> None:
    key_id = "tl-master-offline"
    assert key_id in PINNED_LEASE_KEYS

    product_key, claims = generate_license(
        client_name="Automated Test Client",
        days=30,
        real_mode=True,
        device_id="*",
    )
    assert product_key.startswith("TLKEY-PRO-")

    signed = decode_product_key(product_key)
    pub_bytes = base64.urlsafe_b64decode(PINNED_LEASE_KEYS[key_id])
    verifier = LeaseVerifier({key_id: pub_bytes})

    verified = verifier.verify(signed)
    assert verified.user_id == "Automated Test Client"
    assert verified.real_mode_allowed is True
