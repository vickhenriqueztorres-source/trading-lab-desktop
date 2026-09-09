"""Public replay-contract vectors for lab/bot equivalence (CAT-04).

The vectors produced here are an artifact, not a runtime integration path.  The
Strategy Lab uses its reference primitives to generate them; the desktop bot
loads the committed JSON and validates the same recipe independently, without
importing this module or any Strategy Lab package.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from manifest_schema.families import FAMILY_BINDINGS, FAMILY_COMPONENTS, FAMILY_GATES, Family
from primitives import Candle

from strategy_lab.research.candidate import Candidate, ParamValue
from strategy_lab.research.payout_lookup import PayoutLookup, PayoutPoint
from strategy_lab.research.replay_simulator import ReplaySignal, Trade, replay_candidate

CONTRACT_VERSION = "replay-v1"
PRIMITIVES_VERSION = "1.0.0"
BASE_TS = 1_700_000_100


@dataclass(frozen=True)
class ReplayCaseSpec:
    case_id: str
    family: Family
    asset: str
    tf: str
    hours: tuple[int, int]
    params: dict[str, str]
    candles: tuple[Candle, ...]
    payout_ratio: Decimal | None = Decimal("0.87")
    bot_comparable: bool = True
    compare_outputs: bool = True
    notes: str = ""


def build_replay_contract() -> dict[str, Any]:
    """Build deterministic public vectors covering the CAT-04 replay contract."""

    cases = [_case_payload(spec) for spec in _case_specs()]
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "primitives_version": PRIMITIVES_VERSION,
        "semantics": {
            "decision_time": "closed_candle_t",
            "settlement_time": "next_complete_timeframe_bucket",
            "settlement_gap_rule": "next_row_after_gap_is_not_t_plus_1",
            "broker_execution_note": (
                "Replay simulates close-to-close outcomes only; live broker entry "
                "price and expiration are separate execution evidence."
            ),
            "approval_rule": "vector_scan_never_approves_manifest_entries",
            "hash_rule": "sha256(canonical_json_without_sha256)",
        },
        "coverage": {
            "families": sorted({case["family"] for case in cases}),
            "assets": sorted({case["asset"] for case in cases}),
            "timeframes": sorted({case["tf"] for case in cases}),
            "edge_cases": [
                "composition_gate_boundary",
                "missing_tick_volume",
                "settlement_gap",
                "tie_is_loss",
                "outside_hours",
                "prefix_reinitialization",
            ],
        },
        "cases": cases,
    }
    payload["sha256"] = _stable_sha256(payload)
    return payload


def _case_payload(spec: ReplayCaseSpec) -> dict[str, Any]:
    candidate = _candidate_from_spec(spec)
    lookup = _payout_lookup(spec)
    log = replay_candidate(candidate, list(spec.candles), lookup, trace_signals=True)
    payload: dict[str, Any] = {
        "id": spec.case_id,
        "family": spec.family,
        "components": list(FAMILY_COMPONENTS[spec.family]),
        "asset": spec.asset,
        "tf": spec.tf,
        "tf_seconds": _timeframe_seconds(spec.tf),
        "hours_utc": list(spec.hours),
        "params": spec.params,
        "candidate_hash": candidate.hash(),
        "bot_comparable": spec.bot_comparable,
        "compare_outputs": spec.compare_outputs,
        "candles": [_candle_payload(candle) for candle in spec.candles],
        "expected_trace": [_signal_payload(signal) for signal in log.signal_trace],
        "expected_trades": [_trade_payload(trade) for trade in log.trades],
        "excluded_missing_payout": log.excluded_missing_payout,
        "excluded_settlement_gap": log.excluded_settlement_gap,
        "notes": spec.notes,
    }
    payload["sha256"] = _stable_sha256(payload)
    return payload


def _candidate_from_spec(spec: ReplayCaseSpec) -> Candidate:
    params: dict[str, dict[str, ParamValue]] = {}
    for wire_name, raw_value in spec.params.items():
        binding = FAMILY_BINDINGS[spec.family].get(wire_name)
        if binding is None:
            if wire_name in FAMILY_GATES[spec.family]:
                params.setdefault("_gates", {})[wire_name] = Decimal(raw_value)
            continue
        primitive_name, primitive_param = binding
        params.setdefault(primitive_name, {})[primitive_param] = _param_value(
            spec.family, wire_name, raw_value
        )
    regime, trigger, confirm = FAMILY_COMPONENTS[spec.family]
    return Candidate(
        family=spec.family,
        regime=regime,
        trigger=trigger,
        confirm=confirm,
        params=params,
        tf=spec.tf,
        hours=spec.hours,
        asset=spec.asset,
    )


def _param_value(family: Family, wire_name: str, raw_value: str) -> ParamValue:
    spec = FAMILY_GATES[family].get(wire_name) if wire_name in FAMILY_GATES[family] else None
    if spec is None:
        primitive_name, primitive_param = FAMILY_BINDINGS[family][wire_name]
        from primitives.registry import REGISTRY

        spec = REGISTRY[primitive_name].param_spec[primitive_param]
    return int(raw_value) if spec.kind == "int" else Decimal(raw_value)


def _payout_lookup(spec: ReplayCaseSpec) -> PayoutLookup:
    if spec.payout_ratio is None:
        return PayoutLookup(())
    points = {
        candle.ts - candle.ts % 3600: PayoutPoint(
            asset=spec.asset,
            hour_ts=candle.ts - candle.ts % 3600,
            payout_return_ratio=spec.payout_ratio,
            samples=1,
            observed_at=candle.ts - candle.ts % 3600,
        )
        for candle in spec.candles
    }
    return PayoutLookup(points.values())


def _case_specs() -> tuple[ReplayCaseSpec, ...]:
    return (
        ReplayCaseSpec(
            case_id="f1_eurusd_m1_gate_boundary",
            family="F1",
            asset="EURUSD",
            tf="M1",
            hours=(0, 24),
            params={
                "adx_len": "14",
                "adx_max": "100",
                "bb_len": "20",
                "bb_k": "2.0",
                "rsi_len": "14",
                "rsi_lo": "30",
                "rsi_hi": "70",
            },
            candles=_walk(BASE_TS, 60, 48, Decimal("1.1000"), "4"),
            notes="F1 covers ADX composition gate at the permissive boundary.",
        ),
        ReplayCaseSpec(
            case_id="f2_eurusdotc_m5",
            family="F2",
            asset="EURUSD-OTC",
            tf="M5",
            hours=(0, 24),
            params={
                "ema_short": "5",
                "ema_medium": "10",
                "ema_long": "20",
                "pullback_len": "20",
                "pullback_tolerance": "0.002",
                "body_max": "0.35",
                "wick_min": "0.5",
            },
            candles=_walk(_align(BASE_TS, 300), 300, 34, Decimal("1.0800"), "5"),
            notes="M5 vector proves candidate timeframe, not polling cadence, drives replay.",
        ),
        ReplayCaseSpec(
            case_id="f3_gbpusd_m15",
            family="F3",
            asset="GBPUSD",
            tf="M15",
            hours=(0, 24),
            params=_f3_params(),
            candles=_level_rejection(_align(BASE_TS, 900), 900, close_next=Decimal("99.40")),
            notes="M15 level-rejection vector with settlement on the next M15 bucket.",
        ),
        ReplayCaseSpec(
            case_id="f4_usdjpy_missing_volume",
            family="F4",
            asset="USDJPY",
            tf="M1",
            hours=(0, 24),
            params={
                "bb_len": "20",
                "bb_k": "2.0",
                "width_median_len": "20",
                "width_ratio_max": "1.0",
                "break_len": "20",
                "volume_len": "20",
                "volume_min": "1.5",
            },
            candles=_walk(BASE_TS, 60, 48, Decimal("145.000"), None),
            bot_comparable=True,
            compare_outputs=False,
            notes="Missing tick volume must block before execution without changing formulas.",
        ),
        ReplayCaseSpec(
            case_id="f5_eurjpy_outside_hours",
            family="F5",
            asset="EURJPY",
            tf="M1",
            hours=(0, 1),
            params={
                "quadrant_window": "3",
                "rsi_len": "14",
                "rsi_lo": "30",
                "rsi_hi": "70",
            },
            candles=_walk(BASE_TS, 60, 30, Decimal("162.000"), "6"),
            notes="Hours are part of the manifest recipe and block at candle close time.",
        ),
        ReplayCaseSpec(
            case_id="f3_tie_is_loss",
            family="F3",
            asset="AUDUSD-OTC",
            tf="M1",
            hours=(0, 24),
            params=_f3_params(),
            candles=_level_rejection(BASE_TS, 60, close_next=Decimal("99.20")),
            bot_comparable=True,
            notes="A close-to-close tie at t+1 is settled as a loss.",
        ),
        ReplayCaseSpec(
            case_id="f3_settlement_gap",
            family="F3",
            asset="USDCHF-OTC",
            tf="M1",
            hours=(0, 24),
            params=_f3_params(),
            candles=_level_rejection(BASE_TS, 120, close_next=Decimal("99.40")),
            bot_comparable=False,
            notes="The next row after a gap is not a valid t+1 settlement candle.",
        ),
    )


def _f3_params() -> dict[str, str]:
    return {
        "level_support": "99",
        "level_resistance": "101",
        "level_tolerance": "0.30",
        "body_max": "0.35",
        "wick_min": "0.5",
    }


def _walk(
    start_ts: int,
    step_s: int,
    count: int,
    start_price: Decimal,
    tick_vol: str | None,
) -> tuple[Candle, ...]:
    candles: list[Candle] = []
    price = start_price
    for index in range(count):
        delta = Decimal((index % 7) - 3) / Decimal("10000")
        close = price + delta
        high = max(price, close) + Decimal("0.0005")
        low = min(price, close) - Decimal("0.0005")
        candles.append(
            Candle(
                ts=start_ts + index * step_s,
                o=price,
                h=high,
                l=low,
                c=close,
                tick_vol=None if tick_vol is None else int(tick_vol) + index % 3,
            )
        )
        price = close
    return tuple(candles)


def _level_rejection(
    start_ts: int,
    step_s: int,
    *,
    close_next: Decimal,
) -> tuple[Candle, ...]:
    return (
        Candle(
            ts=start_ts,
            o=Decimal("99.15"),
            h=Decimal("99.25"),
            l=Decimal("98.95"),
            c=Decimal("99.20"),
            tick_vol=5,
        ),
        Candle(
            ts=start_ts + step_s,
            o=Decimal("99.20"),
            h=max(Decimal("99.20"), close_next) + Decimal("0.05"),
            l=min(Decimal("99.20"), close_next) - Decimal("0.05"),
            c=close_next,
            tick_vol=6,
        ),
    )


def _candle_payload(candle: Candle) -> dict[str, object]:
    return {
        "ts": candle.ts,
        "o": _decimal(candle.o),
        "h": _decimal(candle.h),
        "l": _decimal(candle.l),
        "c": _decimal(candle.c),
        "tick_vol": candle.tick_vol,
    }


def _signal_payload(signal: ReplaySignal) -> dict[str, object]:
    return {
        "ts": signal.ts,
        "stage": signal.stage,
        "direction": signal.direction,
        "regime_direction": signal.regime_direction,
        "trigger_direction": signal.trigger_direction,
        "confirm_direction": signal.confirm_direction,
        "regime_value": _decimal_or_none(signal.regime_value),
        "trigger_value": _decimal_or_none(signal.trigger_value),
        "confirm_value": _decimal_or_none(signal.confirm_value),
    }


def _trade_payload(trade: Trade) -> dict[str, object]:
    return {
        "ts": trade.ts,
        "asset": trade.asset,
        "direction": trade.direction,
        "won": trade.won,
        "payout_return_ratio": _decimal(trade.payout_return_ratio),
        "profit_ratio": _decimal(trade.profit_ratio),
        "update_count_at_signal": trade.update_count_at_signal,
    }


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else _decimal(value)


def _timeframe_seconds(timeframe: str) -> int:
    if timeframe == "M1":
        return 60
    if timeframe == "M5":
        return 300
    if timeframe == "M15":
        return 900
    raise ValueError("REPLAY_CONTRACT_TIMEFRAME_UNSUPPORTED")


def _align(ts: int, step_s: int) -> int:
    return ts - ts % step_s


def _stable_sha256(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "sha256"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
