from __future__ import annotations

import secrets
import sys

import pytest

from packages.security.dpapi import (
    CRYPTPROTECT_UI_FORBIDDEN,
    PROTECTION_FLAGS,
    VaultDecryptionError,
    VaultPlatformError,
    win32_crypt_protect_data,
    win32_crypt_unprotect_data,
)


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is only available on Windows")
def test_dpapi_current_user_round_trip_without_entropy() -> None:
    plaintext = secrets.token_bytes(64)

    ciphertext = win32_crypt_protect_data(plaintext)

    assert ciphertext != plaintext
    assert plaintext not in ciphertext
    assert win32_crypt_unprotect_data(ciphertext) == plaintext
    assert PROTECTION_FLAGS == CRYPTPROTECT_UI_FORBIDDEN


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is only available on Windows")
def test_dpapi_current_user_round_trip_is_bound_to_entropy() -> None:
    plaintext = secrets.token_bytes(48)
    entropy = secrets.token_bytes(32)
    ciphertext = win32_crypt_protect_data(plaintext, entropy=entropy)

    assert win32_crypt_unprotect_data(ciphertext, entropy=entropy) == plaintext
    with pytest.raises(VaultDecryptionError, match="VAULT_DECRYPTION_FAILED"):
        win32_crypt_unprotect_data(ciphertext, entropy=secrets.token_bytes(32))


def test_dpapi_rejects_invalid_inputs_without_calling_windows() -> None:
    with pytest.raises(ValueError, match="non-empty bytes"):
        win32_crypt_protect_data(b"")
    with pytest.raises(ValueError, match="non-empty bytes"):
        win32_crypt_unprotect_data(b"")


@pytest.mark.skipif(sys.platform == "win32", reason="negative platform path is non-Windows only")
def test_dpapi_fails_closed_on_unsupported_platform() -> None:
    with pytest.raises(VaultPlatformError, match="VAULT_PLATFORM_UNSUPPORTED"):
        win32_crypt_protect_data(secrets.token_bytes(8))
