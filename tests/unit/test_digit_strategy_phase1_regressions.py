from __future__ import annotations

import hashlib
import json
import tracemalloc
from collections.abc import Sequence
from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from apps.core.deriv_telemetry import DerivTelemetrySource
from apps.core.digit_risk_config import DigitRiskConfig
from packages.domain.market import MarketTick
from packages.strategies.deriv_digits import (
    DerivDigitEnginePool,
    DerivDigitShadowEngine,
    DerivDigitStrategyId,
    DigitStrategyDecision,
    ParityRegimeEdgeStrategy,
    SelectiveDiffersEdgeStrategy,
    ShadowSignalState,
    TailProbabilityEdgeStrategy,
    _wilson_bound,
    default_digit_strategy_registry,
)
from tests.unit.test_deriv_auto_trader import _Runtime, _telemetry
from tests.unit.test_deriv_digit_strategies import _ticks


def _spread_values(
    digits: list[int],
    *,
    start: int,
    end: int,
    count: int,
    value: int,
) -> None:
    positions = list(range(start, end + 1))
    for index in range(count):
        digits[positions[(index * len(positions)) // count]] = value


def _tail_boundary_digits(*, direction: str, above: bool) -> list[int]:
    success_digit = 8 if direction == "OVER" else 0
    failure_digit = 0 if direction == "OVER" else 8
    if above:
        digits = [1] * 55 + [failure_digit] * 445
        segments = ((56, 150, 52), (151, 300, 84), (301, 499, 122))
    else:
        digits = [1] * 35 + [failure_digit] * 465
        segments = ((36, 150, 63), (151, 300, 84), (301, 499, 122))
    for start, end, count in segments:
        _spread_values(digits, start=start, end=end, count=count, value=success_digit)
    return digits


def _differs_above_tail200_digits() -> list[int]:
    parities = [0] * 200
    for position in range(5, 5 + 32 * 5, 5):
        parities[position] = 1
    digits = [2 if parity == 0 else 1 for parity in parities]
    odd_values = [1] * 7 + [3] * 7 + [5] * 6 + [7] * 6 + [9] * 6
    even_values = [0] * 4 + [2] * 33 + [4] * 33 + [6] * 33 + [8] * 32
    odd_index = 0
    even_index = 0
    for index in range(1, 200):
        if parities[index - 1] != 0:
            continue
        if parities[index] == 1:
            digits[index] = odd_values[odd_index]
            odd_index += 1
        else:
            digits[index] = even_values[even_index]
            even_index += 1
    assert odd_index == len(odd_values)
    assert even_index == len(even_values)
    return digits


def _differs_above_digits() -> list[int]:
    return [1] * 300 + _differs_above_tail200_digits()


def _differs_below_digits() -> list[int]:
    segment = [1] * 151
    position = 0
    for _ in range(8):
        for offset in range(7):
            segment[position + offset] = 2
        segment[position + 7] = 1
        position += 8
    segment[150] = 2
    even_values = [0] * 3 + [2] * 12 + [4] * 11 + [6] * 11 + [8] * 11
    odd_values = [1] + [3] + [5] * 2 + [7] * 2 + [9] * 2
    even_index = 0
    odd_index = 0
    for index in range(1, 151):
        if segment[index - 1] % 2 != 0:
            continue
        if segment[index] % 2 == 0:
            segment[index] = even_values[even_index]
            even_index += 1
        else:
            segment[index] = odd_values[odd_index]
            odd_index += 1
    assert even_index == 48
    assert odd_index == 8
    return [1] * 150 + segment[:-1] + _differs_above_tail200_digits()


def _differs_disagree_digits() -> list[int]:
    segment = [1] * 151
    position = 0
    for run_length in [11] * 5 + [10] * 2:
        for offset in range(run_length):
            segment[position + offset] = 2
        segment[position + run_length] = 1
        position += run_length + 1
    segment[150] = 2
    even_values = [0] * 5 + [2] * 28 + [4] * 12 + [6] * 12 + [8] * 11
    odd_values = [3] + [5] * 2 + [7] * 2 + [9] * 2
    even_index = 0
    odd_index = 0
    for index in range(1, 151):
        if segment[index - 1] % 2 != 0:
            continue
        if segment[index] % 2 == 0:
            segment[index] = even_values[even_index]
            even_index += 1
        else:
            segment[index] = odd_values[odd_index]
            odd_index += 1
    assert even_index == 68
    assert odd_index == 7
    return [1] * 150 + segment[:-1] + _differs_above_tail200_digits()


def _differs_realistic_probability_digits() -> list[int]:
    segment = [1] * 200
    position = 0
    for _ in range(50):
        segment[position] = 2
        segment[position + 1] = 2
        segment[position + 2] = 1
        position += 3
    segment[-1] = 2
    even_values = [0] * 10 + [2] * 10 + [4] * 10 + [6] * 10 + [8] * 10
    odd_values = [1] * 10 + [3] * 10 + [5] * 10 + [7] * 10 + [9] * 10
    even_index = 0
    odd_index = 0
    for index in range(1, 200):
        if segment[index - 1] % 2 != 0:
            continue
        if segment[index] % 2 == 0:
            segment[index] = even_values[even_index]
            even_index += 1
        else:
            segment[index] = odd_values[odd_index]
            odd_index += 1
    assert even_index == 50
    assert odd_index == 50
    return [1] * 300 + segment


def _tail_realistic_probability_digits() -> list[int]:
    digits = [0] * 500
    for start, end, count in ((1, 150, 105), (151, 300, 105), (301, 499, 139)):
        _spread_values(digits, start=start, end=end, count=count, value=8)
    return digits


def _parity_segment(length: int, run_lengths: Sequence[int]) -> list[int]:
    segment = [1] * length
    position = 0
    for run_length in run_lengths:
        for offset in range(run_length):
            segment[position + offset] = 2
        if position + run_length < length:
            segment[position + run_length] = 1
        position += run_length + 1
    segment[-1] = 2
    return segment


def _parity_even_above_digits() -> list[int]:
    segment = [1] * 151
    position = 0
    for offset in range(4):
        segment[position + offset] = 2
    segment[position + 4] = 1
    position = 5
    for _ in range(57):
        segment[position] = 2
        segment[position + 1] = 1
        position += 2
    segment[150] = 2
    return [1] * 150 + segment[:-1] + _differs_above_tail200_digits()


def _parity_even_below_digits() -> list[int]:
    middle = _parity_segment(151, [3] * 12 + [2] * 23)
    tail = _parity_segment(200, [3] * 30 + [2] * 5)
    return [1] * 150 + middle[:-1] + tail


def _invert_parity(digits: Sequence[int]) -> list[int]:
    return [digit + 1 if digit % 2 == 0 else digit - 1 for digit in digits]


def _conditional_outcomes_from_digits(
    digits: Sequence[int],
    *,
    window_size: int,
) -> tuple[int, ...]:
    window = digits[-window_size:]
    context_parity = window[-1] % 2
    return tuple(
        window[index] for index in range(1, len(window)) if window[index - 1] % 2 == context_parity
    )


def _format_decimal(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.000001'))}"


def _tail_stats(
    digits: Sequence[int],
    *,
    direction: str,
    barrier: int,
    required: Decimal,
    window_size: int = 500,
) -> tuple[int, int, Decimal, Decimal]:
    outcomes = _conditional_outcomes_from_digits(digits, window_size=window_size)
    successes = sum(
        digit > barrier if direction == "OVER" else digit < barrier for digit in outcomes
    )
    wilson = _wilson_bound(successes, len(outcomes), upper=False) * Decimal(100)
    return len(outcomes), successes, wilson, wilson - required


def _differs_stats(
    digits: Sequence[int],
    *,
    barrier: int,
    required: Decimal = Decimal("92.25"),
    window_size: int = 500,
) -> tuple[int, int, Decimal, Decimal]:
    outcomes = _conditional_outcomes_from_digits(digits, window_size=window_size)
    losing_count = outcomes.count(barrier)
    wilson = (Decimal(1) - _wilson_bound(losing_count, len(outcomes), upper=True)) * Decimal(100)
    return len(outcomes), losing_count, wilson, wilson - required


def _parity_stats(
    digits: Sequence[int],
    *,
    direction: str,
    required: Decimal = Decimal("52.00"),
    window_size: int = 500,
) -> tuple[int, int, Decimal, Decimal]:
    outcomes = _conditional_outcomes_from_digits(digits, window_size=window_size)
    successes = sum(digit % 2 == 0 if direction == "EVEN" else digit % 2 == 1 for digit in outcomes)
    wilson = _wilson_bound(successes, len(outcomes), upper=False) * Decimal(100)
    return len(outcomes), successes, wilson, wilson - required


def test_existing_three_strategies_behaviour_is_bit_identical() -> None:
    series = (
        ("warmup-499", [digit for _ in range(249) for digit in (9, 0)] + [9]),
        ("tail-alternating-500", [digit for _ in range(250) for digit in (9, 0)]),
        ("parity-alternating-500", [digit for _ in range(250) for digit in (1, 2)]),
        ("uniform-cycle-500", [digit for _ in range(50) for digit in range(10)]),
    )
    direct_factories = (
        TailProbabilityEdgeStrategy,
        SelectiveDiffersEdgeStrategy,
        ParityRegimeEdgeStrategy,
    )
    catalog_factories = {
        item.manifest.strategy_id: item.factory
        for item in default_digit_strategy_registry().registrations
    }
    comparisons = 0
    for series_name, digits in series:
        ticks = _ticks(digits)
        for direct_factory in direct_factories:
            expected = direct_factory().evaluate(ticks)
            actual_object = catalog_factories[str(expected.strategy_id)]().evaluate(ticks)
            assert isinstance(actual_object, DigitStrategyDecision)
            actual = actual_object
            expected_bytes = json.dumps(
                asdict(expected), default=str, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            actual_bytes = json.dumps(
                asdict(actual), default=str, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            assert actual == expected
            assert actual_bytes == expected_bytes
            comparisons += 1
            print(
                "equivalence "
                f"series={series_name} ticks={len(ticks)} strategy={expected.strategy_id} "
                f"state={expected.state} reason={expected.reason_code} "
                f"contract={expected.contract_type} direction={expected.direction} "
                f"barrier={expected.barrier} "
                f"estimated={expected.estimated_probability_pct} "
                f"required={expected.required_probability_pct} "
                f"evidence={expected.evidence!r} bytes={len(expected_bytes)} "
                f"sha256={hashlib.sha256(expected_bytes).hexdigest()} identical=true"
            )
    assert comparisons == 12


@pytest.mark.parametrize(
    ("case_name", "strategy", "digits", "reason", "contract", "direction", "barrier"),
    (
        (
            "tail-over-above",
            TailProbabilityEdgeStrategy(),
            _tail_boundary_digits(direction="OVER", above=True),
            "TAIL_EDGE_CONSERVATIVE_SIGNAL",
            "DIGITOVER",
            "OVER",
            4,
        ),
        (
            "tail-under-above",
            TailProbabilityEdgeStrategy(),
            _tail_boundary_digits(direction="UNDER", above=True),
            "TAIL_EDGE_CONSERVATIVE_SIGNAL",
            "DIGITUNDER",
            "UNDER",
            5,
        ),
        (
            "differs-above",
            SelectiveDiffersEdgeStrategy(),
            _differs_above_digits(),
            "DIFFERS_EDGE_CONSERVATIVE_SIGNAL",
            "DIGITDIFF",
            "DIFFERS 0",
            0,
        ),
        (
            "parity-even-above",
            ParityRegimeEdgeStrategy(),
            _parity_even_above_digits(),
            "PARITY_EDGE_CONSERVATIVE_SIGNAL",
            "DIGITEVEN",
            "EVEN",
            None,
        ),
        (
            "parity-odd-above",
            ParityRegimeEdgeStrategy(),
            _invert_parity(_parity_even_above_digits()),
            "PARITY_EDGE_CONSERVATIVE_SIGNAL",
            "DIGITODD",
            "ODD",
            None,
        ),
    ),
)
def test_digit_strategy_wilson_frontier_above_threshold_emits_signal(
    case_name: str,
    strategy: object,
    digits: list[int],
    reason: str,
    contract: str,
    direction: str,
    barrier: int | None,
) -> None:
    decision = strategy.evaluate(_ticks(digits))  # type: ignore[attr-defined]

    assert decision.state is ShadowSignalState.SHADOW_SIGNAL
    assert decision.reason_code == reason
    assert decision.contract_type == contract
    assert decision.direction == direction
    assert decision.barrier == barrier
    assert decision.estimated_probability_pct is not None
    assert decision.required_probability_pct is not None
    if case_name.startswith("tail"):
        sample, successes, wilson, margin = _tail_stats(
            digits,
            direction=direction,
            barrier=barrier or 0,
            required=decision.required_probability_pct,
        )
        p_hat = Decimal(successes) / Decimal(sample)
    elif case_name.startswith("differs"):
        sample, successes, wilson, margin = _differs_stats(
            digits,
            barrier=barrier or 0,
            required=decision.required_probability_pct,
        )
        p_hat = Decimal(sample - successes) / Decimal(sample)
    else:
        sample, successes, wilson, margin = _parity_stats(
            digits,
            direction=direction,
            required=decision.required_probability_pct,
        )
        p_hat = Decimal(successes) / Decimal(sample)
    print(
        "wilson_frontier_above "
        f"case={case_name} contract={contract} direction={direction} barrier={barrier} "
        f"sample={sample} count={successes} p_hat={_format_decimal(p_hat)} "
        f"wilson={_format_decimal(wilson)} required={decision.required_probability_pct} "
        f"margin_pp={_format_decimal(margin)}"
    )
    assert Decimal("0") < margin < Decimal("0.05")


@pytest.mark.parametrize(
    ("case_name", "strategy", "digits", "reason", "stats_kind", "direction", "barrier"),
    (
        (
            "tail-over-below",
            TailProbabilityEdgeStrategy(),
            _tail_boundary_digits(direction="OVER", above=False),
            "TAIL_EDGE_NO_CONSERVATIVE_ADVANTAGE",
            "tail",
            "OVER",
            4,
        ),
        (
            "tail-under-below",
            TailProbabilityEdgeStrategy(),
            _tail_boundary_digits(direction="UNDER", above=False),
            "TAIL_EDGE_NO_CONSERVATIVE_ADVANTAGE",
            "tail",
            "UNDER",
            5,
        ),
        (
            "differs-below",
            SelectiveDiffersEdgeStrategy(),
            _differs_below_digits(),
            "DIFFERS_EDGE_NO_CONSERVATIVE_ADVANTAGE",
            "differs",
            "DIFFERS 0",
            0,
        ),
        (
            "parity-even-below",
            ParityRegimeEdgeStrategy(),
            _parity_even_below_digits(),
            "PARITY_EDGE_NO_CONSERVATIVE_ADVANTAGE",
            "parity",
            "EVEN",
            None,
        ),
        (
            "parity-odd-below",
            ParityRegimeEdgeStrategy(),
            _invert_parity(_parity_even_below_digits()),
            "PARITY_EDGE_NO_CONSERVATIVE_ADVANTAGE",
            "parity",
            "ODD",
            None,
        ),
    ),
)
def test_digit_strategy_wilson_frontier_below_threshold_returns_no_advantage(
    case_name: str,
    strategy: object,
    digits: list[int],
    reason: str,
    stats_kind: str,
    direction: str,
    barrier: int | None,
) -> None:
    decision = strategy.evaluate(_ticks(digits))  # type: ignore[attr-defined]

    assert decision.state is ShadowSignalState.MONITORING
    assert decision.reason_code == reason
    if stats_kind == "tail":
        sample, successes, wilson, margin = _tail_stats(
            digits,
            direction=direction,
            barrier=barrier or 0,
            required=Decimal("52.00"),
        )
        p_hat = Decimal(successes) / Decimal(sample)
    elif stats_kind == "differs":
        sample, successes, wilson, margin = _differs_stats(digits, barrier=barrier or 0)
        p_hat = Decimal(sample - successes) / Decimal(sample)
    else:
        sample, successes, wilson, margin = _parity_stats(digits, direction=direction)
        p_hat = Decimal(successes) / Decimal(sample)
    print(
        "wilson_frontier_below "
        f"case={case_name} direction={direction} barrier={barrier} "
        f"sample={sample} count={successes} p_hat={_format_decimal(p_hat)} "
        f"wilson={_format_decimal(wilson)} margin_pp={_format_decimal(margin)}"
    )
    assert Decimal("-0.05") < margin <= Decimal("0")


def test_digit_strategy_realistic_phat_series_are_reported() -> None:
    tail_digits = _tail_realistic_probability_digits()
    differs_digits = _differs_realistic_probability_digits()
    parity_digits = _differs_realistic_probability_digits()

    tail = TailProbabilityEdgeStrategy().evaluate(_ticks(tail_digits))
    differs = SelectiveDiffersEdgeStrategy().evaluate(_ticks(differs_digits))
    parity = ParityRegimeEdgeStrategy().evaluate(_ticks(parity_digits))

    assert tail.reason_code == "TAIL_EDGE_CONSERVATIVE_SIGNAL"
    assert differs.reason_code == "DIFFERS_EDGE_NO_CONSERVATIVE_ADVANTAGE"
    assert parity.reason_code == "PARITY_EDGE_NO_CONSERVATIVE_ADVANTAGE"
    tail_sample, tail_successes, tail_wilson, tail_margin = _tail_stats(
        tail_digits,
        direction="OVER",
        barrier=4,
        required=Decimal("52.00"),
    )
    differs_sample, differs_losing, differs_wilson, differs_margin = _differs_stats(
        differs_digits,
        barrier=0,
    )
    parity_sample, parity_successes, parity_wilson, parity_margin = _parity_stats(
        parity_digits,
        direction="EVEN",
    )
    print(
        "realistic_phat "
        f"tail_p_hat={_format_decimal(Decimal(tail_successes) / Decimal(tail_sample))} "
        f"tail_wilson={_format_decimal(tail_wilson)} "
        f"tail_margin_pp={_format_decimal(tail_margin)} "
        "differs_p_hat="
        f"{_format_decimal(Decimal(differs_sample - differs_losing) / Decimal(differs_sample))} "
        f"differs_wilson={_format_decimal(differs_wilson)} "
        f"differs_margin_pp={_format_decimal(differs_margin)} "
        f"parity_p_hat={_format_decimal(Decimal(parity_successes) / Decimal(parity_sample))} "
        f"parity_wilson={_format_decimal(parity_wilson)} "
        f"parity_margin_pp={_format_decimal(parity_margin)}"
    )
    assert Decimal("0.69") < Decimal(tail_successes) / Decimal(tail_sample) < Decimal("0.71")
    assert (
        Decimal("0.89")
        < Decimal(differs_sample - differs_losing) / Decimal(differs_sample)
        < Decimal("0.91")
    )
    assert Decimal("0.49") < Decimal(parity_successes) / Decimal(parity_sample) < Decimal("0.51")


@pytest.mark.parametrize(
    ("strategy", "accepted_reason", "insufficient_reason"),
    (
        (
            TailProbabilityEdgeStrategy(),
            "TAIL_EDGE_CONSERVATIVE_SIGNAL",
            "TAIL_EDGE_CONTEXT_INSUFFICIENT",
        ),
        (
            SelectiveDiffersEdgeStrategy(),
            "DIFFERS_EDGE_NO_CONSERVATIVE_ADVANTAGE",
            "DIFFERS_EDGE_CONTEXT_INSUFFICIENT",
        ),
        (
            ParityRegimeEdgeStrategy(),
            "PARITY_EDGE_NO_CONSERVATIVE_ADVANTAGE",
            "PARITY_EDGE_CONTEXT_INSUFFICIENT",
        ),
    ),
)
def test_digit_strategy_context_sample_accepts_70_and_rejects_69(
    strategy: object,
    accepted_reason: str,
    insufficient_reason: str,
) -> None:
    accepted_digits = [1] * 300 + _parity_segment(200, [2] * 35)
    insufficient_digits = [1] * 300 + _parity_segment(200, [2] * 34 + [1])

    accepted = strategy.evaluate(_ticks(accepted_digits))  # type: ignore[attr-defined]
    insufficient = strategy.evaluate(_ticks(insufficient_digits))  # type: ignore[attr-defined]

    assert accepted.reason_code == accepted_reason
    assert insufficient.reason_code == insufficient_reason
    print(
        "context_boundary "
        f"strategy={accepted.strategy_id} accepted_sample=70 "
        f"accepted_reason={accepted.reason_code} "
        f"insufficient_sample=69 insufficient_reason={insufficient.reason_code}"
    )


def test_selective_differs_returns_windows_disagree_when_barriers_diverge() -> None:
    decision = SelectiveDiffersEdgeStrategy().evaluate(_ticks(_differs_disagree_digits()))

    assert decision.state is ShadowSignalState.MONITORING
    assert decision.reason_code == "DIFFERS_EDGE_WINDOWS_DISAGREE"
    short = _differs_stats(_differs_disagree_digits(), barrier=0, window_size=200)
    medium = _differs_stats(_differs_disagree_digits(), barrier=1, window_size=350)
    long = _differs_stats(_differs_disagree_digits(), barrier=1, window_size=500)
    print(
        "windows_disagree "
        f"strategy={decision.strategy_id} short_barrier=0 short_sample={short[0]} "
        f"short_wilson={_format_decimal(short[2])} medium_barrier=1 "
        f"medium_sample={medium[0]} medium_wilson={_format_decimal(medium[2])} "
        f"long_barrier=1 long_sample={long[0]} long_wilson={_format_decimal(long[2])}"
    )


def test_strategy_ids_are_stable() -> None:
    assert tuple(item.value for item in DerivDigitStrategyId) == (
        "tail-probability-edge",
        "selective-differs-edge",
        "parity-regime-edge",
        "payout-routed-differs-session",
    )


def test_historical_shadow_only_records_are_preserved() -> None:
    decision = TailProbabilityEdgeStrategy().evaluate(
        _ticks([digit for _ in range(250) for digit in (9, 0)])
    )
    assert decision.state is ShadowSignalState.SHADOW_SIGNAL
    assert dict(decision.evidence)["entry_mode"] == "SHADOW_ONLY"


def test_stress_mode_requires_demo_account() -> None:
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = DigitRiskConfig()
    real_snapshot = replace(
        _telemetry(),
        source=DerivTelemetrySource.REAL_LIVE,
        connection_mode="REAL",
    )
    trader = DerivDigitAutoTrader(runtime, "CR-REAL", lambda: real_snapshot)
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_STRESS_MODE_REQUIRES_DEMO"
    assert runtime.requests == []


def test_toggling_strategy_does_not_affect_inflight_order() -> None:
    class PersistingRuntime(_Runtime):
        def submit(self, request: object) -> object:
            self.requests.append(request)  # type: ignore[arg-type]
            return type("Persisted", (), {"order_id": "order-in-flight"})()

    runtime = PersistingRuntime()
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)
    assert trader.evaluate_once() is True
    runtime.risk_ledger.digit_config = replace(
        runtime.risk_ledger.digit_config,
        enabled_strategy_ids=frozenset({"selective-differs-edge"}),
    )
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_ORDER_IN_FLIGHT"
    assert len(runtime.requests) == 1


class _LightStrategy:
    warmup_ticks = 1

    def __init__(self, strategy_id: DerivDigitStrategyId) -> None:
        self.strategy_id = strategy_id

    def evaluate(self, ticks: Sequence[MarketTick]) -> DigitStrategyDecision:
        tick = ticks[-1]
        return DigitStrategyDecision(
            self.strategy_id,
            tick.broker_symbol,
            ShadowSignalState.MONITORING,
            "STRESS_MONITORING",
            tick.epoch,
        )


@pytest.mark.slow
def test_stress_load_10_symbols_packaged_strategies() -> None:
    pool = DerivDigitEnginePool(
        tuple(f"R_TEST_{index}" for index in range(10)),
        engine_factory=lambda **kwargs: DerivDigitShadowEngine(
            symbol=str(kwargs["symbol"]),
            strategies=tuple(_LightStrategy(item) for item in DerivDigitStrategyId),
        ),
    )
    tracemalloc.start()
    try:
        for epoch in range(1, 10_001):
            for index in range(10):
                pool.ingest_tick(_ticks([epoch % 10], symbol=f"R_TEST_{index}")[0])
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    metrics = pool.metrics
    print(
        "stress_metrics "
        f"p95_us={metrics.evaluation_cycle_duration_microseconds_p95} "
        f"peak_bytes={peak} engines={pool.active_engines} db_reads=0"
    )
    assert pool.active_engines == 10
    assert metrics.enabled_strategies == 4
    assert metrics.evaluation_cycle_duration_microseconds_p95 < 20_000
    assert peak < 64 * 1024 * 1024
