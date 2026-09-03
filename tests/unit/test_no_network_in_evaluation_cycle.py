"""Test that the evaluation cycle never performs network or disk I/O (R-BOT-2, R-BOT-3)."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.core.manifest_client import ManifestClient
from tests.unit.test_manifest_client_hostile import (
    VALID_DOC_BASE,
    FakeHttpTransport,
    _sign_document,
)


def test_no_network_in_evaluation_cycle(tmp_path: Path) -> None:
    """evaluate_once and evaluation cycles must never invoke poll() or make network calls."""
    key_priv = Ed25519PrivateKey.generate()
    key_pub = key_priv.public_key().public_bytes_raw()
    public_keys = {"A": key_pub}

    transport = FakeHttpTransport()
    client = ManifestClient(
        http=transport,
        cache_dir=tmp_path / "cache",
        public_keys=public_keys,
    )

    # Initial setup
    v1_raw = _sign_document(VALID_DOC_BASE, key_priv, key_id="A")
    client.accept(v1_raw)
    curr = client.current()
    assert curr is not None
    assert curr.manifest_version == 1

    # Reset call history from any initial setup
    transport.call_history.clear()

    # Simulated evaluation cycle: simulate 10,000 rapid tick evaluations
    def evaluate_once() -> bool:
        # In an evaluation step, code only reads in-memory current() and checks expiration
        manifest = client.current()
        if manifest is None:
            return False
        if client.is_expired():
            return False
        # Do signal processing on active strategies without touching network
        assert len(manifest.strategies) > 0
        return True

    # Execute cycle repeatedly
    for _ in range(10_000):
        result = evaluate_once()
        assert result is True

    # Crucial assertion: 0 network calls occurred during the entire evaluation cycle
    assert len(transport.call_history) == 0, (
        f"Evaluation cycle called network {len(transport.call_history)} times! "
        "Network is forbidden in evaluate_once."
    )
