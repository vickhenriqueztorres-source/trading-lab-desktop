from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from math import sqrt
from typing import cast

from packages.domain.market import MarketTick
from packages.domain.models import Broker
from packages.strategy_catalog.digit_registry import (
    DigitStrategyManifest,
    DigitStrategyProtocol,
    DigitStrategyRegistration,
    DigitStrategyRegistry,
)
from packages.strategy_catalog.models import ParameterKind, ParameterSpec, ReleaseStatus, RiskClass


class DerivDigitStrategyId(StrEnum):
    TAIL_PROBABILITY_EDGE = "tail-probability-edge"
    SELECTIVE_DIFFERS_EDGE = "selective-differs-edge"
    PARITY_REGIME_EDGE = "parity-regime-edge"
    PAYOUT_ROUTED_DIFFERS_SESSION = "payout-routed-differs-session"


def _stable_strategy_id(strategy_id: str) -> DerivDigitStrategyId | str:
    try:
        return DerivDigitStrategyId(strategy_id)
    except ValueError:
        return strategy_id


class ShadowSignalState(StrEnum):
    WARMING_UP = "WARMING_UP"
    MONITORING = "MONITORING"
    SHADOW_SIGNAL = "SHADOW_SIGNAL"
    EXECUTABLE_SIGNAL = "EXECUTABLE_SIGNAL"
    DATA_BLOCKED = "DATA_BLOCKED"


class DigitAssetShadowState(StrEnum):
    WARMING_UP = "WARMING_UP"
    MONITORING = "MONITORING"
    CANDIDATE = "CANDIDATE"
    DATA_BLOCKED = "DATA_BLOCKED"


@dataclass(frozen=True, slots=True)
class DigitStrategyDecision:
    strategy_id: DerivDigitStrategyId | str
    symbol: str
    state: ShadowSignalState
    reason_code: str
    observed_epoch: int | None
    contract_type: str | None = None
    direction: str | None = None
    barrier: int | None = None
    estimated_probability_pct: Decimal | None = None
    required_probability_pct: Decimal | None = None
    evidence: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.symbol or not self.reason_code:
            raise ValueError("digit strategy decision identity is required")
        if self.barrier is not None and not 0 <= self.barrier <= 9:
            raise ValueError("digit strategy barrier is invalid")
        if self.state in {ShadowSignalState.SHADOW_SIGNAL, ShadowSignalState.EXECUTABLE_SIGNAL}:
            if (
                self.observed_epoch is None
                or self.contract_type is None
                or self.direction is None
                or self.estimated_probability_pct is None
                or self.required_probability_pct is None
                or not self.evidence
            ):
                raise ValueError("shadow signal requires complete statistical evidence")
        elif any(
            item is not None
            for item in (
                self.contract_type,
                self.direction,
                self.barrier,
                self.estimated_probability_pct,
                self.required_probability_pct,
            )
        ):
            raise ValueError("non-signal decision cannot carry a contract candidate")


@dataclass(frozen=True, slots=True)
class DigitStrategyProjection:
    strategy_id: DerivDigitStrategyId | str
    display_name: str
    markets: str
    lifecycle_status: str
    signal_state: ShadowSignalState
    reason_code: str
    warmup_current: int
    warmup_required: int
    last_signal_epoch: int | None
    last_signal_symbol: str | None
    last_contract_type: str | None
    last_direction: str | None
    last_barrier: int | None
    estimated_probability_pct: Decimal | None
    required_probability_pct: Decimal | None
    analysis_latency_microseconds: int
    signals_emitted_total: int = 0
    signals_executed_total: int = 0
    signals_lost_to_arbitration_total: int = 0
    analysis_latency_microseconds_p95: int = 0
    conditional_sample: int = 0

    def __post_init__(self) -> None:
        if not self.display_name or not self.markets or not self.lifecycle_status:
            raise ValueError("strategy projection labels are required")
        if not 0 <= self.warmup_current <= self.warmup_required:
            raise ValueError("strategy projection warmup is invalid")
        if self.analysis_latency_microseconds < 0:
            raise ValueError("strategy projection analysis latency is invalid")
        if (
            min(
                self.signals_emitted_total,
                self.signals_executed_total,
                self.signals_lost_to_arbitration_total,
                self.analysis_latency_microseconds_p95,
                self.conditional_sample,
            )
            < 0
        ):
            raise ValueError("strategy projection counters are invalid")


