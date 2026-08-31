from __future__ import annotations

from apps.core.state.idempotency import generate_dedupe_key, is_duplicate


def _key(direction: str = "CALL") -> str:
    return generate_dedupe_key(
        "account-1",
        "strategy-1",
        "EURUSD",
        "2026-08-31T10:00:00Z",
        direction,
        60,
        "v1",
    )


def test_dedupe_key_is_deterministic() -> None:
    assert _key() == _key()
    assert len(_key()) == 64


def test_different_intent_fields_do_not_collide() -> None:
    assert _key("CALL") != _key("PUT")


def test_store_duplicate_check_is_delegated() -> None:
    class Store:
        def idempotency_key_exists(self, dedupe_key: str) -> bool:
            return dedupe_key == "known"

    assert is_duplicate(Store(), "known")
    assert not is_duplicate(Store(), "new")
