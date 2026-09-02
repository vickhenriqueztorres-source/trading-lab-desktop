from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_rsi import (
    IQOPTION_RSI_ARTIFACT,
    IQOPTION_RSI_STRATEGY_ID,
    IQOptionRsiDemoStrategy,
    calculate_wilder_rsi,
    iqoption_rsi_manifest,
)
from packages.strategies.models import RuntimeContext, StrategyEvaluationReason
from packages.strategies.runtime import StrategyRuntimeManager
from packages.strategy_catalog import (
    StrategyCatalog,
    ValidationEvidence,
    ValidationRegistry,
    ValidationStage,
)


def _context(*, broker: Broker = Broker.IQ_OPTION, timeframe: int = 60) -> RuntimeContext:
    return RuntimeContext(
        strategy_id=IQOPTION_RSI_STRATEGY_ID,
        strategy_version="1.0.0",
        broker=broker,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        timeframe_seconds=timeframe,
        configuration_version="rsi-demo-v1",
    )


def _candles(closes: tuple[Decimal, ...], *, closed: bool = True) -> tuple[MarketCandle, ...]:
    started = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    result = []
    for index, close in enumerate(closes):
        opened = started + timedelta(minutes=index)
        result.append(
            MarketCandle(
                broker=Broker.IQ_OPTION,
                broker_symbol="EURUSD-OTC",
                timeframe_seconds=60,
                open_time=opened,
                close_time=opened + timedelta(minutes=1),
                open=close,
                high=close + Decimal("0.0001"),
                low=close - Decimal("0.0001"),
                close=close,
                is_closed=closed,
            )
        )
    return tuple(result)


def test_wilder_rsi_reference_value_is_decimal_and_deterministic() -> None:
    closes = tuple(
        Decimal(value)
        for value in (
            "44.34",
            "44.09",
            "44.15",
            "43.61",
            "44.33",
            "44.83",
            "45.10",
            "45.42",
            "45.84",
            "46.08",
            "45.89",
            "46.03",
            "45.61",
            "46.28",
            "46.28",
        )
    )
    first = calculate_wilder_rsi(closes)
    second = calculate_wilder_rsi(closes)
    assert first == second
    assert first.quantize(Decimal("0.000001")) == Decimal("70.464135")


@pytest.mark.parametrize(
    ("closes", "expected_direction", "reason"),
    (
        (tuple(Decimal(100 - index) for index in range(15)), Direction.CALL, "RSI_OVERSOLD"),
        (tuple(Decimal(100 + index) for index in range(15)), Direction.PUT, "RSI_OVERBOUGHT"),
        (tuple(Decimal("100") for _ in range(15)), None, "RSI_NEUTRAL"),
    ),
)
def test_rsi_strategy_emits_only_strict_threshold_signals(
    closes: tuple[Decimal, ...],
    expected_direction: Direction | None,
    reason: str,
) -> None:
    decision = IQOptionRsiDemoStrategy().evaluate_decision(_candles(closes), _context())
    assert decision.direction is expected_direction
    assert decision.reason_code == reason


def test_rsi_strategy_rejects_open_candle_and_non_iq_context() -> None:
    candles = _candles(tuple(Decimal(100 + index) for index in range(15)), closed=False)
    strategy = IQOptionRsiDemoStrategy()
    with pytest.raises(ValueError, match="invalid candle series"):
        strategy.evaluate(candles, _context())
    with pytest.raises(ValueError, match="requires IQ Option"):
        strategy.evaluate(
            _candles(tuple(Decimal(100 + index) for index in range(15))),
            _context(broker=Broker.DERIV),
        )


def test_rsi_strategy_requires_full_warmup_and_one_minute_timeframe() -> None:
    strategy = IQOptionRsiDemoStrategy()
    with pytest.raises(ValueError, match="warming up"):
        strategy.evaluate(_candles(tuple(Decimal(100 + index) for index in range(14))), _context())
    with pytest.raises(ValueError, match="1-minute"):
        strategy.evaluate(
            _candles(tuple(Decimal(100 + index) for index in range(15))),
            _context(timeframe=300),
        )


def test_rsi_strategy_is_released_through_catalog_and_runtime_only_for_practice_context() -> None:
    registry = ValidationRegistry()
    manifest = iqoption_rsi_manifest()
    period_start = datetime(2026, 8, 1, tzinfo=UTC)
    for stage in ValidationStage:
        registry.record(
            ValidationEvidence(
                evidence_id=f"rsi-{stage.value.lower()}",
                strategy_id=manifest.strategy_id,
                strategy_version=manifest.version,
                report_id=manifest.validation_report_id,
                stage=stage,
                approved=True,
                broker=Broker.IQ_OPTION,
                product="BINARY_OPTION",
                symbol="EURUSD-OTC",
                timeframe_seconds=60,
                dataset_id=f"local-{stage.value.lower()}",
                period_start=period_start,
                period_end=period_start + timedelta(days=1),
                metrics=(("sample_count", Decimal("15")),),
            )
        )
    catalog = StrategyCatalog(registry)
    strategy = IQOptionRsiDemoStrategy()
    catalog.register(manifest, strategy, IQOPTION_RSI_ARTIFACT)
    runtime = StrategyRuntimeManager(catalog)

    result = None
    for candle in _candles(tuple(Decimal(100 - index) for index in range(15))):
        result = runtime.evaluate(
            _context(),
            candle,
            entitled_packs=frozenset({"iqoption-practice-candidates"}),
        )

    assert result is not None
    assert result.reason is StrategyEvaluationReason.SIGNAL
    assert result.signal is not None
    assert result.signal.direction is Direction.CALL
    assert result.signal.context.account_id == "PRACTICE_ACCOUNT"
