from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum

from packages.domain.market import BrokerProposalQuote

PAYOUT_ROUTED_DIFFERS_STRATEGY_ID = "payout-routed-differs-session"
PAYOUT_ROUTED_DIFFERS_VERSION = "1.9.11"
DIFFERS_SESSION_DISPLAY_NAME = "Sessão Differs"

# Official Deriv WebSocket budget for the shared proposal/proposal_open_contract/buy/sell
# row is 360/minute and 14,400/hour. This feature reserves 60/minute for buy,
# proposal_open_contract and reconciliation, so proposal refresh/subscription management
# may use at most 300 requests/minute.
DERIV_PROPOSAL_GROUP_LIMIT_PER_MINUTE = 360
DERIV_PROPOSAL_GROUP_RESERVED_FINANCIAL_PER_MINUTE = 60
DERIV_PROPOSAL_MESSAGE_BUDGET_PER_MINUTE = (
    DERIV_PROPOSAL_GROUP_LIMIT_PER_MINUTE - DERIV_PROPOSAL_GROUP_RESERVED_FINANCIAL_PER_MINUTE
)

DERIV_PROPOSAL_GROUP_LIMIT_PER_HOUR = 14_400
DERIV_PROPOSAL_GROUP_RESERVED_FINANCIAL_PER_HOUR = 2_400
DERIV_PROPOSAL_MESSAGE_BUDGET_PER_HOUR = (
    DERIV_PROPOSAL_GROUP_LIMIT_PER_HOUR - DERIV_PROPOSAL_GROUP_RESERVED_FINANCIAL_PER_HOUR
)

DERIV_PAYOUT_SESSION_SYMBOLS = ("R_10", "R_25", "R_50", "R_75", "R_100")
DERIV_PAYOUT_SESSION_FIXED_BARRIER = 0
DERIV_PAYOUT_SESSION_SUBSCRIPTION_COUNT = 1
DERIV_DIFFERS_SESSION_PROPOSAL_REQUESTS_PER_REFRESH = 1
DERIV_DIFFERS_SESSION_DEFAULT_REFRESH_TTL_SECONDS = 2.0
DERIV_DIFFERS_SESSION_DEFAULT_REQUESTS_PER_MINUTE = 30
DERIV_DIFFERS_SESSION_DEFAULT_REQUESTS_PER_HOUR = 1_800
# Public production probe on 2026-08-28 observed DIGITDIFF payout_return_ratio
# 0.090000 across R_10/R_25/R_50/R_75/R_100 and barriers 0-9. The floor below
# is a safety degradation guard, not a payout optimizer.
DERIV_OBSERVED_DIGITDIFF_PAYOUT_RETURN_RATIO = Decimal("0.090000")
DERIV_DIFFERS_SESSION_PAYOUT_SAFETY_FLOOR = Decimal("0.088")

_DIGIT_DIFF_WIN_PROBABILITY = Decimal("0.9")
_DIGIT_DIFF_LOSS_PROBABILITY = Decimal("0.1")


class PayoutRoutedDiffersState(StrEnum):
    ACTIVE = "ACTIVE"
    STOPPED_TAKE_PROFIT = "STOPPED_TAKE_PROFIT"
    STOPPED_LOSS = "STOPPED_LOSS"
    STOPPED_TOLL_BUDGET = "STOPPED_TOLL_BUDGET"
    STOPPED_DAILY = "STOPPED_DAILY"
    STOPPED_MANUAL = "STOPPED_MANUAL"


@dataclass(frozen=True, slots=True)
class BrokerMessageBudgetDecision:
    allowed: bool
    used_in_window: int
    limit: int
    pressure: bool


