"""Hostile manifest rejection and fail-closed persistence tests (R-BOT-1..4, CI intocável)."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.core.manifest_client import (
    DEFAULT_PARITY_SHA256,
    Accepted,
    HttpResponse,
    ManifestClient,
    Rejected,
    canonical_bytes,
)

# Sample valid document template for tests
VALID_DOC_BASE: dict[str, Any] = {
    "schema_version": 1,
    "manifest_version": 1,
    "key_id": "A",
    "published_at": 1788350400,
    "expires_at": 1792238400,
    "primitives_version": "1.0.0",
    "primitives_parity_sha256": DEFAULT_PARITY_SHA256,
    "research_run_id": "run_test_hostile",
    "strategies": [
        {
            "key": "f1_reversal:EURUSD:M1:00-06",
            "family": "F1",
            "display_name_pt": "Reversão de Extremo",
            "asset": "EURUSD",
            "timeframe": "M1",
            "hours_utc": [0, 6],
            "params": {
                "adx_len": "14",
                "adx_max": "20",
                "bb_len": "20",
                "bb_k": "2.0",
                "rsi_len": "7",
                "rsi_lo": "20",
                "rsi_hi": "80",
            },
            "validated": {
                "p_hat": "0.578",
                "wilson_lower": "0.561",
                "p_min_at_validation": "0.541",
                "payout_min": "0.84",
                "n": 1240,
                "ops_per_day": "11.2",
                "worst_streak": 6,
                "result_1000_ops_stake10": "182.00",
                "windows_passed": "8/8",
                "holdout_passed": True,
            },
            "status": "observation",
            "management": {
                "stake_pct": "1.0",
                "martingale_steps_max": 2,
                "paroli": True,
            },
        }
    ],
}


def _sign_document(doc: dict[str, Any], private_key: Ed25519PrivateKey, key_id: str = "A") -> bytes:
    doc_copy = copy.deepcopy(doc)
    doc_copy["key_id"] = key_id
    unsigned = {k: v for k, v in doc_copy.items() if k != "signature"}
    payload = canonical_bytes(unsigned)
    signature_bytes = private_key.sign(payload)
    doc_copy["signature"] = "ed25519:" + base64.b64encode(signature_bytes).decode("ascii")
    return json.dumps(doc_copy, ensure_ascii=False).encode("utf-8")


class FakeHttpTransport:
    def __init__(self) -> None:
        self.responses: dict[str, HttpResponse | Exception] = {}
        self.call_history: list[str] = []

    def set_response(self, url: str, resp_or_exc: HttpResponse | Exception) -> None:
        self.responses[url] = resp_or_exc

    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        self.call_history.append(url)
        res = self.responses.get(url)
        if isinstance(res, Exception):
            raise res
        if res is not None:
            return res
        return HttpResponse(status_code=404, headers={}, body=b"Not Found")


class MockClock:
    def __init__(self, now_epoch: int = 1788350500) -> None:
        self.epoch = now_epoch
        self.mono = 1000.0

    def now_utc_epoch(self) -> int:
        return self.epoch

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.epoch += int(seconds)
        self.mono += seconds


def test_hostile_manifests_rejected(tmp_path: Path) -> None:
    """CI intocável: 11 cenários hostis onde o manifesto anterior permanece ativo.

    Cenários:
    1. Assinatura inválida
    2. Chave de teste em build prod
    3. Versão regressiva (v <= v_atual)
    4. Primitives version divergente
    5. Primitives parity SHA-256 divergente
    6. Params fora de faixa (ex: RSI hi fora de 51..99)
    7. Expirado (por cabeçalho HTTP Date do CDN)
    8. Cache local truncado/corrompido
    9. Cache local com assinatura alterada
    10. Primário fora -> espelho R2 funciona e aceita
    11. Ambos fora -> cache local mantido
    """
    key_priv = Ed25519PrivateKey.generate()
    key_pub = key_priv.public_key().public_bytes_raw()
    public_keys = {"A": key_pub, "B": key_pub}

    clock = MockClock(now_epoch=1788350500)
    cache_dir = tmp_path / "cache"

    transport = FakeHttpTransport()
    primary_url = "https://storage.dualtrade.com/manifests/latest.json"
    mirror_url = "https://r2.dualtrade.com/manifests/latest.json"

    client = ManifestClient(
        clock=clock,
        http=transport,
        cache_dir=cache_dir,
        public_keys=public_keys,
        primary_url=primary_url,
        mirror_url=mirror_url,
        allow_test_keys=False,
    )

    def _assert_active_version(expected: int) -> None:
        curr = client.current()
        assert curr is not None
        assert curr.manifest_version == expected

    # Apply initial valid manifest v1
    v1_raw = _sign_document(VALID_DOC_BASE, key_priv, key_id="A")
    res1 = client.accept(v1_raw)
    assert isinstance(res1, Accepted)
    _assert_active_version(1)

    # Scenario 1: Assinatura inválida
    v2_bad_sig = copy.deepcopy(VALID_DOC_BASE)
    v2_bad_sig["manifest_version"] = 2
    raw2_bad_sig = _sign_document(v2_bad_sig, key_priv, key_id="A")
    tampered = json.loads(raw2_bad_sig.decode("utf-8"))
    tampered["signature"] = "ed25519:" + base64.b64encode(b"\x00" * 64).decode("ascii")
    res_sc1 = client.accept(json.dumps(tampered).encode("utf-8"))
    assert isinstance(res_sc1, Rejected)
    assert res_sc1.reason_code == "MANIFEST_SIGNATURE_INVALID"
    _assert_active_version(1)

    # Scenario 2: Chave de teste em build prod
    dummy_key = bytes.fromhex("00" * 31 + "01")
    test_priv = Ed25519PrivateKey.from_private_bytes(dummy_key)
    v2_test_key = copy.deepcopy(VALID_DOC_BASE)
    v2_test_key["manifest_version"] = 2
    v2_test_key["key_id"] = "A"
    raw_test_key = _sign_document(v2_test_key, test_priv, key_id="A")
    res_sc2 = client.accept(raw_test_key)
    assert isinstance(res_sc2, Rejected)
    assert res_sc2.reason_code == "MANIFEST_SIGNATURE_INVALID"
    _assert_active_version(1)

    # Scenario 3: Versão regressiva (v <= v_atual)
    v0_regressive = copy.deepcopy(VALID_DOC_BASE)
    v0_regressive["manifest_version"] = 1  # equal to current
    raw_v0 = _sign_document(v0_regressive, key_priv, key_id="A")
    res_sc3 = client.accept(raw_v0)
    assert isinstance(res_sc3, Rejected)
    assert res_sc3.reason_code == "MANIFEST_REJECTED_REGRESSIVE_VERSION"
    _assert_active_version(1)

    # Scenario 4: Primitives version divergente
    v2_bad_primitives = copy.deepcopy(VALID_DOC_BASE)
    v2_bad_primitives["manifest_version"] = 2
    v2_bad_primitives["primitives_version"] = "2.0.0"
    raw_bad_prim = _sign_document(v2_bad_primitives, key_priv, key_id="A")
    res_sc4 = client.accept(raw_bad_prim)
    assert isinstance(res_sc4, Rejected)
    assert res_sc4.reason_code == "MANIFEST_PRIMITIVES_VERSION"
    _assert_active_version(1)

    # Scenario 5: Primitives parity SHA-256 divergente
    v2_bad_parity = copy.deepcopy(VALID_DOC_BASE)
    v2_bad_parity["manifest_version"] = 2
    v2_bad_parity["primitives_parity_sha256"] = "sha256:" + "0" * 64
    raw_bad_par = _sign_document(v2_bad_parity, key_priv, key_id="A")
    res_sc5 = client.accept(raw_bad_par)
    assert isinstance(res_sc5, Rejected)
    assert res_sc5.reason_code == "MANIFEST_PRIMITIVES_PARITY"
    _assert_active_version(1)

    # Scenario 6: Params fora de faixa (RSI hi fora da faixa 51..99)
    v2_bad_param = copy.deepcopy(VALID_DOC_BASE)
    v2_bad_param["manifest_version"] = 2
    v2_bad_param["strategies"][0]["params"]["rsi_hi"] = "105"
    raw_bad_param = _sign_document(v2_bad_param, key_priv, key_id="A")
    res_sc6 = client.accept(raw_bad_param)
    assert isinstance(res_sc6, Rejected)
    assert res_sc6.reason_code == "MANIFEST_PARAM_RANGE"
    _assert_active_version(1)

    # Scenario 7: Expirado com base no cabeçalho HTTP Date do CDN
    v2_expired = copy.deepcopy(VALID_DOC_BASE)
    v2_expired["manifest_version"] = 2
    v2_expired["expires_at"] = 1792238400
    raw_v2_exp = _sign_document(v2_expired, key_priv, key_id="A")
    cdn_date_future = "Tue, 01 Jan 2030 00:00:00 GMT"
    res_sc7 = client.accept(raw_v2_exp, response_date_header=cdn_date_future)
    assert isinstance(res_sc7, Rejected)
    assert res_sc7.reason_code == "MANIFEST_REJECTED_EXPIRED"
    _assert_active_version(1)

    # Scenario 8: Cache local truncado
    cache_file = cache_dir / "manifest.json"
    cache_file.write_bytes(b'{"truncated": true')
    client_reloaded = ManifestClient(
        clock=clock,
        cache_dir=cache_dir,
        public_keys=public_keys,
    )
    assert client_reloaded.current() is None
    client_reloaded.accept(v1_raw)
    curr_rel = client_reloaded.current()
    assert curr_rel is not None
    assert curr_rel.manifest_version == 1

    # Scenario 9: Cache local com assinatura alterada
    tampered_cache = json.loads(v1_raw.decode("utf-8"))
    tampered_cache["signature"] = "ed25519:" + base64.b64encode(b"\xff" * 64).decode("ascii")
    cache_file.write_text(json.dumps(tampered_cache), encoding="utf-8")
    client_reload_tampered = ManifestClient(
        clock=clock,
        cache_dir=cache_dir,
        public_keys=public_keys,
    )
    assert client_reload_tampered.current() is None
    client.accept(v1_raw)
    _assert_active_version(1)

    # Scenario 10: Primário indisponível -> espelho R2 responde 200 e é aceito
    v2_valid = copy.deepcopy(VALID_DOC_BASE)
    v2_valid["manifest_version"] = 2
    raw_v2 = _sign_document(v2_valid, key_priv, key_id="A")
    transport.set_response(primary_url, ConnectionError("Primary CDN timeout"))
    transport.set_response(
        mirror_url,
        HttpResponse(
            status_code=200,
            headers={"Date": "Mon, 01 Sep 2026 12:00:00 GMT"},
            body=raw_v2,
        ),
    )
    poll_res = client.poll(force=True)
    assert isinstance(poll_res, Accepted)
    _assert_active_version(2)

    # Scenario 11: Ambos indisponíveis -> cache local mantido
    transport.set_response(primary_url, ConnectionError("Primary down"))
    transport.set_response(mirror_url, ConnectionError("Mirror down"))
    poll_res_both_down = client.poll(force=True)
    assert poll_res_both_down is None
    _assert_active_version(2)
