from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from primitives import Candle, Category, Indicator, Output
from primitives.registry import REGISTRY
from strategy_lab.cli import main
from strategy_lab.research.candidate import Candidate
from strategy_lab.research.dataset import ResearchDataset, ResearchDatasetError
from strategy_lab.research.delay_penalty import apply_delay_penalty
from strategy_lab.research.outcome import settle
from strategy_lab.research.payout_lookup import PayoutLookup, PayoutPoint
from strategy_lab.research.replay_simulator import replay_candidate
from strategy_lab.research.synthetic import (
    BASE_TS,
    edge_series,
    random_walk,
    reverse_oracle_lookahead,
)
from strategy_lab.research.vector_scan import vector_scan_candidate


class AlwaysRegime(Indicator):
    category = Category.REGIME
    name = "always_regime"
    param_spec = {}

    @property
    def warmup_required(self) -> int:
        return 1

    def reset(self) -> None:
        return None

    def update(self, candle: Candle) -> Output:
        return Output(direction="none", value=Decimal("1"), meta={"ts": Decimal(candle.ts)})


class BodyTrigger(Indicator):
    category = Category.TRIGGER
    name = "body_trigger"
    param_spec = {}

    @property
    def warmup_required(self) -> int:
        return 1

    def reset(self) -> None:
        return None

    def update(self, candle: Candle) -> Output:
        direction = "call" if candle.c > candle.o else "put" if candle.c < candle.o else "none"
        return Output(direction=direction, value=candle.c - candle.o, meta={})


class BodyConfirm(BodyTrigger):
    category = Category.CONFIRM
    name = "body_confirm"


FAKE_REGISTRY: dict[str, type[Indicator]] = {
    "always_regime": AlwaysRegime,
    "body_trigger": BodyTrigger,
    "body_confirm": BodyConfirm,
}


def test_settle_tie_is_loss() -> None:
    """R-RES-4: empate no fechamento seguinte e direction none liquidam como perda."""
    current = Candle(
        ts=BASE_TS, o=Decimal("1"), h=Decimal("2"), l=Decimal("1"), c=Decimal("1.5"), tick_vol=1
    )
    following = Candle(
        ts=BASE_TS + 60,
        o=Decimal("1.5"),
        h=Decimal("2"),
        l=Decimal("1"),
        c=Decimal("1.5"),
        tick_vol=1,
    )

    assert not settle("call", current, following)
    assert not settle("put", current, following)
    assert not settle("none", current, following)


def test_payout_none_excludes_trade() -> None:
    """R-RES-4: hora com samples=0 exclui a operação sem inventar payout."""
    candles = edge_series(seed=7, length=20)
    lookup = PayoutLookup([PayoutPoint("EURUSD-OTC", BASE_TS - BASE_TS % 3600, None, 0)])
    log = replay_candidate(_body_candidate(), candles, lookup, registry=FAKE_REGISTRY)

    assert len(log.trades) == 0
    assert log.excluded_missing_payout > 0


def test_replay_never_sees_future() -> None:
    """R-RES-5/I-4: replay alimenta cada um dos 14 primitivos só com a vela corrente."""
    candles = random_walk(seed=11, length=260)
    lookup = _full_lookup()
    covered = set()
    for primitive_name, primitive_type in REGISTRY.items():
        registry = {
            "always_regime": AlwaysRegime,
            "body_trigger": BodyTrigger,
            "body_confirm": BodyConfirm,
            primitive_name: primitive_type,
        }
        candidate = Candidate(
            family="test",
            regime=primitive_name,
            trigger="body_trigger",
            confirm="body_confirm",
            asset="EURUSD-OTC",
        )
        log = replay_candidate(candidate, candles, lookup, registry=registry, trace_updates=True)
        covered.add(primitive_name)
        assert all(trace.candle_ts == trace.step_ts for trace in log.update_trace)
    assert covered == set(REGISTRY)


def test_vector_scan_matches_replay_on_signal_timestamps() -> None:
    """R-RES-5: triagem retorna os mesmos timestamps do replay para o candidato controlado."""
    candles = edge_series(seed=21, length=180)
    lookup = _full_lookup()
    replay = replay_candidate(_body_candidate(), candles, lookup, registry=FAKE_REGISTRY)
    scanned = vector_scan_candidate(_body_candidate(), candles, lookup, registry=FAKE_REGISTRY)
    replay_ts = {trade.ts for trade in replay.trades}
    scan_ts = set(scanned["ts"].to_list())
    intersection = replay_ts & scan_ts
    ratio = Decimal(len(intersection)) / Decimal(len(replay_ts))

    assert ratio >= Decimal("0.99")


