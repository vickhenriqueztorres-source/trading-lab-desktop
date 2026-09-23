from __future__ import annotations

from packages.brokers.iqoption.community_read_only import (
    ActiveIdentityResolver,
    CanonicalActive,
)


def test_active_identity_resolver_preseeded_pairs() -> None:
    resolver = ActiveIdentityResolver()

    # Standard Forex
    eurusd_id = resolver.get_active_id("EURUSD")
    assert eurusd_id == 1
    assert resolver.get_symbol(1) == "EURUSD"

    # OTC pairs
    otc_id = resolver.get_active_id("EURUSD-OTC")
    assert otc_id == 76
    assert resolver.get_symbol(76) == "EURUSD-OTC"


def test_active_identity_resolver_resolve_numeric_and_symbol() -> None:
    resolver = ActiveIdentityResolver()

    # Numeric ID resolving to canonical symbol
    res1 = resolver.resolve(1)
    assert isinstance(res1, CanonicalActive)
    assert res1.symbol == "EURUSD"
    assert res1.active_id == 1

    # OTC string ID resolving to canonical symbol
    res76 = resolver.resolve("76")
    assert isinstance(res76, CanonicalActive)
    assert res76.symbol == "EURUSD-OTC"
    assert res76.active_id == 76

    # With expected symbol
    res_exp = resolver.resolve(76, expected_symbol="EURUSD-OTC")
    assert isinstance(res_exp, CanonicalActive)
    assert res_exp.symbol == "EURUSD-OTC"
    assert res_exp.resolution_source == "EXPECTED_SYMBOL_MATCH"

    # Unknown
    assert resolver.resolve("999999") is None
    assert resolver.resolve(None) is None


def test_active_identity_resolver_update_catalog() -> None:
    resolver = ActiveIdentityResolver()
    resolver.update_catalog({"TESTUSD": 999, "TESTUSD-OTC": 1000}, generation=2)

    assert resolver.generation == 2
    assert resolver.get_active_id("TESTUSD") == 999
    assert resolver.get_symbol(999) == "TESTUSD"
    assert resolver.get_active_id("TESTUSD-OTC") == 1000
    assert resolver.get_symbol(1000) == "TESTUSD-OTC"

    res = resolver.resolve(999)
    assert isinstance(res, CanonicalActive)
    assert res.symbol == "TESTUSD"
    assert res.catalog_generation == 2