class SlidingWindowBrokerMessageBudget:
    """Fail-closed request counter for broker message budgets."""

    def __init__(
        self,
        *,
        max_messages_per_minute: int = DERIV_PROPOSAL_MESSAGE_BUDGET_PER_MINUTE,
        pressure_ratio: Decimal = Decimal("0.80"),
    ) -> None:
        if max_messages_per_minute <= 0:
            raise ValueError("broker message budget must be positive")
        if (
            not isinstance(pressure_ratio, Decimal)
            or not pressure_ratio.is_finite()
            or not Decimal("0") < pressure_ratio <= Decimal("1")
        ):
            raise ValueError("broker message pressure ratio is invalid")
        self._limit = max_messages_per_minute
        self._pressure_threshold = int(
            (Decimal(max_messages_per_minute) * pressure_ratio).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        self._timestamps: deque[float] = deque()

    @property
    def limit(self) -> int:
        return self._limit

    def try_acquire(self, *, now_monotonic: float, count: int = 1) -> BrokerMessageBudgetDecision:
        if now_monotonic < 0 or count <= 0:
            raise ValueError("broker message budget input is invalid")
        self._discard_expired(now_monotonic)
        used = len(self._timestamps)
        allowed = used + count <= self._limit
        pressure = used + count >= self._pressure_threshold
        if allowed:
            for _ in range(count):
                self._timestamps.append(now_monotonic)
            used += count
        return BrokerMessageBudgetDecision(
            allowed=allowed,
            used_in_window=used,
            limit=self._limit,
            pressure=pressure,
        )

    def _discard_expired(self, now_monotonic: float) -> None:
        boundary = now_monotonic - 60.0
        while self._timestamps and self._timestamps[0] <= boundary:
            self._timestamps.popleft()


@dataclass(frozen=True, slots=True)
class PayoutRoutedDiffersConfig:
    symbols: tuple[str, ...] = ("R_100",)
    fixed_barrier: int = DERIV_PAYOUT_SESSION_FIXED_BARRIER
    proposal_max_age_seconds: float = DERIV_DIFFERS_SESSION_DEFAULT_REFRESH_TTL_SECONDS
    min_payout_return_ratio: Decimal = DERIV_DIFFERS_SESSION_PAYOUT_SAFETY_FLOOR
    entry_interval_ticks: int = 3
    entry_min_interval_seconds: float = 5.0
    session_take_profit_ratio: Decimal = Decimal("0.04")
    session_stop_loss_ratio: Decimal = Decimal("0.12")
    session_toll_budget_minor_units: int = 1_000
    session_stake_ratio: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if not self.symbols or any(not symbol.strip() for symbol in self.symbols):
            raise ValueError("payout session symbols are required")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("payout session symbols must be unique")
        if not 0 <= self.fixed_barrier <= 9:
            raise ValueError("payout session fixed barrier is invalid")
        if self.proposal_max_age_seconds <= 0:
            raise ValueError("proposal TTL must be positive")
        if (
            not isinstance(self.min_payout_return_ratio, Decimal)
            or not self.min_payout_return_ratio.is_finite()
            or self.min_payout_return_ratio <= 0
        ):
            raise ValueError("minimum payout ratio is invalid")
        if self.entry_interval_ticks <= 0 or self.entry_min_interval_seconds < 0:
            raise ValueError("payout session cadence is invalid")
        for value in (
            self.session_take_profit_ratio,
            self.session_stop_loss_ratio,
            self.session_stake_ratio,
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError("payout session decimal parameter is invalid")
        if self.session_toll_budget_minor_units <= 0:
            raise ValueError("payout session toll budget must be positive")


@dataclass(frozen=True, slots=True)
class PayoutRoutedDiffersSelection:
    quote: BrokerProposalQuote
    best_available_ratio: Decimal
    worst_available_ratio: Decimal
    candidate_count: int
    theoretical_ev_ratio: Decimal
    proposal_age_ms: int

    @property
    def evidence(self) -> tuple[tuple[str, str], ...]:
        return (
            ("strategy_id", PAYOUT_ROUTED_DIFFERS_STRATEGY_ID),
            ("chosen_symbol", self.quote.broker_symbol),
            ("chosen_barrier", str(self.quote.barrier)),
            ("payout_return_ratio_chosen", str(self.quote.payout_return_ratio)),
            ("payout_return_ratio_best_available", str(self.best_available_ratio)),
            ("payout_return_ratio_worst_available", str(self.worst_available_ratio)),
            ("candidate_count", str(self.candidate_count)),
            ("theoretical_ev_ratio", str(self.theoretical_ev_ratio)),
            ("proposal_age_ms", str(self.proposal_age_ms)),
            ("entry_mode", "EXECUTABLE_SIGNAL"),
            ("environment", "DEMO"),
        )


@dataclass(frozen=True, slots=True)
class PayoutRoutedDiffersDecision:
    selection: PayoutRoutedDiffersSelection | None
    reason_code: str
    observed_payout_return_ratio: Decimal | None = None
    minimum_payout_return_ratio: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PayoutRoutedDiffersSessionState:
    session_id: str
    started_monotonic: float
    starting_balance_minor_units: int
    realized_pnl_minor_units: int = 0
    entries_count: int = 0
    wins_count: int = 0
    losses_count: int = 0
    theoretical_toll_minor_units: int = 0
    state: PayoutRoutedDiffersState = PayoutRoutedDiffersState.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.state is PayoutRoutedDiffersState.ACTIVE


def digit_differs_theoretical_ev_ratio(payout_return_ratio: Decimal) -> Decimal:
    if (
        not isinstance(payout_return_ratio, Decimal)
        or not payout_return_ratio.is_finite()
        or payout_return_ratio <= 0
    ):
        raise ValueError("payout return ratio is invalid")
    return _DIGIT_DIFF_WIN_PROBABILITY * payout_return_ratio - _DIGIT_DIFF_LOSS_PROBABILITY


def theoretical_toll_minor_units(stake_minor_units: int, payout_return_ratio: Decimal) -> int:
    if stake_minor_units <= 0:
        raise ValueError("stake must be positive")
    ev = digit_differs_theoretical_ev_ratio(payout_return_ratio)
    if ev >= 0:
        return 0
    toll = Decimal(stake_minor_units) * abs(ev)
    return int(toll.to_integral_value(rounding=ROUND_CEILING))


class PayoutRoutedDiffersProposalCache:
    """Fresh proposal cache keyed by symbol/contract/barrier.

    It never reads persistence and never asks the broker for data. A separate
    worker/monitor can feed it with subscription updates or paced read-only
    requests after acquiring message budget.
    """

    def __init__(
        self,
        config: PayoutRoutedDiffersConfig | None = None,
        *,
        message_budget: SlidingWindowBrokerMessageBudget | None = None,
        event_sink: Callable[[str, dict[str, object]], None] | None = None,
        symbol_provider: Callable[[], str] | None = None,
    ) -> None:
        self._config = config or PayoutRoutedDiffersConfig()
        self._quotes: dict[tuple[str, str, int | None], BrokerProposalQuote] = {}
        self._message_budget = message_budget or SlidingWindowBrokerMessageBudget()
        self._event_sink = event_sink
        self._symbol_provider = symbol_provider
        self._payout_change_events: set[tuple[str, str]] = set()

    @property
    def config(self) -> PayoutRoutedDiffersConfig:
        return self._config

    @property
    def quote_count(self) -> int:
        return len(self._quotes)

    @property
    def active_symbol(self) -> str:
        if self._symbol_provider is None:
            symbol = self._config.symbols[0]
        else:
            symbol = self._symbol_provider()
        symbol = symbol.strip()
        if not symbol:
            raise ValueError("active payout session symbol is invalid")
        return symbol

    def store(self, quote: BrokerProposalQuote) -> None:
        self._quotes[(quote.broker_symbol, quote.contract_type.upper(), quote.barrier)] = quote
        if (
            quote.contract_type.upper() == "DIGITDIFF"
            and quote.barrier == self._config.fixed_barrier
            and quote.payout_return_ratio != DERIV_OBSERVED_DIGITDIFF_PAYOUT_RETURN_RATIO
        ):
            event_key = (quote.broker_symbol, str(quote.payout_return_ratio))
            if event_key not in self._payout_change_events:
                self._payout_change_events.add(event_key)
                self._emit(
                    "broker_payout_changed",
                    broker_symbol=quote.broker_symbol,
                    observed_payout_return_ratio=str(quote.payout_return_ratio),
                    baseline_payout_return_ratio=str(DERIV_OBSERVED_DIGITDIFF_PAYOUT_RETURN_RATIO),
                )

    def acquire_quote_budget(self, *, now_monotonic: float, count: int = 1) -> bool:
        decision = self._message_budget.try_acquire(now_monotonic=now_monotonic, count=count)
        if decision.pressure:
            self._emit(
                "broker_message_budget_pressure",
                used=decision.used_in_window,
                limit=decision.limit,
                allowed=decision.allowed,
            )
        return decision.allowed

    def select(
        self,
        *,
        now_monotonic: float,
        digit_history: Iterable[int] = (),
    ) -> PayoutRoutedDiffersDecision:
        # The iterable is accepted only to let tests prove invariance. This
        # strategy deliberately does not inspect digit history.
        del digit_history
        fresh = self._fresh_quotes(now_monotonic)
        if not fresh:
            return PayoutRoutedDiffersDecision(None, "SESSION_NO_FRESH_PROPOSAL")
        selected = fresh[0]
        best_ratio = selected.payout_return_ratio
        worst_ratio = selected.payout_return_ratio
        if selected.payout_return_ratio < self._config.min_payout_return_ratio:
            return PayoutRoutedDiffersDecision(
                None,
                "SESSION_PAYOUT_BELOW_FLOOR",
                observed_payout_return_ratio=selected.payout_return_ratio,
                minimum_payout_return_ratio=self._config.min_payout_return_ratio,
            )
        age_ms = int(max(0.0, now_monotonic - selected.received_monotonic) * 1_000)
        return PayoutRoutedDiffersDecision(
            PayoutRoutedDiffersSelection(
                quote=selected,
                best_available_ratio=best_ratio,
                worst_available_ratio=worst_ratio,
                candidate_count=len(fresh),
                theoretical_ev_ratio=digit_differs_theoretical_ev_ratio(
                    selected.payout_return_ratio
                ),
                proposal_age_ms=age_ms,
            ),
            "EXECUTABLE_SIGNAL",
            observed_payout_return_ratio=selected.payout_return_ratio,
            minimum_payout_return_ratio=self._config.min_payout_return_ratio,
        )

    def _fresh_quotes(self, now_monotonic: float) -> tuple[BrokerProposalQuote, ...]:
        if now_monotonic < 0:
            raise ValueError("monotonic timestamp is invalid")
        configured = self._config
        quote = self._quotes.get((self.active_symbol, "DIGITDIFF", configured.fixed_barrier))
        if quote is None:
            return ()
        if now_monotonic - quote.received_monotonic <= configured.proposal_max_age_seconds:
            return (quote,)
        return ()

    def _emit(self, name: str, **fields: object) -> None:
        if self._event_sink is not None:
            self._event_sink(name, dict(fields))


class PayoutRoutedDiffersQuoteFeeder:
    """Bounded background proposal refresher outside the decision hot path."""

    def __init__(
        self,
        cache: PayoutRoutedDiffersProposalCache,
        quote_provider: Callable[..., BrokerProposalQuote],
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        interval_waiter: Callable[[float], bool] | None = None,
        enabled: Callable[[], bool] = lambda: True,
    ) -> None:
        self._cache = cache
        self._quote_provider = quote_provider
        self._monotonic = monotonic_clock
        self._stop = threading.Event()
        self._waiter = interval_waiter or self._stop.wait
        self._enabled = enabled
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="payout-routed-differs-proposal-feeder",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def refresh_once(self) -> int:
        if not self._enabled():
            return 0
        config = self._cache.config
        symbols = (self._cache.active_symbol,)
        now = self._monotonic()
        if not self._cache.acquire_quote_budget(now_monotonic=now, count=len(symbols)):
            return 0
        refreshed = 0
        for symbol in symbols:
            try:
                quote = self._quote_provider(
                    product="DIGITDIFF",
                    symbol=symbol,
                    amount_minor_units=100,
                    currency="USD",
                    prediction_digit=config.fixed_barrier,
                    received_monotonic=self._monotonic(),
                )
            except Exception:
                continue
            self._cache.store(quote)
            refreshed += 1
        return refreshed

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            if self._waiter(self._cache.config.proposal_max_age_seconds):
                return


def apply_session_settlement(
    state: PayoutRoutedDiffersSessionState,
    *,
    pnl_minor_units: int,
    stake_minor_units: int,
    payout_return_ratio: Decimal,
    config: PayoutRoutedDiffersConfig | None = None,
) -> PayoutRoutedDiffersSessionState:
    if not state.is_active:
        return state
    configured = config or PayoutRoutedDiffersConfig()
    pnl = state.realized_pnl_minor_units + pnl_minor_units
    toll = state.theoretical_toll_minor_units + theoretical_toll_minor_units(
        stake_minor_units,
        payout_return_ratio,
    )
    next_state = replace(
        state,
        realized_pnl_minor_units=pnl,
        entries_count=state.entries_count + 1,
        wins_count=state.wins_count + (1 if pnl_minor_units > 0 else 0),
        losses_count=state.losses_count + (1 if pnl_minor_units < 0 else 0),
        theoretical_toll_minor_units=toll,
    )
    take_profit = (
        Decimal(state.starting_balance_minor_units) * configured.session_take_profit_ratio
    ).to_integral_value(rounding=ROUND_CEILING)
    stop_loss = (
        Decimal(state.starting_balance_minor_units) * configured.session_stop_loss_ratio
    ).to_integral_value(rounding=ROUND_CEILING)
    if pnl >= int(take_profit):
        return replace(next_state, state=PayoutRoutedDiffersState.STOPPED_TAKE_PROFIT)
    if pnl <= -int(stop_loss):
        return replace(next_state, state=PayoutRoutedDiffersState.STOPPED_LOSS)
    if toll >= configured.session_toll_budget_minor_units:
        return replace(next_state, state=PayoutRoutedDiffersState.STOPPED_TOLL_BUDGET)
    return next_state
