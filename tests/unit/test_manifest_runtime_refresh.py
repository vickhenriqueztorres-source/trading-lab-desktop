"""CAT-14: background delivery, atomic catalog swap, and fail-closed last-good."""

from __future__ import annotations

import copy
import threading
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.core.lifecycle_service import CoreLifecycleService
from apps.core.manifest_catalog import DynamicManifestCatalog
from apps.core.manifest_client import (
    DEFAULT_MANIFEST_MIRROR_URL,
    DEFAULT_MANIFEST_PRIMARY_URL,
    Accepted,
    HttpResponse,
    ManifestClient,
    ManifestPollState,
    ManifestRecord,
    ManifestRefreshService,
    Rejected,
)
from tests.unit.test_manifest_client_hostile import VALID_DOC_BASE, MockClock, _sign_document


class HeaderAwareHttp:
    def __init__(self) -> None:
        self.responses: dict[str, HttpResponse | Exception] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        self.calls.append((url, dict(headers or {})))
        response = self.responses.get(url, HttpResponse(503))
        if isinstance(response, Exception):
            raise response
        return response


def _client(
    tmp_path: Path,
    key: Ed25519PrivateKey,
    http: HeaderAwareHttp | None = None,
) -> ManifestClient:
    return ManifestClient(
        clock=MockClock(),
        http=http,
        cache_dir=tmp_path / "cache",
        public_keys={"A": key.public_key().public_bytes_raw()},
        allow_test_keys=True,
    )


def test_runtime_defaults_use_deployed_channel_and_repairable_projection() -> None:
    assert DEFAULT_MANIFEST_PRIMARY_URL.endswith("/functions/v1/manifest_current")
    assert ".supabase.co/" in DEFAULT_MANIFEST_PRIMARY_URL
    assert DEFAULT_MANIFEST_MIRROR_URL.endswith("/storage/v1/object/public/manifests/current.json")
    assert "dualtrade.com/manifests/latest.json" not in DEFAULT_MANIFEST_PRIMARY_URL