def test_vector_scan_polars_path_matches_range_rejection_replay() -> None:
    """R-RES-5: caminho vetorizado Polars bate com replay incremental."""
    candles = [
        _fixed_candle(0, "100", "100.05", "99.95", "100.01"),
        _fixed_candle(1, "100.01", "100.06", "99.96", "100.02"),
        _fixed_candle(2, "100.02", "100.07", "99.97", "100.03"),
        _fixed_candle(3, "101.00", "101.06", "100.00", "101.05"),
        _fixed_candle(4, "101.05", "101.10", "101.02", "101.08"),
    ]
    candidate = Candidate(
        family="test",
        regime="session_window",
        trigger="range_break",
        confirm="candle_rejection",
        params={
            "session_window": {"start_minute": 0, "end_minute": 1440},
            "range_break": {"length": 3},
            "candle_rejection": {
                "max_body_ratio": Decimal("0.35"),
                "min_wick_ratio": Decimal("0.5"),
            },
        },
        asset="EURUSD-OTC",
    )
    replay = replay_candidate(candidate, candles, _full_lookup())
    scanned = vector_scan_candidate(candidate, candles, _full_lookup())

    assert scanned["ts"].to_list() == [trade.ts for trade in replay.trades]


def test_injected_edge_recovered() -> None:
    """R-RES-10: edge sintético p=0,60 é medido dentro da faixa esperada."""
    candles = edge_series(seed=31, length=800, win_probability_pct=60)
    log = replay_candidate(_body_candidate(), candles, _full_lookup(), registry=FAKE_REGISTRY)

    assert Decimal("0.58") <= log.p_hat <= Decimal("0.62")
    assert apply_delay_penalty(log.p_hat, Decimal("0.010")) == log.p_hat - Decimal("0.010")


def test_reverse_oracle_lookahead_detected() -> None:
    """R-RES-10/I-4: fixture ilegal com t+1 produz p_hat > 0,95 e seria detectada."""
    candles = random_walk(seed=41, length=240)

    assert reverse_oracle_lookahead(candles) > Decimal("0.95")


def test_dataset_coverage_refuses_low_or_unresolved_gaps() -> None:
    """R-RES-1: cobertura <95% ou gap in_session não resolvido aborta a pesquisa."""
    candles = [
        {
            "asset": "EURUSD-OTC",
            "ts": BASE_TS + index * 60,
            "o": "1",
            "h": "2",
            "l": "1",
            "c": "1.1",
            "tick_vol": 1,
        }
        for index in range(95)
    ]
    dataset = ResearchDataset.from_rows(candles, [], [])
    assert dataset.coverage("EURUSD-OTC", BASE_TS, BASE_TS + 99 * 60) == Decimal("0.95")

    dataset.refuse_if_coverage_below("EURUSD-OTC", BASE_TS, BASE_TS + 99 * 60)
    low = ResearchDataset.from_rows(candles[:-1], [], [])
    with pytest.raises(ResearchDatasetError, match="RES_COVERAGE_BELOW_MINIMUM"):
        low.refuse_if_coverage_below("EURUSD-OTC", BASE_TS, BASE_TS + 99 * 60)

    gap = {
        "asset": "EURUSD-OTC",
        "from_ts": BASE_TS,
        "to_ts": BASE_TS + 60,
        "in_session": True,
        "resolved": False,
    }
    blocked = ResearchDataset.from_rows(candles, [], [gap])
    with pytest.raises(ResearchDatasetError, match="RES_COVERAGE_BELOW_MINIMUM"):
        blocked.refuse_if_coverage_below("EURUSD-OTC", BASE_TS, BASE_TS + 99 * 60)


def test_payout_lookup_uses_hour_bucket_and_samples() -> None:
    """R-RES-4: payout usa hour_ts e retorna None quando samples=0."""
    lookup = PayoutLookup.from_rows(
        [
            {
                "asset": "EURUSD-OTC",
                "hour_ts": BASE_TS - BASE_TS % 3600,
                "payout_pct": "87.00",
                "samples": 3,
            },
            {
                "asset": "EURUSD-OTC",
                "hour_ts": BASE_TS - BASE_TS % 3600 + 3600,
                "payout_pct": "90.00",
                "samples": 0,
            },
        ]
    )

    assert lookup.payout("EURUSD-OTC", BASE_TS + 120) == Decimal("0.87")
    assert lookup.payout("EURUSD-OTC", BASE_TS - BASE_TS % 3600 + 3600) is None


