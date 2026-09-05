from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.core.families.base import EvalResult
from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_candidates import CandidateSignal, arbitrate, resolve_candidates
from apps.core.iqoption_connection_safety import IQOptionMessageBudget
from apps.core.iqoption_risk_config import IqOptionRiskConfig, IqOptionRiskConfigStore
from apps.core.manifest_catalog import DynamicManifestCatalog
from packages.domain.models import Direction
from packages.protocol.ui_messages import UiIqOptionAssetRank, UiIqOptionRiskConfig
from tests.unit.test_iqoption_auto_trader import FakeRuntime, _falling_prices, _make_candles

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


def entry(key: str = "f5:a", **fields: object) -> dict[str, object]:
    return {
        "key": key,
        "family": "F5",
        "asset": "EURUSD-OTC",
        "timeframe": "M1",
        "status": "approved",
        "params": {},
        "hours_utc": [0, 24],
        "display_name_pt": key,
        "validated": {"wilson_lower": "0.60", "p_min_at_validation": "0.55"},
        **fields,
    }


def catalog(*entries: dict[str, object]) -> DynamicManifestCatalog:
    result = DynamicManifestCatalog(utc_clock=lambda: NOW)
    result.apply_manifest({"manifest_version": 1, "strategies": list(entries)})
    return result


def resolve(cat: DynamicManifestCatalog | None, **fields):
    arguments = dict(
        catalog=cat,
        symbol="EURUSD-OTC",
        mode="SINGLE",
        active_strategy_key="f5:a",
        account_type="PRACTICE",
        now_utc=NOW,
    )
    arguments.update(fields)
    return resolve_candidates(**arguments)


def test_single_only_selected_exact_asset():
    cat = catalog(entry(), entry("f5:b", asset="GBPJPY"))
    choices, rejected = resolve(cat, symbol="GBPJPY")
    assert choices == [] and rejected == {"f5:a": "ASSET_MISMATCH"}
    assert [c.key for c in resolve(cat)[0]] == ["f5:a"]


@pytest.mark.parametrize("asset,symbol", [("EURUSD", "EURUSD-OTC"), ("EURUSD-OTC", "EURUSD")])
def test_otc_and_spot_never_cross(asset, symbol):
    assert resolve(catalog(entry(asset=asset)), mode="AUTO", symbol=symbol) == (
        [],
        {"f5:a": "ASSET_MISMATCH"},
    )


@pytest.mark.parametrize(
    "account,allowed", [("PRACTICE", True), ("DEMO", True), ("REAL", False), ("UNKNOWN", False)]
)
def test_observation_only_confirmed_demo(account, allowed):
    choices, _ = resolve(catalog(entry(status="observation")), account_type=account)
    assert bool(choices) == allowed


def test_no_implicit_rsi_or_first_entry_fallback():
    assert resolve(catalog(entry()), active_strategy_key="missing")[0] == []
    assert resolve(None, mode="AUTO", active_strategy_key="iqoption-rsi-demo")[0] == []
    assert resolve(None, active_strategy_key="missing")[0] == []


def test_manifest_context_change_rebuilds_isolated_instance():
    cat = catalog(entry())
    original = cat.active_strategies["f5:a"].instance
    cat.apply_manifest(
        {
            "manifest_version": 2,
            "strategies": [entry(asset="EURUSD", timeframe="M5")],
        }
    )
    assert cat.active_strategies["f5:a"].instance is not original
    assert resolve(cat)[0] == []
    assert resolve(cat, symbol="EURUSD")[0][0].timeframe_seconds == 300


def test_local_rsi_explicit_practice_single_and_never_real():
    choices, _ = resolve(None, active_strategy_key="iqoption-rsi-demo")
    assert len(choices) == 1 and choices[0].entry.status == "demo_only"
    assert resolve(None, active_strategy_key="iqoption-rsi-demo", account_type="REAL")[0] == []
    cat = catalog(entry(status="demo_only"))
    assert resolve(cat, mode="AUTO")[0] == []


def test_arbiter_deterministic_edge_then_key_and_opposite_cancel():
    cat = catalog(entry(), entry("f5:b"), entry("f5:c", timeframe="M5"))
    choices, _ = resolve(cat, mode="AUTO")
    signals = [CandidateSignal(c, Direction.CALL, Decimal(20), 100) for c in choices]
    for ordering in permutations(signals):
        assert arbitrate(list(ordering)).candidate.key == "f5:a"
    assert arbitrate([signals[0], replace(signals[1], direction=Direction.PUT)]) is None
    better = replace(signals[2].candidate.entry.validated, wilson_lower=Decimal("0.70"))
    best = replace(
        signals[2],
        candidate=replace(
            signals[2].candidate, entry=replace(signals[2].candidate.entry, validated=better)
        ),
    )
    assert arbitrate([signals[0], best]) == best


