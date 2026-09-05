"""Unit tests for DynamicManifestCatalog (R-BOT-5, R-BOT-6, R-BOT-8, R-BOT-9, R-BOT-13)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from apps.core.manifest_catalog import (
    DynamicManifestCatalog,
    StrategyCatalogEntry,
    ValidatedStats,
)


def _make_strategy(
    key: str,
    family: str,
    status: str = "approved",
    hours_utc: tuple[int, int] = (0, 6),
    payout_min: str = "0.85",
    wilson_lower: str = "0.565",
    warmup_required: int | None = None,
) -> StrategyCatalogEntry:
    return StrategyCatalogEntry(
        key=key,
        family=family,
        display_name_pt=f"Estratégia {key}",
        asset="EURUSD",
        timeframe="M1",
        hours_utc=hours_utc,
        params={
            "adx_len": 14,
            "adx_max": "25.0",
            "bb_len": 20,
            "bb_k": "2.0",
            "rsi_len": 14,
            "rsi_lo": "30.0",
            "rsi_hi": "70.0",
            "ema_short": 5,
            "ema_medium": 10,
            "ema_long": 20,
            "pullback_len": 20,
            "pullback_tolerance": "0.002",
            "body_max": "0.35",
            "wick_min": "0.5",
            "level_support": "99.0",
            "level_resistance": "101.0",
            "level_tolerance": "0.1",
        },
        validated=ValidatedStats(
            p_hat=Decimal("0.58"),
            wilson_lower=Decimal(wilson_lower),
            p_min_at_validation=Decimal("0.54"),
            payout_min=Decimal(payout_min),
            ops_per_day=Decimal("15"),
            worst_streak=4,
            result_1000_ops_stake10=Decimal("1200"),
            score=Decimal("4.5"),
        ),
        status=status,
        warmup_required=warmup_required,
    )


def _make_manifest(version: int, strategies: list[StrategyCatalogEntry]) -> dict[str, object]:
    return {
        "manifest_version": version,
        "schema_version": 1,
        "primitives_version": "1.0.0",
        "primitives_parity_sha256": (
            "sha256:f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10"
        ),
        "published_at": int(datetime(2026, 9, 1, tzinfo=UTC).timestamp()),
        "expires_at": int(datetime(2026, 10, 1, tzinfo=UTC).timestamp()),
        "strategies": tuple(strategies),
        "signature": "test_sig",
        "key_id": "A",
    }


def test_manifest_applied_builds_instances() -> None:
    clock_time = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    catalog = DynamicManifestCatalog(utc_clock=lambda: clock_time)

    s1 = _make_strategy("s1", "F1", status="approved")
    s2 = _make_strategy("s2", "F2", status="observation")
    manifest = _make_manifest(1, [s1, s2])

    catalog.apply_manifest(manifest)

    assert catalog.manifest_version == 1
    assert "s1" in catalog.active_strategies
    assert "s2" in catalog.active_strategies
    assert catalog.active_strategies["s1"].status == "approved"
    assert catalog.active_strategies["s2"].status == "observation"
    assert catalog.active_strategies["s1"].instance.family_name == "F1"
    assert catalog.active_strategies["s2"].instance.family_name == "F2"


def test_manifest_new_strategy_appears_without_restart() -> None:
    catalog = DynamicManifestCatalog()

    s1 = _make_strategy("s1", "F1")
    manifest_v1 = _make_manifest(1, [s1])
    catalog.apply_manifest(manifest_v1)
    assert len(catalog.active_strategies) == 1
    assert "s1" in catalog.active_strategies

    # New manifest with unprecedented strategy s_new
    s_new = _make_strategy("s_new", "F3")
    manifest_v2 = _make_manifest(2, [s1, s_new])
    catalog.apply_manifest(manifest_v2)

    assert catalog.manifest_version == 2
    assert len(catalog.active_strategies) == 2
    assert "s_new" in catalog.active_strategies
    assert catalog.active_strategies["s_new"].instance.family_name == "F3"


def test_observation_strategy_only_eligible_in_demo() -> None:
    clock_time = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    catalog = DynamicManifestCatalog(utc_clock=lambda: clock_time)

    s_obs = _make_strategy("s_obs", "F1", status="observation")
    s_app = _make_strategy("s_app", "F1", status="approved")
    manifest = _make_manifest(1, [s_obs, s_app])
    catalog.apply_manifest(manifest)

    # Observation strategy on Demo -> eligible
    ok_demo, reason_demo, _ = catalog.is_eligible(
        "s_obs",
        account_type="DEMO",
        current_payout=Decimal("0.85"),
        now_utc=clock_time,
    )
    assert ok_demo is True
    assert reason_demo == "ELIGIBLE"

    # Observation strategy on Real -> blocked!
    ok_real, reason_real, _ = catalog.is_eligible(
        "s_obs",
        account_type="REAL",
        current_payout=Decimal("0.85"),
        now_utc=clock_time,
    )
    assert ok_real is False
    assert reason_real == "OBSERVATION_ONLY_DEMO"

    # Approved strategy on Real -> eligible!
    ok_app, reason_app, _ = catalog.is_eligible(
        "s_app",
        account_type="REAL",
        current_payout=Decimal("0.85"),
        now_utc=clock_time,
    )
    assert ok_app is True
    assert reason_app == "ELIGIBLE"


def test_retiring_lifecycle_with_in_flight_orders() -> None:
    catalog = DynamicManifestCatalog()

    s1 = _make_strategy("s1", "F1")
    s2 = _make_strategy("s2", "F2")
    manifest_v1 = _make_manifest(1, [s1, s2])
    catalog.apply_manifest(manifest_v1)

    # Open order for s2
    catalog.notify_order_opened("s2", "ord-123")

    # Manifest v2 removes s2
    manifest_v2 = _make_manifest(2, [s1])
    catalog.apply_manifest(manifest_v2)

    # s2 is removed from active and moved to retiring (R-BOT-9)
    assert "s2" not in catalog.active_strategies
    assert "s2" in catalog.retiring_strategies
    assert catalog.retiring_strategies["s2"].status == "retiring"

    # New orders for s2 are blocked while retiring
    ok, reason, _ = catalog.is_eligible(
        "s2",
        account_type="DEMO",
        current_payout=Decimal("0.85"),
    )
    assert ok is False
    assert reason == "STRATEGY_RETIRING"

    # S2 order settles
    catalog.notify_order_settled("s2", "ord-123")

    # s2 is now fully discarded
    assert "s2" not in catalog.retiring_strategies
    assert catalog.get_strategy("s2") is None


def test_retiring_immediate_discard_without_in_flight_orders() -> None:
    catalog = DynamicManifestCatalog()

    s1 = _make_strategy("s1", "F1")
    s2 = _make_strategy("s2", "F2")
    catalog.apply_manifest(_make_manifest(1, [s1, s2]))

    # Manifest v2 removes s2 without any order in flight
    catalog.apply_manifest(_make_manifest(2, [s1]))

    assert "s2" not in catalog.active_strategies
    assert "s2" not in catalog.retiring_strategies


def test_hours_utc_filter_and_dst() -> None:
    catalog = DynamicManifestCatalog()
    s1 = _make_strategy("s1", "F1", hours_utc=(0, 6))
    s2 = _make_strategy("s2", "F1", hours_utc=(22, 4))
    # Isolate the hour/DST rule across months; expiry has dedicated tests.
    catalog.apply_manifest({"manifest_version": 1, "strategies": [s1, s2]})

    # 03:30 UTC -> inside (0, 6) and inside (22, 4)
    t_inside = datetime(2026, 9, 1, 3, 30, tzinfo=UTC)
    ok1, _, _ = catalog.is_eligible(
        "s1", account_type="DEMO", current_payout=Decimal("0.85"), now_utc=t_inside
    )
    ok2, _, _ = catalog.is_eligible(
        "s2", account_type="DEMO", current_payout=Decimal("0.85"), now_utc=t_inside
    )
    assert ok1 is True
    assert ok2 is True

    # 10:00 UTC -> outside both
    t_outside = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    ok1, r1, _ = catalog.is_eligible(
        "s1", account_type="DEMO", current_payout=Decimal("0.85"), now_utc=t_outside
    )
    ok2, r2, _ = catalog.is_eligible(
        "s2", account_type="DEMO", current_payout=Decimal("0.85"), now_utc=t_outside
    )
    assert ok1 is False
    assert r1 == "OUTSIDE_TRADING_HOURS"
    assert ok2 is False
    assert r2 == "OUTSIDE_TRADING_HOURS"

    # 23:15 UTC -> outside (0, 6), inside (22, 4)
    t_night = datetime(2026, 9, 1, 23, 15, tzinfo=UTC)
    ok1, _, _ = catalog.is_eligible(
        "s1", account_type="DEMO", current_payout=Decimal("0.85"), now_utc=t_night
    )
    ok2, _, _ = catalog.is_eligible(
        "s2", account_type="DEMO", current_payout=Decimal("0.85"), now_utc=t_night
    )
    assert ok1 is False
    assert ok2 is True

    # DST test: evaluate timestamps in summer vs winter local time
    # Because evaluation uses UTC exclusively, 04:00 UTC evaluates identically
    dst_summer = datetime(2026, 7, 15, 4, 0, tzinfo=UTC)
    dst_winter = datetime(2026, 12, 15, 4, 0, tzinfo=UTC)
    res_summer, _, _ = catalog.is_eligible(
        "s1", account_type="DEMO", current_payout=Decimal("0.85"), now_utc=dst_summer
    )
    res_winter, _, _ = catalog.is_eligible(
        "s1", account_type="DEMO", current_payout=Decimal("0.85"), now_utc=dst_winter
    )
    assert res_summer is True
    assert res_winter is True


def test_payout_gate_blocks_within_single_order_check() -> None:
    now = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    catalog = DynamicManifestCatalog(utc_clock=lambda: now)

    s1 = _make_strategy("s1", "F1", payout_min="0.85", wilson_lower="0.565")
    catalog.apply_manifest(_make_manifest(1, [s1]))

    # Payout 86% -> Eligible
    ok, reason, res = catalog.is_eligible(
        "s1", account_type="DEMO", current_payout=Decimal("0.86"), now_utc=now
    )
    assert ok is True
    assert reason == "ELIGIBLE"

    # Payout drops to 80% -> Blocked in <= 1 order
    ok, reason, res = catalog.is_eligible(
        "s1", account_type="DEMO", current_payout=Decimal("0.80"), now_utc=now
    )
    assert ok is False
    assert reason == "PAYOUT_BELOW_VALIDATED_EDGE"
    assert res is not None
    assert res.message == "Opera com payout ≥ 85%. Agora: 80% — aguardando."


def test_rejected_strategy_ignored() -> None:
    catalog = DynamicManifestCatalog()
    s_rej = _make_strategy("s_rej", "F1", status="rejected")
    manifest = _make_manifest(1, [s_rej])
    catalog.apply_manifest(manifest)

    assert "s_rej" not in catalog.active_strategies
    assert catalog.get_strategy("s_rej") is None


def test_manifest_warmup_mismatch_rejects_entry_only() -> None:
    catalog = DynamicManifestCatalog()
    mismatched = _make_strategy("bad_warmup", "F1", warmup_required=27)
    valid = _make_strategy("good_warmup", "F2", warmup_required=20)

    catalog.apply_manifest(_make_manifest(1, [mismatched, valid]))

    assert "bad_warmup" not in catalog.active_strategies
    assert "good_warmup" in catalog.active_strategies
    assert catalog.events == (
        (
            "WARMUP_MISMATCH",
            {"strategy_key": "bad_warmup", "declared": 27, "calculated": 28},
        ),
    )