@dataclass(frozen=True, slots=True)
class DigitAssetShadowProjection:
    """Non-financial ranking evidence for one independently warmed Deriv symbol."""

    symbol: str
    state: DigitAssetShadowState
    reason_code: str
    warmup_current: int
    warmup_required: int
    selected: bool = False
    strategy_id: DerivDigitStrategyId | str | None = None
    contract_type: str | None = None
    barrier: int | None = None
    estimated_probability_pct: Decimal | None = None
    required_probability_pct: Decimal | None = None
    conservative_margin_pct: Decimal | None = None
    analysis_latency_microseconds: int = 0
    last_signal_epoch: int | None = None

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.reason_code.strip():
            raise ValueError("asset shadow projection identity is required")
        if not 0 <= self.warmup_current <= self.warmup_required:
            raise ValueError("asset shadow projection warmup is invalid")
        if self.analysis_latency_microseconds < 0:
            raise ValueError("asset shadow analysis latency is invalid")
        if self.last_signal_epoch is not None and self.last_signal_epoch < 0:
            raise ValueError("asset shadow signal epoch is invalid")
        if self.barrier is not None and not 0 <= self.barrier <= 9:
            raise ValueError("asset shadow barrier is invalid")
        signal_fields = (
            self.strategy_id,
            self.contract_type,
            self.estimated_probability_pct,
            self.required_probability_pct,
            self.conservative_margin_pct,
        )
        if self.state is DigitAssetShadowState.CANDIDATE:
            if any(item is None for item in signal_fields):
                raise ValueError("asset candidate requires complete shadow evidence")
        elif any(item is not None for item in signal_fields) or self.selected:
            raise ValueError("non-candidate asset cannot carry candidate evidence")


@dataclass(frozen=True, slots=True)
class DigitEnginePoolMetrics:
    evaluations_per_second: float
    active_engines: int
    enabled_strategies: int
    arbitration_candidates_p95: int
    evaluation_cycle_duration_microseconds_p95: int


def _last_digit(tick: MarketTick) -> int:
    return int(tick.quote.as_tuple().digits[-1])


def _valid_ticks(ticks: Sequence[MarketTick]) -> bool:
    if not ticks:
        return True
    symbol = ticks[0].broker_symbol
    previous_identity: tuple[Broker, str, int, Decimal, str] | None = None
    previous_epoch = 0
    for tick in ticks:
        if (
            tick.broker is not Broker.DERIV
            or tick.broker_symbol != symbol
            or tick.epoch < previous_epoch
        ):
            return False
        if previous_identity is not None and tick.identity == previous_identity:
            return False
        previous_identity = tick.identity
        previous_epoch = tick.epoch
    return True


def _wilson_bound(successes: int, total: int, *, upper: bool) -> Decimal:
    """99% Wilson score bound, used to avoid small-sample digit illusions."""

    if total <= 0 or successes < 0 or successes > total:
        raise ValueError("Wilson interval inputs are invalid")
    # This is non-monetary inference on bounded integer counts. Binary float keeps the hot
    # path below tick cadence; Decimal remains mandatory for all displayed thresholds.
    z = 2.575829
    n = float(total)
    p = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    spread = z * sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    value = (center + spread if upper else center - spread) / denominator
    return Decimal(str(max(0.0, min(1.0, value))))


def _conditional_outcomes(digits: Sequence[int], context_parity: int) -> tuple[int, ...]:
    return tuple(
        digits[index] for index in range(1, len(digits)) if digits[index - 1] % 2 == context_parity
    )