class HistoryClient:
    def iqoption_binary_payout(self, symbol):
        return Decimal("0.85")

    def __init__(self):
        self.requests = []

    def market_history(self, symbol, *, style, count, timeframe_seconds):
        self.requests.append((symbol, timeframe_seconds, count))
        candles = _make_candles(_falling_prices(), symbol=symbol)
        return [], [
            replace(
                c,
                timeframe_seconds=timeframe_seconds,
                open_time=NOW - timedelta(seconds=(20 - i) * timeframe_seconds),
                close_time=NOW - timedelta(seconds=(19 - i) * timeframe_seconds),
            )
            for i, c in enumerate(candles)
        ]


def trader_for(cat, config, *, armed=False, account="PRACTICE", budget=None):
    client, runtime = HistoryClient(), FakeRuntime()
    clock = [NOW]
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,
        risk_config_provider=lambda: config,
        operator_armed=lambda: armed,
        catalog_provider=lambda: cat,
        account_type_provider=lambda: account,
        utc_clock=lambda: clock[0],
        monotonic=lambda: (clock[0] - NOW).total_seconds(),
        message_budget=budget,
        monitor_provider=lambda: SimpleNamespace(ready=True),
    )
    return trader, client, runtime, clock


def test_manifest_timeframe_wins_and_override_event_once():
    cat = catalog(entry(timeframe="M5"))
    trader, client, runtime, _ = trader_for(cat, IqOptionRiskConfig(strategy_id="f5:a"))
    trader._evaluate_cycle()
    trader._evaluate_cycle()
    assert client.requests == [("EURUSD-OTC", 300, 18)]
    assert sum(n == "TIMEFRAME_OVERRIDDEN_BY_MANIFEST" for n, _ in runtime.events) == 1


def test_auto_two_timeframes_share_window_and_budget_per_pair():
    cat = catalog(entry(), entry("f5:b", timeframe="M5"), entry("f5:c", timeframe="M5"))
    trader, client, _, clock = trader_for(cat, IqOptionRiskConfig(symbol="AUTO"))
    trader._evaluate_cycle()
    trader._scan_cursor = 0
    trader._evaluate_cycle()
    assert client.requests == [("EURUSD-OTC", 60, 18), ("EURUSD-OTC", 300, 18)]
    clock[0] += timedelta(minutes=1)
    trader._scan_cursor = 0
    trader._evaluate_cycle()
    assert len(client.requests) == 3
    assert client.requests[-1][1] == 60


def test_budget_exhaustion_is_fail_closed_for_second_pair():
    cat = catalog(entry(), entry("f5:b", timeframe="M5"))
    trader, client, runtime, _ = trader_for(
        cat,
        IqOptionRiskConfig(symbol="AUTO"),
        armed=True,
        budget=IQOptionMessageBudget(limit=1, pressure_at=1),
    )
    trader._evaluate_cycle()
    assert len(client.requests) == 1 and runtime.requests == []
    assert trader.status_reason == "IQOPTION_MESSAGE_BUDGET_EXHAUSTED"


def test_real_engine_never_fetches_or_submits():
    trader, client, runtime, _ = trader_for(
        catalog(entry()), IqOptionRiskConfig(strategy_id="f5:a"), account="REAL", armed=True
    )
    trader._evaluate_cycle()
    assert client.requests == runtime.requests == []


def test_alias_load_preserves_key_and_financial_limits(tmp_path: Path):
    store = IqOptionRiskConfigStore(tmp_path)
    config = IqOptionRiskConfig(strategy_id="f5:a", stake_minor_units=200)
    legacy = UiIqOptionRiskConfig(strategy_id="f5:a", stake_minor_units=200).to_payload()
    (tmp_path / "iqoption-risk-config.json").write_text(json.dumps(legacy))
    assert store.load() == config
    store.save(config)
    assert "active_strategy_key" in json.loads((tmp_path / "iqoption-risk-config.json").read_text())
    assert store.load() == config
    legacy["active_strategy_key"] = legacy.pop("strategy_id")
    assert UiIqOptionRiskConfig.from_payload(legacy).active_strategy_key == "f5:a"


def test_decision_deduplicated_and_radar_details_roundtrip():
    trader, _, runtime, _ = trader_for(
        catalog(entry(asset="EURUSD")), IqOptionRiskConfig(strategy_id="f5:a")
    )
    trader._evaluate_cycle()
    trader._evaluate_cycle()
    events = [f for n, f in runtime.events if n == "iqoption_decision"]
    assert len(events) == 2  # one rejection + one NO_CANDIDATE symbol summary
    rank = trader.asset_ranking[0]
    assert "ASSET_MISMATCH" in rank.candidate_details
    assert UiIqOptionAssetRank.from_payload(rank.to_payload()) == rank


def test_auto_arbitrates_all_signals_not_catalog_insertion_order():
    cat = catalog(entry("f5:b"), entry("f5:a"))
    for info in cat.active_strategies.values():
        info.instance.evaluate_detailed = lambda candles, ctx: EvalResult(
            Direction.CALL, "OK", 20, 15, None, None, None
        )
    trader, _, runtime, _ = trader_for(cat, IqOptionRiskConfig(symbol="AUTO"), armed=True)
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    assert runtime.requests[0].strategy_id == "f5:a"
    trader._scan_cursor = 0
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
