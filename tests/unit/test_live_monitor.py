"""Unit tests for LiveMonitor and dynamic SPRT demotion (R-BOT-7, R-BOT-8)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from apps.core.live_monitor import (
    MANIFEST_EXPIRED,
    STRATEGY_DEMOTED_BY_SPRT,
    LiveMonitor,
)
from apps.core.manifest_catalog import (
    DynamicManifestCatalog,
    StrategyCatalogEntry,
    ValidatedStats,
)
from packages.observability.events import EventSink
from packages.sprt import Decision


class RecordingEventSink(EventSink):
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event_name: str, **kwargs: Any) -> None:
        self.events.append((event_name, kwargs))


def _make_strategy(
    key: str = "eurusd_f1",
    status: str = "approved",
    wilson_lower: str = "0.58",
    p_min: str = "0.46",
) -> StrategyCatalogEntry:
    return StrategyCatalogEntry(
        key=key,
        family="F1",
        display_name_pt=f"Estratégia {key}",
        asset="EURUSD",
        timeframe="M1",
        hours_utc=(0, 24),
        params={
            "adx_len": 14,
            "adx_max": "25.0",
            "bb_len": 20,
            "bb_k": "2.0",
            "rsi_len": 14,
            "rsi_lo": "30.0",
            "rsi_hi": "70.0",
        },
        validated=ValidatedStats(
            p_hat=Decimal("0.60"),
            wilson_lower=Decimal(wilson_lower),
            p_min_at_validation=Decimal(p_min),
            payout_min=Decimal("0.80"),
            ops_per_day=Decimal("15"),
            worst_streak=3,
            result_1000_ops_stake10=Decimal("1500"),
            score=Decimal("5.0"),
        ),
        status=status,
    )


def test_live_monitor_initialization_from_catalog() -> None:
    catalog = DynamicManifestCatalog()
    s1 = _make_strategy("s1", status="approved")
    manifest = {
        "manifest_version": 1,
        "strategies": (s1,),
    }
    catalog.apply_manifest(manifest)

    sink = RecordingEventSink()
    monitor = LiveMonitor(catalog, event_sink=sink)
    monitor.sync_from_catalog()

    assert "s1" in monitor.monitors
    sp = monitor.monitors["s1"]
    assert sp.p0 == Decimal("0.58")
    assert sp.p1 == Decimal("0.46")
    assert sp.decision == Decision.CONTINUE


def test_sprt_demotes_strategy_to_observation_and_blocks_real() -> None:
    clock_time = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    catalog = DynamicManifestCatalog(utc_clock=lambda: clock_time)
    s1 = _make_strategy("s1", status="approved", wilson_lower="0.58", p_min="0.46")
    catalog.apply_manifest({"manifest_version": 1, "strategies": (s1,)})

    sink = RecordingEventSink()
    monitor = LiveMonitor(catalog, event_sink=sink)
    monitor.sync_from_catalog()

    # Strategy is currently approved -> eligible in Real account
    ok_real, reason_real, _ = catalog.is_eligible(
        "s1", account_type="REAL", current_payout=Decimal("0.85"), now_utc=clock_time
    )
    assert ok_real is True
    assert reason_real == "ELIGIBLE"

    # Simulate consecutive losses causing SPRT to reject H0
    rejected = False
    for i in range(1, 50):
        dec = monitor.on_settlement("s1", won=False, ts=1756684800 + i, payout_pct="85.0")
        if dec == Decision.REJECT_H0:
            rejected = True
            break

    assert rejected is True

    # 1. Catalog status changed to observation
    info = catalog.get_strategy("s1")
    assert info is not None
    assert info.status == "observation"

    # 2. strategy_demoted event emitted
    demoted_events = [e for e in sink.events if e[0] == "strategy_demoted"]
    assert len(demoted_events) == 1
    ev_name, ev_payload = demoted_events[0]
    assert ev_payload["strategy_key"] == "s1"
    assert ev_payload["reason_code"] == STRATEGY_DEMOTED_BY_SPRT

    # 3. Strategy is now blocked in Real account!
    ok_real_after, reason_real_after, _ = catalog.is_eligible(
        "s1", account_type="REAL", current_payout=Decimal("0.85"), now_utc=clock_time
    )
    assert ok_real_after is False
    assert reason_real_after == "OBSERVATION_ONLY_DEMO"

    # But still eligible in Demo account
    ok_demo, reason_demo, _ = catalog.is_eligible(
        "s1", account_type="DEMO", current_payout=Decimal("0.85"), now_utc=clock_time
    )
    assert ok_demo is True
    assert reason_demo == "ELIGIBLE"


def test_manifest_expiration_demotes_all_to_observation() -> None:
    catalog = DynamicManifestCatalog()
    s1 = _make_strategy("s1", status="approved")
    s2 = _make_strategy("s2", status="approved")
    catalog.apply_manifest({"manifest_version": 1, "strategies": (s1, s2)})

    sink = RecordingEventSink()
    monitor = LiveMonitor(catalog, event_sink=sink)
    monitor.sync_from_catalog()

    assert catalog.active_strategies["s1"].status == "approved"
    assert catalog.active_strategies["s2"].status == "approved"

    # When manifest expires without replacement
    demoted_count = monitor.on_manifest_expired()
    assert demoted_count == 2
    assert catalog.active_strategies["s1"].status == "observation"
    assert catalog.active_strategies["s2"].status == "observation"

    exp_events = [e for e in sink.events if e[0] == "manifest_expired"]
    assert len(exp_events) == 1
    assert exp_events[0][1]["reason_code"] == MANIFEST_EXPIRED
    assert exp_events[0][1]["demoted_count"] == 2


def test_manifest_update_resets_monitor_only_when_validated_changes() -> None:
    catalog = DynamicManifestCatalog()
    s1 = _make_strategy("s1", status="approved", wilson_lower="0.58", p_min="0.46")
    catalog.apply_manifest({"manifest_version": 1, "strategies": (s1,)})

    monitor = LiveMonitor(catalog)
    monitor.sync_from_catalog()

    # Progress monitor on s1 with one win
    monitor.on_settlement("s1", won=True, ts=1756684800, payout_pct="85.0")
    assert monitor.monitors["s1"].n == 1
    assert monitor.monitors["s1"].wins == 1

    # Manifest v2: same validated stats -> monitor keeps state
    s1_same = _make_strategy("s1", status="approved", wilson_lower="0.58", p_min="0.46")
    catalog.apply_manifest({"manifest_version": 2, "strategies": (s1_same,)})
    monitor.on_manifest_applied(catalog)
    assert monitor.monitors["s1"].n == 1
    assert monitor.monitors["s1"].wins == 1

    # Manifest v3: changed validated stats -> monitor resets
    s1_changed = _make_strategy("s1", status="approved", wilson_lower="0.59", p_min="0.48")
    catalog.apply_manifest({"manifest_version": 3, "strategies": (s1_changed,)})
    monitor.on_manifest_applied(catalog)
    assert monitor.monitors["s1"].n == 0
    assert monitor.monitors["s1"].p0 == Decimal("0.59")
    assert monitor.monitors["s1"].p1 == Decimal("0.48")
