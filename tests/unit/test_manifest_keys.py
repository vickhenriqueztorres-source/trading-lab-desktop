"""Tests for manifest public key store and build profile isolation (R-BOT-1, R-ISO-2)."""

from __future__ import annotations

from apps.core.manifest_keys import (
    PROD_PUBLIC_KEYS,
    PUBLIC_KEYS,
    TEST_KEY,
    TEST_KEY_HEX,
    get_public_keys,
)


def test_production_build_strictly_excludes_test_key() -> None:
    """Build prod: TEST_KEY is absent from PUBLIC_KEYS and get_public_keys('production')."""
    prod_keys = get_public_keys("production")

    # TEST_KEY should not be a value for any key in production
    assert TEST_KEY not in prod_keys.values(), "TEST_KEY must not exist in production trust store"
    assert "TEST" not in prod_keys, "'TEST' key ID must not exist in production trust store"

    # Both A and B must be present and distinct from TEST_KEY
    assert "A" in prod_keys
    assert "B" in prod_keys
    assert prod_keys["A"] != TEST_KEY
    assert prod_keys["B"] != TEST_KEY
    assert len(prod_keys["A"]) == 32
    assert len(prod_keys["B"]) == 32

    # Module-level PUBLIC_KEYS when in default production profile
    if "TEST" not in PUBLIC_KEYS and PUBLIC_KEYS.get("A") == PROD_PUBLIC_KEYS["A"]:
        assert TEST_KEY not in PUBLIC_KEYS.values()


def test_test_build_profile_includes_test_key() -> None:
    """Test build profile: TEST_KEY is admitted for test suites and acceptance harnesses."""
    test_keys = get_public_keys("test")
    assert "A" in test_keys
    assert "B" in test_keys
    assert "TEST" in test_keys
    assert test_keys["TEST"] == TEST_KEY
    assert test_keys["A"] == TEST_KEY
    assert test_keys["B"] == TEST_KEY
    assert test_keys["TEST"].hex() == TEST_KEY_HEX