def test_research_coverage_report_cli_with_parquet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R-RES-1: `research --coverage-report` imprime cobertura por asset e recusa <95%."""
    pytest.importorskip("polars")
    pl = pytest.importorskip("polars")
    candles = [
        {
            "asset": "EURUSD-OTC",
            "ts": BASE_TS + index * 60,
            "o": "1",
            "h": "2",
            "l": "1",
            "c": "1.1",
            "tick_vol": 1,
        }
        for index in range(100)
    ]
    candles_path = tmp_path / "candles.parquet"
    payouts_path = tmp_path / "payouts.parquet"
    pl.DataFrame(candles).write_parquet(candles_path)
    pl.DataFrame(
        [
            {
                "asset": "EURUSD-OTC",
                "hour_ts": BASE_TS - BASE_TS % 3600,
                "payout_pct": "87.00",
                "samples": 1,
            }
        ]
    ).write_parquet(payouts_path)

    code = main(
        [
            "research",
            "--coverage-report",
            "--assets",
            "EURUSD-OTC",
            "--from",
            str(BASE_TS),
            "--to",
            str(BASE_TS + 99 * 60),
            "--candles-parquet",
            str(candles_path),
            "--payouts-parquet",
            str(payouts_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["event"] == "strategy_lab_coverage_report"
    assert payload["assets"][0]["coverage"] == "0.95" or payload["assets"][0]["coverage"] == "1"


def test_research_coverage_report_cli_refuses_low_coverage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R-RES-1: `research --coverage-report` recusa asset com cobertura < 95%."""
    pytest.importorskip("polars")
    pl = pytest.importorskip("polars")
    candles = [
        {
            "asset": "EURUSD-OTC",
            "ts": BASE_TS + index * 60,
            "o": "1",
            "h": "2",
            "l": "1",
            "c": "1.1",
            "tick_vol": 1,
        }
        for index in range(50)
    ]
    candles_path = tmp_path / "candles_low.parquet"
    payouts_path = tmp_path / "payouts_low.parquet"
    pl.DataFrame(candles).write_parquet(candles_path)
    pl.DataFrame(
        [
            {
                "asset": "EURUSD-OTC",
                "hour_ts": BASE_TS - BASE_TS % 3600,
                "payout_pct": "87.00",
                "samples": 1,
            }
        ]
    ).write_parquet(payouts_path)

    code = main(
        [
            "research",
            "--coverage-report",
            "--assets",
            "EURUSD-OTC",
            "--from",
            str(BASE_TS),
            "--to",
            str(BASE_TS + 99 * 60),
            "--candles-parquet",
            str(candles_path),
            "--payouts-parquet",
            str(payouts_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["event"] == "strategy_lab_coverage_report"
    assert payload["assets"][0]["accepted"] is False
    assert Decimal(str(payload["assets"][0]["coverage"])) == Decimal("0.5")


def test_candidate_hash_stable() -> None:
    """R-RES-5: hash() estável do Candidate independe da ordem das chaves de parâmetros."""
    c1 = Candidate(
        family="f1",
        regime="session_window",
        trigger="range_break",
        confirm="candle_rejection",
        params={"range_break": {"length": 20, "step": 1}},
        asset="EURUSD-OTC",
    )
    c2 = Candidate(
        family="f1",
        regime="session_window",
        trigger="range_break",
        confirm="candle_rejection",
        params={"range_break": {"step": 1, "length": 20}},
        asset="EURUSD-OTC",
    )

    assert c1.hash() == c1.stable_hash()
    assert c1.hash() == c2.hash()
    assert len(c1.hash()) == 64
    assert hash(c1) == hash(c2)
    assert c1 in {c2}


def test_delay_penalty_deterministic_values() -> None:
    """R-RES-6: penalidade de atraso aplica -0,5 pp e -1,0 pp de forma determinística."""
    p_hat = Decimal("0.600")

    # Default -0.5 pp (0.005)
    assert apply_delay_penalty(p_hat) == Decimal("0.595")

    # Explicit -1.0 pp (0.010)
    assert apply_delay_penalty(p_hat, Decimal("0.010")) == Decimal("0.590")

    # Floor at zero
    assert apply_delay_penalty(Decimal("0.003"), Decimal("0.005")) == Decimal("0")


def _body_candidate() -> Candidate:
    return Candidate(
        family="test",
        regime="always_regime",
        trigger="body_trigger",
        confirm="body_confirm",
        asset="EURUSD-OTC",
    )


def _full_lookup() -> PayoutLookup:
    return PayoutLookup(
        [
            PayoutPoint(
                "EURUSD-OTC",
                BASE_TS - BASE_TS % 3600 + offset * 3600,
                Decimal("0.87"),
                1,
            )
            for offset in range(20)
        ]
    )


def _fixed_candle(index: int, open_: str, high: str, low: str, close: str) -> Candle:
    return Candle(
        ts=BASE_TS + index * 60,
        o=Decimal(open_),
        h=Decimal(high),
        l=Decimal(low),
        c=Decimal(close),
        tick_vol=100,
    )
