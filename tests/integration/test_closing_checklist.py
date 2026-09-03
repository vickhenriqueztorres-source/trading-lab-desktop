"""Integration tests proving the Project Closing Checklist criteria.

Criteria verified:
- Item 3: publish -> bot Demo updates new strategy in <= 15 min without restart.
- Item 4: Network disconnected for 1h -> bot operates normally from disk cache.
- Item 5: Payout below payout_min -> card displays 'aguardando', zero orders dispatched.
- Item 6: Synthetic live_outcomes with p = p_min -> SPRT demotes strategy in < 120 ops.
- Item 9: Key B signs manifest and bot accepts (rotation test).
"""

from __future__ import annotations

import base64
import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.core.live_monitor import STRATEGY_DEMOTED_BY_SPRT, LiveMonitor
from apps.core.manifest_catalog import DynamicManifestCatalog, parse_strategy_entry
from apps.core.manifest_client import (
    Accepted,
    ClockProtocol,
    HttpResponse,
    HttpTransportProtocol,
    ManifestClient,
    canonical_bytes,
)
from apps.core.payout_gate import PAYOUT_BELOW_VALIDATED_EDGE, PayoutGate
from packages.sprt import Decision


class FakeClock(ClockProtocol):
    def __init__(self, start_ts: int = 1_700_000_000, start_mono: float = 1000.0) -> None:
        self._epoch = start_ts
        self._mono = start_mono

    def monotonic(self) -> float:
        return self._mono

    def now_utc_epoch(self) -> int:
        return self._epoch

    def advance(self, seconds: float) -> None:
        self._epoch += int(seconds)
        self._mono += seconds


class FakeHttp(HttpTransportProtocol):
    def __init__(self) -> None:
        self.responses: dict[str, HttpResponse] = {}
        self.network_connected: bool = True
        self.requests: list[str] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        self.requests.append(url)
        if not self.network_connected:
            raise ConnectionError("Network unreachable (offline simulation)")
        if url in self.responses:
            return self.responses[url]
        return HttpResponse(status_code=404, body=b"Not found")


def _sign_manifest(manifest_dict: dict[str, Any], private_key: Ed25519PrivateKey) -> bytes:
    canonical = canonical_bytes(manifest_dict)
    sig_bytes = private_key.sign(canonical)
    signed_doc = dict(manifest_dict)
    signed_doc["signature"] = "ed25519:" + base64.b64encode(sig_bytes).decode("ascii")
    return json.dumps(signed_doc).encode("utf-8")


def _make_strategy(key: str, display_name: str, asset: str, status: str = "approved") -> dict[str, Any]:
    return {
        "key": key,
        "family": "F1",
        "display_name_pt": display_name,
        "asset": asset,
        "timeframe": "M1",
        "hours_utc": [0, 24],
        "params": {
            "rsi_len": "14",
            "rsi_lo": "30",
            "rsi_hi": "70",
            "bb_len": "20",
            "bb_k": "2.0",
            "adx_len": "14",
            "adx_max": "30",
        },
        "management": {
            "stake_pct": "1.0",
            "martingale_steps_max": 2,
            "paroli": True,
        },
        "validated": {
            "p_hat": "0.578",
            "wilson_lower": "0.561",
            "p_min_at_validation": "0.541",
            "payout_min": "0.84",
            "ops_per_day": "25.0",
            "worst_streak": 4,
            "n": 1000,
            "windows_passed": "8/8",
            "holdout_passed": True,
            "result_1000_ops_stake10": "1500.00",
        },
        "status": status,
    }


def _make_sample_manifest(
    manifest_version: int,
    key_id: str,
    private_key: Ed25519PrivateKey,
    strategies: list[dict[str, Any]],
    primitives_parity_sha256: str = "sha256:f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10",
) -> bytes:
    doc = {
        "schema_version": 1,
        "manifest_version": manifest_version,
        "key_id": key_id,
        "published_at": 1_700_000_000,
        "expires_at": 1_700_000_000 + 30 * 86400,
        "primitives_version": "1.0.0",
        "primitives_parity_sha256": primitives_parity_sha256,
        "research_run_id": f"run_{manifest_version}",
        "strategies": strategies,
    }
    return _sign_manifest(doc, private_key)