class TailProbabilityEdgeStrategy:
    strategy_id = DerivDigitStrategyId.TAIL_PROBABILITY_EDGE
    warmup_ticks = 500
    _window_sizes = (200, 350, 500)
    _minimum_context = 70
    _candidates = (
        ("DIGITOVER", "OVER", 2, Decimal("72.00")),
        ("DIGITUNDER", "UNDER", 7, Decimal("72.00")),
        ("DIGITOVER", "OVER", 3, Decimal("62.00")),
        ("DIGITUNDER", "UNDER", 6, Decimal("62.00")),
        ("DIGITOVER", "OVER", 4, Decimal("52.00")),
        ("DIGITUNDER", "UNDER", 5, Decimal("52.00")),
    )

    def evaluate(self, ticks: Sequence[MarketTick]) -> DigitStrategyDecision:
        symbol = ticks[-1].broker_symbol if ticks else "R_100"
        if len(ticks) < self.warmup_ticks:
            return DigitStrategyDecision(
                self.strategy_id,
                symbol,
                ShadowSignalState.WARMING_UP,
                "TAIL_EDGE_WARMING_UP",
                None,
            )
        window = tuple(ticks[-self.warmup_ticks :])
        if not _valid_ticks(window):
            return DigitStrategyDecision(
                self.strategy_id,
                symbol,
                ShadowSignalState.DATA_BLOCKED,
                "TAIL_EDGE_TICK_CONTEXT_INVALID",
                None,
            )
        decisions: list[tuple[str, str, int, Decimal, Decimal, int]] = []
        for window_size in self._window_sizes:
            digits = tuple(_last_digit(item) for item in window[-window_size:])
            outcomes = _conditional_outcomes(digits, digits[-1] % 2)
            if len(outcomes) < self._minimum_context:
                return DigitStrategyDecision(
                    self.strategy_id,
                    symbol,
                    ShadowSignalState.MONITORING,
                    "TAIL_EDGE_CONTEXT_INSUFFICIENT",
                    window[-1].epoch,
                )
            best: tuple[str, str, int, Decimal, Decimal, int] | None = None
            best_margin = Decimal("-100")
            for contract, direction, barrier, required in self._candidates:
                wins = sum(
                    digit > barrier if direction == "OVER" else digit < barrier
                    for digit in outcomes
                )
                lower = _wilson_bound(wins, len(outcomes), upper=False) * Decimal(100)
                margin = lower - required
                if margin > best_margin:
                    best = (contract, direction, barrier, lower, required, len(outcomes))
                    best_margin = margin
            assert best is not None
            if best_margin <= 0:
                return DigitStrategyDecision(
                    self.strategy_id,
                    symbol,
                    ShadowSignalState.MONITORING,
                    "TAIL_EDGE_NO_CONSERVATIVE_ADVANTAGE",
                    window[-1].epoch,
                )
            decisions.append(best)
        identity = {(item[0], item[1], item[2]) for item in decisions}
        if len(identity) != 1:
            return DigitStrategyDecision(
                self.strategy_id,
                symbol,
                ShadowSignalState.MONITORING,
                "TAIL_EDGE_WINDOWS_DISAGREE",
                window[-1].epoch,
            )
        contract, direction, barrier, probability, required, sample = decisions[-1]
        return DigitStrategyDecision(
            self.strategy_id,
            symbol,
            ShadowSignalState.SHADOW_SIGNAL,
            "TAIL_EDGE_CONSERVATIVE_SIGNAL",
            window[-1].epoch,
            contract,
            direction,
            barrier,
            probability.quantize(Decimal("0.01")),
            required,
            (
                ("confidence", "WILSON_99_PERCENT"),
                ("context", "PREVIOUS_DIGIT_PARITY"),
                ("context_sample", str(sample)),
                ("duration_ticks", "1"),
                ("entry_mode", "SHADOW_ONLY"),
            ),
        )


class SelectiveDiffersEdgeStrategy:
    strategy_id = DerivDigitStrategyId.SELECTIVE_DIFFERS_EDGE
    warmup_ticks = 500
    _window_sizes = (200, 350, 500)
    _minimum_context = 70
    _required_probability_pct = Decimal("92.25")

    def evaluate(self, ticks: Sequence[MarketTick]) -> DigitStrategyDecision:
        symbol = ticks[-1].broker_symbol if ticks else "R_100"
        if len(ticks) < self.warmup_ticks:
            return DigitStrategyDecision(
                self.strategy_id,
                symbol,
                ShadowSignalState.WARMING_UP,
                "DIFFERS_EDGE_WARMING_UP",
                None,
            )
        window = tuple(ticks[-self.warmup_ticks :])
        if not _valid_ticks(window):
            return DigitStrategyDecision(
                self.strategy_id,
                symbol,
                ShadowSignalState.DATA_BLOCKED,
                "DIFFERS_EDGE_TICK_CONTEXT_INVALID",
                None,
            )
        choices: list[tuple[int, Decimal, int]] = []
        for window_size in self._window_sizes:
            digits = tuple(_last_digit(item) for item in window[-window_size:])
            outcomes = _conditional_outcomes(digits, digits[-1] % 2)
            if len(outcomes) < self._minimum_context:
                return DigitStrategyDecision(
                    self.strategy_id,
                    symbol,
                    ShadowSignalState.MONITORING,
                    "DIFFERS_EDGE_CONTEXT_INSUFFICIENT",
                    window[-1].epoch,
                )
            counts = tuple(outcomes.count(digit) for digit in range(10))
            barrier = min(range(10), key=lambda digit: (counts[digit], digit))
            losing_upper = _wilson_bound(counts[barrier], len(outcomes), upper=True)
            win_lower = (Decimal(1) - losing_upper) * Decimal(100)
            if win_lower <= self._required_probability_pct:
                return DigitStrategyDecision(
                    self.strategy_id,
                    symbol,
                    ShadowSignalState.MONITORING,
                    "DIFFERS_EDGE_NO_CONSERVATIVE_ADVANTAGE",
                    window[-1].epoch,
                )
            choices.append((barrier, win_lower, len(outcomes)))
        if len({item[0] for item in choices}) != 1:
            return DigitStrategyDecision(
                self.strategy_id,
                symbol,
                ShadowSignalState.MONITORING,
                "DIFFERS_EDGE_WINDOWS_DISAGREE",
                window[-1].epoch,
            )
        barrier, probability, sample = choices[-1]
        return DigitStrategyDecision(
            self.strategy_id,
            symbol,
            ShadowSignalState.SHADOW_SIGNAL,
            "DIFFERS_EDGE_CONSERVATIVE_SIGNAL",
            window[-1].epoch,
            "DIGITDIFF",
            f"DIFFERS {barrier}",
            barrier,
            probability.quantize(Decimal("0.01")),
            self._required_probability_pct,
            (
                ("confidence", "WILSON_99_PERCENT"),
                ("selection_correction", "THREE_WINDOWS_SAME_DIGIT"),
                ("context_sample", str(sample)),
                ("duration_ticks", "1"),
                ("entry_mode", "SHADOW_ONLY"),
            ),
        )


