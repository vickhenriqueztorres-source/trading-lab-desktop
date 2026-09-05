"""Manifest public key store and build profile segregation (R-BOT-1, R-ISO-2)."""

from __future__ import annotations

import os

# Build profile: defaults to "production". Only "test" admits the test public key.
BUILD_PROFILE = os.environ.get("DUALTRADE_BUILD_PROFILE", "production")

# Canonical test key hex (publicly disclosed, only for test suites)
TEST_KEY_HEX = "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"
TEST_KEY = bytes.fromhex(TEST_KEY_HEX)

# Production public trust roots for key IDs "A" and "B" (32-byte Ed25519 public keys)
PROD_PUBLIC_KEYS: dict[str, bytes] = {
    "A": bytes.fromhex("a865c63da214266748a500044ace980f897772d458930ad915b8c6f1903d7114"),
    "B": bytes.fromhex("b1c2d3e4f5061728394a5b6c7d8e9f0123456789abcdef0123456789abcdef01"),
}


def get_public_keys(build_profile: str | None = None) -> dict[str, bytes]:
    """Return trust store for given build profile. In production, TEST_KEY is strictly absent."""
    profile = build_profile if build_profile is not None else BUILD_PROFILE
    if profile == "test":
        return {
            "A": TEST_KEY,
            "B": TEST_KEY,
            "TEST": TEST_KEY,
        }
    return dict(PROD_PUBLIC_KEYS)


# Module-level PUBLIC_KEYS reflecting the current build profile
PUBLIC_KEYS: dict[str, bytes] = get_public_keys(BUILD_PROFILE)