def test_checklist_item_3_publish_to_bot_demo_in_15_min_no_restart(tmp_path: Path) -> None:
    """Item 3: publish -> bot Demo mostra a estratégia nova em <= 15 min sem restart."""
    clock = FakeClock()
    http = FakeHttp()
    cache_dir = tmp_path / "cache"

    priv_key_a = Ed25519PrivateKey.generate()
    pub_key_a_bytes = priv_key_a.public_key().public_bytes_raw()

    applied_manifests: list[Any] = []

    client = ManifestClient(
        clock=clock,
        http=http,
        cache_dir=cache_dir,
        public_keys={"A": pub_key_a_bytes},
        allow_test_keys=True,
    )
    client.on_change(lambda m: applied_manifests.append(m))

    catalog = DynamicManifestCatalog()
    client.on_change(catalog.apply_manifest)

    # Initial publish: Version 1 with Strategy 1
    strat1 = _make_strategy("EURUSD-OTC:F1:strat1", "F1 EURUSD-OTC M1", "EURUSD-OTC")
    raw_v1 = _make_sample_manifest(1, "A", priv_key_a, [strat1])
    http.responses[client._primary_url] = HttpResponse(status_code=200, body=raw_v1)

    # First poll at t=0
    client.poll()
    assert len(applied_manifests) == 1
    assert "EURUSD-OTC:F1:strat1" in catalog.active_strategies

    # Fast-forward 15 minutes (900 seconds)
    clock.advance(900.0)

    # New publish on server: Version 2 includes an additional new opportunity
    strat2 = _make_strategy("GBPUSD-OTC:F1:strat2", "F1 GBPUSD-OTC M1", "GBPUSD-OTC")
    raw_v2 = _make_sample_manifest(2, "A", priv_key_a, [strat1, strat2])
    http.responses[client._primary_url] = HttpResponse(status_code=200, body=raw_v2)

    # Next 15-minute scheduled poll: receives v2 and updates catalog in-place WITHOUT restart
    client.poll()
    assert len(applied_manifests) == 2
    assert "GBPUSD-OTC:F1:strat2" in catalog.active_strategies
    assert "EURUSD-OTC:F1:strat1" in catalog.active_strategies


def test_checklist_item_4_offline_cache_resilience_1h(tmp_path: Path) -> None:
    """Item 4: Desligar a rede do PC de teste por 1 h -> bot opera normalmente com cache."""
    clock = FakeClock()
    http = FakeHttp()
    cache_dir = tmp_path / "cache"

    priv_key_a = Ed25519PrivateKey.generate()
    pub_key_a_bytes = priv_key_a.public_key().public_bytes_raw()

    strat = _make_strategy("EURUSD-OTC:F1:cached", "F1 EURUSD M1", "EURUSD-OTC")
    raw_v1 = _make_sample_manifest(1, "A", priv_key_a, [strat])
    http.responses["https://storage.dualtrade.com/manifests/latest.json"] = HttpResponse(
        status_code=200, body=raw_v1
    )

    # Initial online poll saves to cache
    client = ManifestClient(
        clock=clock,
        http=http,
        cache_dir=cache_dir,
        public_keys={"A": pub_key_a_bytes},
        allow_test_keys=True,
    )
    catalog = DynamicManifestCatalog()
    client.on_change(catalog.apply_manifest)
    client.poll()

    assert (cache_dir / "manifest.json").exists()
    assert "EURUSD-OTC:F1:cached" in catalog.active_strategies

    # SIMULATE NETWORK DISCONNECTION (Unplug network)
    http.network_connected = False

    # Simulate 1 hour passing (4 polling cycles of 15 min each)
    for _ in range(4):
        clock.advance(900.0)
        client.poll()

        # Bot must remain active with the cached strategies without crash or clearing
        assert "EURUSD-OTC:F1:cached" in catalog.active_strategies
        assert client.current() is not None
        assert client.current().manifest_version == 1


def test_checklist_item_5_payout_gate_blocks_under_payout_min() -> None:
    """Item 5: Simular payout abaixo de payout_min -> ficha mostra 'aguardando', nenhuma ordem."""
    wilson_lower = Decimal("0.60")
    payout_min = Decimal("0.85")  # Requires at least 85% payout

    # Current broker payout drops to 80% (below 85%)
    current_payout = Decimal("0.80")

    result = PayoutGate.check_payout(current_payout, wilson_lower, payout_min)

    # Must be blocked fail-closed
    assert result.allowed is False
    assert result.reason_code == PAYOUT_BELOW_VALIDATED_EDGE
    # Must display explicit pt-BR status message containing 'aguardando'
    assert "aguardando" in result.message
    assert "Opera com payout ≥ 85%. Agora: 80% — aguardando." == result.message


