"""Unit tests for /.well-known/lease-keys endpoint."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient


def test_wellknown_returns_active_key_and_cache_header(app_client: TestClient) -> None:
    """Active Ed25519 signing key appears in /.well-known/lease-keys with valid 32 bytes format."""
    response = app_client.get("/.well-known/lease-keys")
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "public, max-age=3600"

    data = response.json()
    assert "keys" in data
    keys = data["keys"]
    assert isinstance(keys, dict)
    assert len(keys) >= 1

    for key_id, pubkey_b64 in keys.items():
        assert isinstance(key_id, str) and key_id.strip()
        assert isinstance(pubkey_b64, str) and pubkey_b64.strip()

        # Decodes URL-safe base64 into raw 32 bytes (Ed25519 public key)
        raw_key = base64.urlsafe_b64decode(pubkey_b64)
        assert len(raw_key) == 32