class ParityRegimeEdgeStrategy:
    strategy_id = DerivDigitStrategyId.PARITY_REGIME_EDGE
    warmup_ticks = 500
    _window_sizes = (200, 350, 500)
    _minimum_context = 70
    _required_probability_pct = Decimal("52.00")

    def evaluate(self, ticks: Sequence[MarketTick]) -> DigitStrategyDecision:
        symbol = ticks[-1].broker_symbol if ticks else "R_100"
        if len(ticks) < self.warmup_ticks:
            return DigitStrategyDecision(
                self.strategy_id,
                symbol,
                ShadowSignalState.WARMING_UP,
                "PARITY_EDGE_WARMING_UP",
                None,
            )
        window = tuple(ticks[-self.warmup_ticks :])
        if not _valid_ticks(window):
            return DigitStrategyDecision(
                self.strategy_id,
                symbol,
                ShadowSignalState.DATA_BLOCKED,
                "PARITY_EDGE_TICK_CONTEXT_INVALID",
                None,
            )
        choices: list[tuple[str, str, Decimal, int]] = []
        for window_size in self._window_sizes:
            digits = tuple(_last_digit(item) for item in window[-window_size:])
            outcomes = _conditional_outcomes(digits, digits[-1] % 2)
            if len(outcomes) < self._minimum_context:
                return DigitStrategyDecision(
                    self.strategy_id,
                    symbol,
                    ShadowSignalState.MONITORING,
                    "PARITY_EDGE_CONTEXT_INSUFFICIENT",
                    window[-1].epoch,
                )
            even_count = sum(digit % 2 == 0 for digit in outcomes)
            odd_count = len(outcomes) - even_count
            if even_count >= odd_count:
                contract, direction, successes = "DIGITEVEN", "EVEN", even_count
            else:
                contract, direction, successes = "DIGITODD", "ODD", odd_count
            lower = _wilson_bound(successes, len(outcomes), upper=False) * Decimal(100)
            if lower <= self._required_probability_pct:
                return DigitStrategyDecision(
                    self.strategy_id,
                    symbol,
                    ShadowSignalState.MONITORING,
                    "PARITY_EDGE_NO_CONSERVATIVE_ADVANTAGE",
                    window[-1].epoch,
                )
            choices.append((contract, direction, lower, len(outcomes)))
        if len({item[0] for item in choices}) != 1:
            return DigitStrategyDecision(
                self.strategy_id,
                symbol,
                ShadowSignalState.MONITORING,
                "PARITY_EDGE_WINDOWS_DISAGREE",
                window[-1].epoch,
            )
        contract, direction, probability, sample = choices[-1]
        return DigitStrategyDecision(
            self.strategy_id,
            symbol,
            ShadowSignalState.SHADOW_SIGNAL,
            "PARITY_EDGE_CONSERVATIVE_SIGNAL",
            window[-1].epoch,
            contract,
            direction,
            None,
            probability.quantize(Decimal("0.01")),
            self._required_probability_pct,
            (
                ("confidence", "WILSON_99_PERCENT"),
                ("context", "PREVIOUS_DIGIT_PARITY"),
                ("context_sample", str(sample)),
                ("duration_ticks", "1"),
                ("entry_mode", "SHADOW_ONLY"),
            ),
        )


class PayoutRoutedDiffersSessionStrategy:
    """Catalog entry for proposal-confirmed Digit Differs sessions.

    Execution evidence is produced by the Core proposal cache, not by digit
    history, because DIGITDIFF has p=0.9 for every barrier. This strategy
    object is intentionally non-predictive and warmup-free.
    """

    strategy_id = DerivDigitStrategyId.PAYOUT_ROUTED_DIFFERS_SESSION
    warmup_ticks = 0

    def evaluate(self, ticks: Sequence[MarketTick]) -> DigitStrategyDecision:
        symbol = ticks[-1].broker_symbol if ticks else "aguardando-proposal"
        epoch = ticks[-1].epoch if ticks else None
        return DigitStrategyDecision(
            strategy_id=self.strategy_id,
            symbol=symbol,
            state=ShadowSignalState.MONITORING,
            reason_code="SESSION_NO_FRESH_PROPOSAL",
            observed_epoch=epoch,
        )


