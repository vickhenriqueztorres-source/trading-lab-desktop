"""Testes unitários para validação das chaves públicas fixadas (pinned keys)."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from apps.auth_agent.pinned_keys import PINNED_LEASE_KEYS


def decode_urlsafe_b64(value: str) -> bytes:
    """Decodifica string urlsafe base64 garantindo padding correto."""
    padding_needed = (-len(value)) % 4
    padded = value + ("=" * padding_needed)
    return base64.urlsafe_b64decode(padded)


def test_pinned_keys_is_non_empty() -> None:
    """Valida que o dicionário de chaves fixadas possui ao menos uma chave de produção."""
    assert isinstance(PINNED_LEASE_KEYS, dict)
    assert len(PINNED_LEASE_KEYS) >= 1
    assert "tl-2026-09" in PINNED_LEASE_KEYS


@pytest.mark.parametrize("key_id,pubkey_b64", list(PINNED_LEASE_KEYS.items()))
def test_pinned_key_validity(key_id: str, pubkey_b64: str) -> None:
    """Valida que cada chave fixada decodifica para 32 bytes e é uma chave Ed25519 válida."""
    assert isinstance(key_id, str) and key_id.strip() != "", f"key_id inválido: {key_id}"
    assert isinstance(pubkey_b64, str) and pubkey_b64.strip() != "", (
        f"pubkey_b64 vazia para {key_id}"
    )

    # 1. Decodificar urlsafe base64 com padding
    raw_bytes = decode_urlsafe_b64(pubkey_b64)

    # 2. Exatamente 32 bytes (tamanho de chave pública Ed25519)
    assert len(raw_bytes) == 32, f"Chave {key_id} deve ter 32 bytes, mas tem {len(raw_bytes)}"

    # 3. Ed25519PublicKey.from_public_bytes aceita os bytes sem erro
    public_key = Ed25519PublicKey.from_public_bytes(raw_bytes)
    assert isinstance(public_key, Ed25519PublicKey)

    # 4. Confirma que a exportação de volta preserva os bytes
    exported_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    assert exported_raw == raw_bytes