def test_checklist_item_6_sprt_demotes_under_p_min_in_under_120_ops() -> None:
    """Item 6: Alimentar live_outcomes sintéticos com p = p_min -> SPRT rebaixa em < 120 ops."""
    catalog = DynamicManifestCatalog()
    events: list[tuple[str, dict[str, Any]]] = []

    class MockEventSink:
        def emit(self, event_name: str, **kwargs: Any) -> None:
            events.append((event_name, kwargs))

    monitor = LiveMonitor(catalog=catalog, event_sink=MockEventSink())  # type: ignore[arg-type]

    # Validated strategy with p0 = 0.65, p1 = 0.55
    entry_dict = {
        "key": "EURUSD-OTC:F1:sprt_test",
        "family": "F1",
        "display_name_pt": "F1 EURUSD",
        "asset": "EURUSD-OTC",
        "timeframe": "M1",
        "hours_utc": (0, 24),
        "params": {
            "rsi_len": 14,
            "rsi_lo": Decimal(30),
            "rsi_hi": Decimal(70),
            "bb_len": 20,
            "bb_k": Decimal("2.0"),
            "adx_len": 14,
            "adx_max": Decimal(30),
        },
        "validated": {
            "p_hat": Decimal("0.60"),
            "wilson_lower": Decimal("0.58"),
            "p_min_at_validation": Decimal("0.46"),
            "payout_min": Decimal("0.80"),
            "ops_per_day": Decimal("20"),
            "worst_streak": 4,
            "result_1000_ops_stake10": Decimal("1500"),
            "score": Decimal("3"),
        },
        "status": "approved",
    }
    catalog.apply_manifest({"manifest_version": 1, "strategies": [entry_dict]})
    monitor.sync_from_catalog()

    # Feed synthetic outcomes with true win rate p = p_min = 0.46
    rng = random.Random(2)
    demoted_at_n: int | None = None

    for i in range(1, 121):
        # Bernoulli trial with true win rate p = p_min = 0.46
        won = rng.random() < 0.46
        decision = monitor.on_settlement(
            strategy_key="EURUSD-OTC:F1:sprt_test",
            won=won,
            ts=1_700_000_000 + i * 60,
            payout_pct=Decimal("0.85"),
        )
        if decision == Decision.REJECT_H0:
            demoted_at_n = i
            break

    assert demoted_at_n is not None, "SPRT failed to demote strategy within 120 operations!"
    assert demoted_at_n < 120, f"Demoted at {demoted_at_n}, expected < 120"

    # Catalog state must be downgraded to observation
    strat = catalog.get_strategy("EURUSD-OTC:F1:sprt_test")
    assert strat is not None
    assert strat.status == "observation"
    assert any(ev[0] == "strategy_demoted" for ev in events)


def test_checklist_item_9_key_b_rotation_accepted(tmp_path: Path) -> None:
    """Item 9: Chave B assina um manifesto e o bot aceita (teste de rotação)."""
    clock = FakeClock()
    http = FakeHttp()
    cache_dir = tmp_path / "cache"

    priv_key_b = Ed25519PrivateKey.generate()
    pub_key_b_bytes = priv_key_b.public_key().public_bytes_raw()

    priv_key_a = Ed25519PrivateKey.generate()
    pub_key_a_bytes = priv_key_a.public_key().public_bytes_raw()

    # Bot initialized with dual public keys (A and B trusted)
    client = ManifestClient(
        clock=clock,
        http=http,
        cache_dir=cache_dir,
        public_keys={"A": pub_key_a_bytes, "B": pub_key_b_bytes},
        allow_test_keys=True,
    )

    strat = _make_strategy("EURUSD-OTC:F1:rot_b", "F1 EURUSD M1", "EURUSD-OTC")

    # Manifest published and signed exclusively with Key B
    raw_manifest_b = _make_sample_manifest(
        manifest_version=10,
        key_id="B",
        private_key=priv_key_b,
        strategies=[strat],
    )

    result = client.accept(raw_manifest_b)

    assert isinstance(result, Accepted)
    curr = client.current()
    assert curr is not None
    assert curr.manifest_version == 10
    assert curr.key_id == "B"
