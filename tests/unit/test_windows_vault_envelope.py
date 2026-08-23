from __future__ import annotations

import hashlib
import secrets

import pytest

from packages.security.windows_vault import (
    VaultIntegrityError,
    _decode_envelope,
    _decode_plaintext,
    _encode_envelope,
    _encode_plaintext,
)


def test_vault_envelope_rejects_truncation_and_checksum_corruption() -> None:
    envelope = _encode_envelope(secrets.token_bytes(96))

    with pytest.raises(VaultIntegrityError, match="VAULT_INTEGRITY_FAILED"):
        _decode_envelope(envelope[:12])

    corrupted = bytearray(envelope)
    corrupted[-1] ^= 0x01
    with pytest.raises(VaultIntegrityError, match="VAULT_INTEGRITY_FAILED"):
        _decode_envelope(bytes(corrupted))


def test_vault_plaintext_package_rejects_wrong_key_and_internal_corruption() -> None:
    digest = hashlib.sha256(b"runtime-key-a").digest()
    other_digest = hashlib.sha256(b"runtime-key-b").digest()
    payload = _encode_plaintext(digest, secrets.token_bytes(64))

    with pytest.raises(VaultIntegrityError, match="VAULT_INTEGRITY_FAILED"):
        _decode_plaintext(other_digest, payload)

    corrupted = bytearray(payload)
    corrupted[-1] ^= 0x01
    with pytest.raises(VaultIntegrityError, match="VAULT_INTEGRITY_FAILED"):
        _decode_plaintext(digest, bytes(corrupted))