def _digit_parameter_schema() -> tuple[ParameterSpec, ...]:
    return (
        ParameterSpec("window_short", ParameterKind.INTEGER, True),
        ParameterSpec("window_medium", ParameterKind.INTEGER, True),
        ParameterSpec("window_long", ParameterKind.INTEGER, True),
        ParameterSpec("minimum_context", ParameterKind.INTEGER, True),
    )


def _payout_routed_parameter_schema() -> tuple[ParameterSpec, ...]:
    return (
        ParameterSpec("fixed_barrier", ParameterKind.INTEGER, True),
        ParameterSpec("proposal_max_age_seconds", ParameterKind.DECIMAL, True),
        ParameterSpec("min_payout_return_ratio", ParameterKind.DECIMAL, True),
        ParameterSpec("entry_interval_ticks", ParameterKind.INTEGER, True),
        ParameterSpec("entry_min_interval_seconds", ParameterKind.DECIMAL, True),
        ParameterSpec("session_take_profit_ratio", ParameterKind.DECIMAL, True),
        ParameterSpec("session_stop_loss_ratio", ParameterKind.DECIMAL, True),
        ParameterSpec("session_toll_budget_minor_units", ParameterKind.INTEGER, True),
        ParameterSpec("session_stake_ratio", ParameterKind.DECIMAL, True),
    )


def default_digit_strategy_registry() -> DigitStrategyRegistry:
    """Return the packaged catalog used by live, replay and tests."""

    def manifest(
        strategy_id: str,
        display_name: str,
        contracts: tuple[str, ...],
    ) -> DigitStrategyManifest:
        return DigitStrategyManifest(
            strategy_id=_stable_strategy_id(strategy_id),
            version="1.9.11",
            display_name_pt_br=display_name,
            emitted_contracts=contracts,
            parameter_schema=_digit_parameter_schema(),
            risk_class=RiskClass.ELEVATED,
            release_status=ReleaseStatus.PRACTICE_VALIDATED,
            warmup_ticks=500,
        )

    registrations = (
        DigitStrategyRegistration(
            manifest(
                DerivDigitStrategyId.TAIL_PROBABILITY_EDGE.value,
                "Edge de Caudas Over/Under",
                ("DIGITOVER", "DIGITUNDER"),
            ),
            cast(Callable[[], DigitStrategyProtocol[object]], TailProbabilityEdgeStrategy),
        ),
        DigitStrategyRegistration(
            manifest(
                DerivDigitStrategyId.SELECTIVE_DIFFERS_EDGE.value,
                "Edge Seletivo Digit Differs",
                ("DIGITDIFF",),
            ),
            cast(Callable[[], DigitStrategyProtocol[object]], SelectiveDiffersEdgeStrategy),
        ),
        DigitStrategyRegistration(
            manifest(
                DerivDigitStrategyId.PARITY_REGIME_EDGE.value,
                "Edge de Regime Par/Ímpar",
                ("DIGITEVEN", "DIGITODD"),
            ),
            cast(Callable[[], DigitStrategyProtocol[object]], ParityRegimeEdgeStrategy),
        ),
        DigitStrategyRegistration(
            DigitStrategyManifest(
                strategy_id=DerivDigitStrategyId.PAYOUT_ROUTED_DIFFERS_SESSION,
                version="1.9.11",
                display_name_pt_br="Sessão Differs",
                emitted_contracts=("DIGITDIFF",),
                parameter_schema=_payout_routed_parameter_schema(),
                risk_class=RiskClass.ELEVATED,
                release_status=ReleaseStatus.PRACTICE_VALIDATED,
                warmup_ticks=0,
            ),
            cast(
                Callable[[], DigitStrategyProtocol[object]],
                PayoutRoutedDiffersSessionStrategy,
            ),
        ),
    )
    return DigitStrategyRegistry(registrations)