def test_etag_304_is_per_origin_and_keeps_last_good(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    http = HeaderAwareHttp()
    raw = _sign_document(VALID_DOC_BASE, key)
    http.responses[DEFAULT_MANIFEST_PRIMARY_URL] = HttpResponse(200, {"ETag": '"v1"'}, raw)
    client = _client(tmp_path, key, http)
    assert isinstance(client.poll(force=True), Accepted)

    http.responses[DEFAULT_MANIFEST_PRIMARY_URL] = HttpResponse(304, {"ETag": '"v1"'})
    assert client.poll(force=True) is None
    assert client.last_poll_state is ManifestPollState.NOT_MODIFIED
    assert http.calls[-1] == (
        DEFAULT_MANIFEST_PRIMARY_URL,
        {"If-None-Match": '"v1"'},
    )
    assert client.current() is not None and client.current().manifest_version == 1


def test_304_without_last_good_falls_through_and_thread_stops(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    http = HeaderAwareHttp()
    http.responses[DEFAULT_MANIFEST_PRIMARY_URL] = HttpResponse(304)
    http.responses[DEFAULT_MANIFEST_MIRROR_URL] = HttpResponse(503)
    empty_client = _client(tmp_path, key, http)
    assert empty_client.poll(force=True) is None
    assert empty_client.last_poll_state is ManifestPollState.UNAVAILABLE

    http.responses[DEFAULT_MANIFEST_PRIMARY_URL] = HttpResponse(
        200, body=_sign_document(VALID_DOC_BASE, key)
    )
    service = ManifestRefreshService(
        empty_client,
        poll_interval_s=60,
        jitter=lambda _low, _high: 0.0,
    )
    service.start()
    deadline = threading.Event()
    for _ in range(100):
        if empty_client.current() is not None:
            break
        deadline.wait(0.01)
    service.stop()
    assert empty_client.current() is not None
    assert not service.is_running


def test_primary_rejected_mirror_valid_is_applied_without_partial_state(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    http = HeaderAwareHttp()
    client = _client(tmp_path, key, http)
    catalog = DynamicManifestCatalog()
    client.on_prepare(catalog.prepare_manifest)
    assert isinstance(client.accept(_sign_document(VALID_DOC_BASE, key)), Accepted)

    v2 = copy.deepcopy(VALID_DOC_BASE)
    v2["manifest_version"] = 2
    bad = bytearray(_sign_document(v2, key))
    bad[-2] = ord("x")
    http.responses[DEFAULT_MANIFEST_PRIMARY_URL] = HttpResponse(200, body=bytes(bad))
    http.responses[DEFAULT_MANIFEST_MIRROR_URL] = HttpResponse(
        200, {"ETag": '"v2"'}, _sign_document(v2, key)
    )

    assert isinstance(client.poll(force=True), Accepted)
    assert client.current() is not None and client.current().manifest_version == 2
    assert catalog.manifest_version == 2
    assert [url for url, _headers in http.calls[-2:]] == [
        DEFAULT_MANIFEST_PRIMARY_URL,
        DEFAULT_MANIFEST_MIRROR_URL,
    ]


def test_invalid_server_clock_and_preparation_failure_preserve_cache(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    client = _client(tmp_path, key)
    raw_v1 = _sign_document(VALID_DOC_BASE, key)
    assert isinstance(client.accept(raw_v1), Accepted)
    cache_before = (tmp_path / "cache" / "manifest.json").read_bytes()

    v2 = copy.deepcopy(VALID_DOC_BASE)
    v2["manifest_version"] = 2
    invalid_date = client.accept(_sign_document(v2, key), response_date_header="not-a-date")
    assert isinstance(invalid_date, Rejected)
    assert invalid_date.reason_code == "MANIFEST_SERVER_DATE_INVALID"

    def reject_prepare(_manifest):
        raise ValueError("fixture compiler failure")

    client.on_prepare(reject_prepare)
    rejected = client.accept(_sign_document(v2, key))
    assert isinstance(rejected, Rejected)
    assert rejected.reason_code == "MANIFEST_PREPARATION_FAILED"
    assert client.current() is not None and client.current().manifest_version == 1
    assert (tmp_path / "cache" / "manifest.json").read_bytes() == cache_before


def test_catalog_generation_is_invisible_until_atomic_commit(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    client = _client(tmp_path, key)
    catalog = DynamicManifestCatalog()
    entered = threading.Event()
    release = threading.Event()

    client.on_prepare(catalog.prepare_manifest)

    def pause_v2(manifest):
        if manifest.manifest_version == 2:
            entered.set()
            assert release.wait(2.0)
        return lambda: None

    client.on_prepare(pause_v2)
    assert isinstance(client.accept(_sign_document(VALID_DOC_BASE, key)), Accepted)

    v2 = copy.deepcopy(VALID_DOC_BASE)
    v2["manifest_version"] = 2
    v2["strategies"][0]["key"] = "f1_reversal:GBPUSD:M1:00-06"
    v2["strategies"][0]["asset"] = "GBPUSD"
    outcome: list[Accepted | Rejected] = []
    thread = threading.Thread(target=lambda: outcome.append(client.accept(_sign_document(v2, key))))
    thread.start()
    assert entered.wait(1.0)
    assert client.current() is not None and client.current().manifest_version == 1
    assert catalog.manifest_version == 1
    assert "f1_reversal:EURUSD:M1:00-06" in catalog.active_strategies
    release.set()
    thread.join(2.0)

    assert len(outcome) == 1 and isinstance(outcome[0], Accepted)
    assert client.current() is not None and client.current().manifest_version == 2
    assert catalog.manifest_version == 2
    assert "f1_reversal:GBPUSD:M1:00-06" in catalog.active_strategies


def test_removed_strategy_with_open_order_retires_until_settlement(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    client = _client(tmp_path, key)
    catalog = DynamicManifestCatalog()
    client.on_prepare(catalog.prepare_manifest)
    assert isinstance(client.accept(_sign_document(VALID_DOC_BASE, key)), Accepted)
    strategy_key = VALID_DOC_BASE["strategies"][0]["key"]
    catalog.notify_order_opened(strategy_key, "order-1")

    v2 = copy.deepcopy(VALID_DOC_BASE)
    v2["manifest_version"] = 2
    v2["strategies"] = []
    assert isinstance(client.accept(_sign_document(v2, key)), Accepted)
    assert strategy_key not in catalog.active_strategies
    assert strategy_key in catalog.retiring_strategies
    assert (
        catalog.is_eligible(strategy_key, account_type="PRACTICE", current_payout="0.90")[1]
        == "STRATEGY_RETIRING"
    )

    catalog.notify_order_settled(strategy_key, "order-1")
    assert strategy_key not in catalog.retiring_strategies


def test_refresh_budget_caps_cycles_and_timeout_keeps_last_good(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    http = HeaderAwareHttp()
    client = _client(tmp_path, key, http)
    assert isinstance(client.accept(_sign_document(VALID_DOC_BASE, key)), Accepted)
    http.responses[DEFAULT_MANIFEST_PRIMARY_URL] = TimeoutError("fixture timeout")
    http.responses[DEFAULT_MANIFEST_MIRROR_URL] = TimeoutError("fixture timeout")
    now = [100.0]
    events: list[str] = []
    service = ManifestRefreshService(
        client,
        max_cycles_per_window=2,
        monotonic=lambda: now[0],
        jitter=lambda _low, _high: 0.0,
        on_event=lambda name, _payload: events.append(name),
    )

    assert service.run_once() is ManifestPollState.UNAVAILABLE
    now[0] += 1
    assert service.run_once() is ManifestPollState.UNAVAILABLE
    now[0] += 1
    assert service.run_once() is ManifestPollState.NOT_DUE
    assert len(http.calls) == 4
    assert "manifest_poll_budget_pressure" in events
    assert "manifest_poll_budget_exhausted" in events
    assert client.current() is not None and client.current().manifest_version == 1


def test_manifest_http_errors_report_stable_sanitized_causes(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    http = HeaderAwareHttp()
    events: list[tuple[str, dict[str, object]]] = []
    client = ManifestClient(
        clock=MockClock(),
        http=http,
        cache_dir=tmp_path / "cache",
        public_keys={"A": key.public_key().public_bytes_raw()},
        allow_test_keys=True,
        on_event=lambda name, payload: events.append((name, payload)),
    )
    http.responses[DEFAULT_MANIFEST_PRIMARY_URL] = HttpResponse(
        503,
        body=b'{"error":"HUB_MANIFEST_LAST_GOOD_UNAVAILABLE"}',
    )
    http.responses[DEFAULT_MANIFEST_MIRROR_URL] = HttpResponse(
        400,
        body=b'{"code":"NoSuchKey","message":"untrusted detail"}',
    )

    assert client.poll(force=True) is None
    failures = [payload for name, payload in events if name == "manifest_source_unavailable"]
    assert [item["reason_code"] for item in failures] == [
        "HUB_MANIFEST_LAST_GOOD_UNAVAILABLE",
        "MANIFEST_MIRROR_OBJECT_MISSING",
    ]
    assert all("body" not in item and "message" not in item for item in failures)


def test_runtime_manifest_change_disarms_only_iqoption(tmp_path: Path) -> None:
    lifecycle = CoreLifecycleService(
        tmp_path,
        ("simulated",),
        force_auth_simulation=True,
        manifest_remote_enabled=False,
    )
    lifecycle._iqoption_bot_armed = True
    lifecycle._safe_stop = False
    record = ManifestRecord(
        schema_version=1,
        manifest_version=7,
        key_id="A",
        published_at=1,
        expires_at=2,
        primitives_version="1.0.0",
        primitives_parity_sha256="sha256:" + "0" * 64,
        research_run_id="fixture",
        schema_revision=None,
        execution_semantics_version=None,
        dataset_evidence=None,
        telemetry=None,
        strategies=(),
        signature="ed25519:fixture",
        raw_bytes=b"{}",
    )

    lifecycle._on_manifest_applied(record)

    assert lifecycle._iqoption_bot_armed is False
    assert lifecycle._iqoption_bot_reason == "IQOPTION_BOT_DISARMED_AFTER_MANIFEST_CHANGE"
    assert lifecycle._safe_stop is False  # Deriv authority is broker-isolated.