class DerivDigitShadowEngine:
    """Bounded signal engine; execution eligibility is decided outside the strategy."""

    def __init__(
        self,
        *,
        capacity: int = 500,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
        registry: DigitStrategyRegistry | None = None,
        strategies: Sequence[DigitStrategyProtocol[object]] | None = None,
        symbol: str | None = None,
    ) -> None:
        if capacity < 500 or capacity > 20_000:
            raise ValueError("digit strategy engine capacity is outside bounds")
        if registry is not None and strategies is not None:
            raise ValueError("provide registry or injected strategies, not both")
        self._registry = registry or default_digit_strategy_registry()
        self._strategies = tuple(strategies or self._registry.create_strategies())
        if not self._strategies:
            raise ValueError("digit strategy engine requires at least one strategy")
        ids = tuple(str(item.strategy_id) for item in self._strategies)
        if len(set(ids)) != len(ids):
            raise ValueError("digit strategy engine ids must be unique")
        self._ticks: deque[MarketTick] = deque(maxlen=capacity)
        self._symbol = symbol
        self._last_identity: tuple[Broker, str, int, Decimal, str] | None = None
        self._last_decisions: dict[str, DigitStrategyDecision] = {}
        self._last_signal: dict[str, DigitStrategyDecision] = {}
        self._analysis_latency_microseconds = 0
        self._strategy_latencies: dict[str, deque[int]] = {
            str(item.strategy_id): deque(maxlen=256) for item in self._strategies
        }
        self._signals_emitted: dict[str, int] = {
            str(item.strategy_id): 0 for item in self._strategies
        }
        self._signals_executed: dict[str, int] = {
            str(item.strategy_id): 0 for item in self._strategies
        }
        self._signals_lost: dict[str, int] = {str(item.strategy_id): 0 for item in self._strategies}
        self._monotonic_ns = monotonic_ns

    @property
    def symbols(self) -> tuple[str, ...]:
        return ()

    def ingest_history(
        self,
        symbol: str,
        *,
        ticks: Sequence[MarketTick] = (),
    ) -> None:
        if self._symbol is not None and symbol != self._symbol:
            raise ValueError("DIGIT_ENGINE_FOREIGN_SYMBOL")
        self._symbol = symbol
        self._ticks.clear()
        self._last_identity = None
        maxlen = self._ticks.maxlen
        assert maxlen is not None
        for tick in ticks[-maxlen:]:
            if (
                tick.broker is Broker.DERIV
                and tick.broker_symbol == symbol
                and tick.identity != self._last_identity
            ):
                self._ticks.append(tick)
                self._last_identity = tick.identity
        self._evaluate_all()

    def ingest_tick(self, tick: MarketTick, *, evaluate: bool = True) -> None:
        if tick.broker is not Broker.DERIV:
            return
        if self._symbol is None:
            self._symbol = tick.broker_symbol
        elif tick.broker_symbol != self._symbol:
            raise ValueError("DIGIT_ENGINE_FOREIGN_SYMBOL")
        if tick.identity == self._last_identity:
            return
        self._ticks.append(tick)
        self._last_identity = tick.identity
        if evaluate:
            self._evaluate_all()

    def evaluate_current(self) -> None:
        self._evaluate_all()

    def _evaluate_all(self) -> None:
        started = self._monotonic_ns()
        ticks = tuple(self._ticks)
        for strategy in self._strategies:
            decision = strategy.evaluate(ticks)
            if not isinstance(decision, DigitStrategyDecision):
                raise TypeError("digit strategy returned an invalid decision")
            self._record(decision)
        elapsed = max(0, self._monotonic_ns() - started)
        self._analysis_latency_microseconds = elapsed // 1_000
        for strategy in self._strategies:
            self._strategy_latencies[str(strategy.strategy_id)].append(
                self._analysis_latency_microseconds
            )

    def _record(self, decision: DigitStrategyDecision) -> None:
        strategy_id = str(decision.strategy_id)
        self._last_decisions[strategy_id] = decision
        if decision.state is ShadowSignalState.SHADOW_SIGNAL:
            previous = self._last_signal.get(strategy_id)
            if previous is None or (decision.observed_epoch or 0) > (previous.observed_epoch or 0):
                self._last_signal[strategy_id] = decision
                self._signals_emitted[strategy_id] += 1

    def projections(self) -> tuple[DigitStrategyProjection, ...]:
        return tuple(
            self._projection(item.manifest.strategy_id, item.manifest.display_name_pt_br)
            for item in self._registry.registrations
        )

    def record_arbitration(self, strategy_id: str, *, executed: bool) -> None:
        target = self._signals_executed if executed else self._signals_lost
        if strategy_id in target:
            target[strategy_id] += 1

    def _projection(
        self,
        strategy_id: str,
        display_name: str,
    ) -> DigitStrategyProjection:
        decision = self._last_decisions.get(strategy_id)
        signal = self._last_signal.get(strategy_id)
        state = ShadowSignalState.WARMING_UP if decision is None else decision.state
        reason = "STRATEGY_WAITING_FOR_MARKET_DATA" if decision is None else decision.reason_code
        latencies = sorted(self._strategy_latencies.get(strategy_id, ()))
        p95_index = max(0, (len(latencies) * 95 + 99) // 100 - 1)
        p95 = 0 if not latencies else latencies[p95_index]
        evidence = {} if signal is None else dict(signal.evidence)
        return DigitStrategyProjection(
            strategy_id=strategy_id,
            display_name=display_name,
            markets=f"{self._symbol or 'aguardando ativo'} · 1 tick",
            lifecycle_status="RESEARCH_SHADOW",
            signal_state=state,
            reason_code=reason,
            warmup_current=min(len(self._ticks), self._registry.manifest(strategy_id).warmup_ticks),
            warmup_required=self._registry.manifest(strategy_id).warmup_ticks,
            last_signal_epoch=None if signal is None else signal.observed_epoch,
            last_signal_symbol=None if signal is None else signal.symbol,
            last_contract_type=None if signal is None else signal.contract_type,
            last_direction=None if signal is None else signal.direction,
            last_barrier=None if signal is None else signal.barrier,
            estimated_probability_pct=(
                None if signal is None else signal.estimated_probability_pct
            ),
            required_probability_pct=(None if signal is None else signal.required_probability_pct),
            analysis_latency_microseconds=self._analysis_latency_microseconds,
            signals_emitted_total=self._signals_emitted.get(strategy_id, 0),
            signals_executed_total=self._signals_executed.get(strategy_id, 0),
            signals_lost_to_arbitration_total=self._signals_lost.get(strategy_id, 0),
            analysis_latency_microseconds_p95=p95,
            conditional_sample=int(evidence.get("context_sample", "0")),
        )


class DerivDigitEnginePool:
    """Route each Deriv symbol to one isolated engine with a bounded lifecycle."""

    def __init__(
        self,
        symbols: Sequence[str] = (),
        *,
        maximum_symbols: int = 12,
        engine_factory: Callable[..., DerivDigitShadowEngine] = DerivDigitShadowEngine,
        evaluation_budget_microseconds: int = 20_000,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
        saturation_notifier: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        if not 1 <= maximum_symbols <= 32:
            raise ValueError("multi-asset radar symbol bound is invalid")
        self._maximum_symbols = maximum_symbols
        if evaluation_budget_microseconds <= 0:
            raise ValueError("evaluation budget must be positive")
        self._engine_factory = engine_factory
        self._evaluation_budget_microseconds = evaluation_budget_microseconds
        self._monotonic_ns = monotonic_ns
        self._saturation_notifier = saturation_notifier
        self._cycle_latencies: deque[int] = deque(maxlen=512)
        self._evaluations_total = 0
        self._evaluation_stride = 1
        self._ticks_since_evaluation: dict[str, int] = {}
        self._started_ns = monotonic_ns()
        self._subscribed_symbols: set[str] = set()
        self._engines: dict[str, DerivDigitShadowEngine] = {}
        self.set_symbols(symbols)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._subscribed_symbols))

    @property
    def active_engines(self) -> int:
        return len(self._engines)

    @property
    def metrics(self) -> DigitEnginePoolMetrics:
        latencies = sorted(self._cycle_latencies)
        index = max(0, (len(latencies) * 95 + 99) // 100 - 1)
        p95 = 0 if not latencies else latencies[index]
        elapsed_seconds = max(1e-9, (self._monotonic_ns() - self._started_ns) / 1_000_000_000)
        strategy_count = max(
            (len(engine.projections()) for engine in self._engines.values()),
            default=0,
        )
        return DigitEnginePoolMetrics(
            evaluations_per_second=self._evaluations_total / elapsed_seconds,
            active_engines=len(self._engines),
            enabled_strategies=strategy_count,
            arbitration_candidates_p95=0,
            evaluation_cycle_duration_microseconds_p95=p95,
        )

    def set_symbols(self, symbols: Sequence[str]) -> None:
        normalized = tuple(sorted({item.strip() for item in symbols if item.strip()}))
        if len(normalized) > self._maximum_symbols:
            raise ValueError("multi-asset radar symbol count exceeds bound")
        for symbol in tuple(self._engines):
            if symbol not in normalized:
                self._engines.pop(symbol)
        self._subscribed_symbols = set(normalized)

    def unsubscribe(self, symbol: str) -> None:
        self._subscribed_symbols.discard(symbol)
        self._engines.pop(symbol, None)

    def _engine_for(self, symbol: str) -> DerivDigitShadowEngine:
        if symbol not in self._subscribed_symbols:
            raise ValueError("asset is outside the active shadow universe")
        engine = self._engines.get(symbol)
        if engine is None:
            try:
                engine = self._engine_factory(symbol=symbol)
            except TypeError:
                engine = self._engine_factory()
            self._engines[symbol] = engine
        return engine

    def ingest_history(self, symbol: str, *, ticks: Sequence[MarketTick] = ()) -> None:
        engine = self._engine_for(symbol)
        engine.ingest_history(symbol, ticks=ticks)

    def ingest_tick(self, tick: MarketTick) -> None:
        started = self._monotonic_ns()
        engine = self._engine_for(tick.broker_symbol)
        count = self._ticks_since_evaluation.get(tick.broker_symbol, 0) + 1
        should_evaluate = count >= self._evaluation_stride
        engine.ingest_tick(tick, evaluate=should_evaluate)
        self._ticks_since_evaluation[tick.broker_symbol] = 0 if should_evaluate else count
        elapsed = max(0, self._monotonic_ns() - started) // 1_000
        self._cycle_latencies.append(elapsed)
        if should_evaluate:
            self._evaluations_total += 1
        latencies = sorted(self._cycle_latencies)
        p95_index = max(0, (len(latencies) * 95 + 99) // 100 - 1)
        p95 = 0 if not latencies else latencies[p95_index]
        saturated = p95 > self._evaluation_budget_microseconds
        if saturated:
            self._evaluation_stride = min(8, self._evaluation_stride + 1)
        elif p95 <= self._evaluation_budget_microseconds // 2:
            self._evaluation_stride = max(1, self._evaluation_stride - 1)
        if saturated and self._saturation_notifier is not None:
            self._saturation_notifier(
                {
                    "budget_microseconds": self._evaluation_budget_microseconds,
                    "cycle_microseconds": elapsed,
                    "active_engines": len(self._engines),
                    "evaluation_stride": self._evaluation_stride,
                }
            )

    def strategy_projections(self, symbol: str) -> tuple[DigitStrategyProjection, ...]:
        engine = self._engines.get(symbol)
        return () if engine is None else engine.projections()

    def all_strategy_projections(self) -> tuple[DigitStrategyProjection, ...]:
        return tuple(
            projection
            for symbol in sorted(self._engines)
            for projection in self._engines[symbol].projections()
        )

    def record_arbitration(
        self,
        winner_signal_id: str | None,
        rejected_signal_ids: Sequence[str],
    ) -> None:
        outcomes = [(item, False) for item in rejected_signal_ids]
        if winner_signal_id is not None:
            outcomes.append((winner_signal_id, True))
        for signal_id, executed in outcomes:
            parts = signal_id.rsplit(":", 2)
            if len(parts) != 3:
                continue
            symbol, strategy_id, _epoch = parts
            engine = self._engines.get(symbol)
            if engine is not None:
                engine.record_arbitration(strategy_id, executed=executed)

    def asset_ranking(self) -> tuple[DigitAssetShadowProjection, ...]:
        projections = [
            self._asset_projection(symbol, engine) for symbol, engine in self._engines.items()
        ]
        ordered = sorted(
            projections,
            key=lambda item: (
                0 if item.state is DigitAssetShadowState.CANDIDATE else 1,
                -(item.conservative_margin_pct or Decimal("-100")),
                item.symbol,
            ),
        )
        winner_assigned = False
        result: list[DigitAssetShadowProjection] = []
        for item in ordered:
            selected = item.state is DigitAssetShadowState.CANDIDATE and not winner_assigned
            result.append(replace(item, selected=selected))
            winner_assigned = winner_assigned or selected
        return tuple(result)

    @staticmethod
    def _asset_projection(
        symbol: str,
        engine: DerivDigitShadowEngine,
    ) -> DigitAssetShadowProjection:
        strategies = engine.projections()
        warmup_strategies = tuple(item for item in strategies if item.warmup_required > 0)
        warmup_current = min((item.warmup_current for item in warmup_strategies), default=0)
        warmup_required = max((item.warmup_required for item in warmup_strategies), default=0)
        latency = max((item.analysis_latency_microseconds for item in strategies), default=0)
        candidates = tuple(
            item
            for item in strategies
            if item.signal_state is ShadowSignalState.SHADOW_SIGNAL
            and item.estimated_probability_pct is not None
            and item.required_probability_pct is not None
            and item.last_contract_type is not None
        )
        if candidates:
            best = min(
                candidates,
                key=lambda item: (
                    -(
                        (item.estimated_probability_pct or Decimal("0"))
                        - (item.required_probability_pct or Decimal("0"))
                    ),
                    str(item.strategy_id),
                ),
            )
            estimated = best.estimated_probability_pct or Decimal("0")
            required = best.required_probability_pct or Decimal("0")
            margin = estimated - required
            return DigitAssetShadowProjection(
                symbol,
                DigitAssetShadowState.CANDIDATE,
                "ASSET_SHADOW_CANDIDATE",
                warmup_current,
                warmup_required,
                strategy_id=best.strategy_id,
                contract_type=best.last_contract_type,
                barrier=best.last_barrier,
                estimated_probability_pct=estimated,
                required_probability_pct=required,
                conservative_margin_pct=margin.quantize(Decimal("0.01")),
                analysis_latency_microseconds=latency,
                last_signal_epoch=best.last_signal_epoch,
            )
        if any(item.signal_state is ShadowSignalState.DATA_BLOCKED for item in strategies):
            state = DigitAssetShadowState.DATA_BLOCKED
            reason = "ASSET_SHADOW_DATA_BLOCKED"
        elif warmup_current < warmup_required:
            state = DigitAssetShadowState.WARMING_UP
            reason = "ASSET_SHADOW_WARMING_UP"
        else:
            state = DigitAssetShadowState.MONITORING
            reason = "ASSET_SHADOW_NO_CONSERVATIVE_CANDIDATE"
        return DigitAssetShadowProjection(
            symbol,
            state,
            reason,
            warmup_current,
            warmup_required,
            analysis_latency_microseconds=latency,
        )


class DerivMultiAssetShadowRadar(DerivDigitEnginePool):
    """Compatibility name for the UI's research ranking over the engine pool."""
