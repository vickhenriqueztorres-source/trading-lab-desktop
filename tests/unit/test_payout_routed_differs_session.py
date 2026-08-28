from __future__ import annotations

from decimal import Decimal

from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from apps.core.deriv_telemetry import DerivTelemetrySnapshot, DerivTelemetrySource
from apps.core.digit_risk_config import DigitRiskConfig
from apps.core.payout_routed_differs import (
    DERIV_DIFFERS_SESSION_DEFAULT_REQUESTS_PER_HOUR,
    DERIV_DIFFERS_SESSION_DEFAULT_REQUESTS_PER_MINUTE,
    DERIV_OBSERVED_DIGITDIFF_PAYOUT_RETURN_RATIO,
    DERIV_PROPOSAL_MESSAGE_BUDGET_PER_MINUTE,
    PAYOUT_ROUTED_DIFFERS_STRATEGY_ID,
    PayoutRoutedDiffersConfig,
    PayoutRoutedDiffersProposalCache,
    PayoutRoutedDiffersQuoteFeeder,
    PayoutRoutedDiffersSessionState,
    PayoutRoutedDiffersState,
    SlidingWindowBrokerMessageBudget,
    apply_session_settlement,
    digit_differs_theoretical_ev_ratio,
)
from apps.deriv_worker.fake_transport import FakeDerivTransport
from apps.deriv_worker.order_session import DerivLiveOrderSession
from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.websocket_client import DerivWebSocketClient, TransportRouteResult
from packages.domain.market import BrokerProposalQuote
from packages.domain.models import Broker, Money
from packages.observability.events import InMemoryEventSink
from packages.strategies.deriv_digits import (
    DerivDigitStrategyId,
    PayoutRoutedDiffersSessionStrategy,
    default_digit_strategy_registry,
)


def _quote(
    symbol: str,
    ratio: str,
    *,
    barrier: int = 0,
    received: float = 10.0,
) -> BrokerProposalQuote:
    return BrokerProposalQuote(
        broker=Broker.DERIV,
        broker_symbol=symbol,
        contract_type="DIGITDIFF",
        barrier=barrier,
        ask_price=Money(100, "USD"),
        payout=Money(int(Decimal("100") * (Decimal("1") + Decimal(ratio))), "USD"),
        proposal_id=f"proposal-{symbol}-{barrier}-{ratio}",
        received_monotonic=received,
        payout_return_ratio=Decimal(ratio),
    )


class _Reader:
    def list_nonterminal_orders(self) -> list[dict[str, object]]:
        return []

    def deriv_recent_strategy_settlements(self, *, limit_per_scope: int) -> list[dict[str, object]]:
        assert limit_per_scope == 30
        return []


class _RiskLedger:
    def __init__(self, config: DigitRiskConfig) -> None:
        self.digit_config = config

    def digit_entry_stake(self, _health_gate: object) -> Money:
        return Money(self.digit_config.stake_minor_units, self.digit_config.currency)


class _Runtime:
    dispatcher_started = True
    health_gate = object()

    def __init__(self, config: DigitRiskConfig) -> None:
        self.reader = _Reader()
        self.risk_ledger = _RiskLedger(config)
        self.requests = []
        self.event_sink = InMemoryEventSink()

    def submit(self, request: object) -> object:
        self.requests.append(request)
        return type("Persisted", (), {"order_id": "order-session"})()


def _demo_snapshot() -> DerivTelemetrySnapshot:
    return DerivTelemetrySnapshot(
        source=DerivTelemetrySource.DEMO_LIVE,
        connection_mode="DEMO",
        connected=True,
        balance=None,
        clock=None,
        reason_code=None,
    )


def test_proposal_is_readonly_on_worker_boundary() -> None:
    transport = FakeDerivTransport(demo_authenticated=True)
    session = DerivLiveOrderSession(transport, "VRTC123456")

    quote = session.quote_digit_contract(
        product="DIGITDIFF",
        symbol="R_100",
        amount_minor_units=100,
        currency="USD",
        prediction_digit=0,
    )

    assert quote["broker_symbol"] == "R_100"
    assert quote["contract_type"] == "DIGITDIFF"
    assert quote["barrier"] == 0
    assert quote["net_profit_ratio"] == "0.10"
    assert transport.operation_counts[DerivOperation.PROPOSAL] == 1
    assert transport.trading_write_requests == 0
    assert DerivOperation.BUY not in transport.operation_counts


def test_proposal_subscription_update_is_routed_not_unknown() -> None:
    client = DerivWebSocketClient()

    result = client._route_response(  # noqa: SLF001 - route invariant test
        {
            "msg_type": "proposal",
            "proposal": {"id": "proposal-1", "ask_price": "1.00", "payout": "1.09"},
            "subscription": {"id": "proposal-1"},
        }
    )

    assert result is TransportRouteResult.DELIVERED
    assert client.receive_proposal(timeout=0) is not None
    health = client.health_snapshot()
    assert health.unknown_msg_type_total == 0
    assert health.proposal_events_overflow_total == 0


def test_no_entry_without_fresh_proposal_and_expired_ttl() -> None:
    cache = PayoutRoutedDiffersProposalCache(
        PayoutRoutedDiffersConfig(proposal_max_age_seconds=2.0)
    )

    assert cache.select(now_monotonic=10.0).reason_code == "SESSION_NO_FRESH_PROPOSAL"

    cache.store(_quote("R_100", "0.100000", received=7.0))
    assert cache.select(now_monotonic=10.1).reason_code == "SESSION_NO_FRESH_PROPOSAL"


def test_symbol_selection_uses_active_symbol_not_payout_rotation() -> None:
    cache = PayoutRoutedDiffersProposalCache(
        PayoutRoutedDiffersConfig(min_payout_return_ratio=Decimal("0.088")),
        symbol_provider=lambda: "R_100",
    )
    cache.store(_quote("R_100", "0.091000"))
    cache.store(_quote("R_75", "0.103000"))
    cache.store(_quote("R_25", "0.103000"))
    cache.store(_quote("R_10", "0.095000"))

    decision = cache.select(now_monotonic=10.5)

    assert decision.selection is not None
    assert decision.selection.quote.broker_symbol == "R_100"
    assert decision.selection.best_available_ratio == Decimal("0.091000")
    assert decision.selection.worst_available_ratio == Decimal("0.091000")
    assert decision.selection.candidate_count == 1
    assert dict(decision.selection.evidence)["entry_mode"] == "EXECUTABLE_SIGNAL"


def test_barrier_is_fixed_and_independent_of_digit_history() -> None:
    cache = PayoutRoutedDiffersProposalCache(
        PayoutRoutedDiffersConfig(fixed_barrier=0, min_payout_return_ratio=Decimal("0.090"))
    )
    cache.store(_quote("R_100", "0.100000", barrier=0))
    cache.store(_quote("R_100", "0.500000", barrier=9))

    first = cache.select(now_monotonic=10.1, digit_history=[9] * 500)
    second = cache.select(now_monotonic=10.1, digit_history=list(range(10)) * 50)

    assert first.selection is not None
    assert second.selection is not None
    assert first.selection.quote.barrier == 0
    assert second.selection.quote.barrier == 0
    assert first.selection == second.selection


def test_payout_floor_blocks_entry_below_threshold() -> None:
    cache = PayoutRoutedDiffersProposalCache()
    cache.store(_quote("R_100", "0.087999"))

    decision = cache.select(now_monotonic=10.1)

    assert decision.selection is None
    assert decision.reason_code == "SESSION_PAYOUT_BELOW_FLOOR"
    assert decision.observed_payout_return_ratio == Decimal("0.087999")
    assert decision.minimum_payout_return_ratio == Decimal("0.088")


def test_real_observed_payout_passes_safety_floor_and_enters() -> None:
    cache = PayoutRoutedDiffersProposalCache()
    cache.store(_quote("R_100", str(DERIV_OBSERVED_DIGITDIFF_PAYOUT_RETURN_RATIO)))

    decision = cache.select(now_monotonic=10.1)

    assert decision.reason_code == "EXECUTABLE_SIGNAL"
    assert decision.selection is not None
    assert decision.selection.quote.payout_return_ratio == Decimal("0.090000")
    assert decision.minimum_payout_return_ratio == Decimal("0.088")


def test_payout_change_emits_telemetry_once_per_symbol_and_ratio() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    cache = PayoutRoutedDiffersProposalCache(
        event_sink=lambda name, fields: events.append((name, fields)),
    )

    cache.store(_quote("R_100", "0.090000"))
    cache.store(_quote("R_100", "0.091000"))
    cache.store(_quote("R_100", "0.091000"))

    assert events == [
        (
            "broker_payout_changed",
            {
                "broker_symbol": "R_100",
                "observed_payout_return_ratio": "0.091000",
                "baseline_payout_return_ratio": "0.090000",
            },
        )
    ]


def test_feeder_quotes_only_active_symbol() -> None:
    quoted_symbols: list[str] = []
    cache = PayoutRoutedDiffersProposalCache(symbol_provider=lambda: "R_75")

    def quote_provider(**kwargs: object) -> BrokerProposalQuote:
        symbol = str(kwargs["symbol"])
        quoted_symbols.append(symbol)
        return _quote(
            symbol,
            "0.090000",
            barrier=int(kwargs["prediction_digit"]),
            received=float(kwargs["received_monotonic"]),
        )

    feeder = PayoutRoutedDiffersQuoteFeeder(
        cache,
        quote_provider,
        monotonic_clock=lambda: 10.0,
    )

    assert feeder.refresh_once() == 1
    assert quoted_symbols == ["R_75"]


def test_ev_is_computed_exactly_with_decimal() -> None:
    assert digit_differs_theoretical_ev_ratio(Decimal("0.090000")) == Decimal("-0.0190000")
    assert digit_differs_theoretical_ev_ratio(Decimal("0.095000")) == Decimal("-0.0145000")
    assert digit_differs_theoretical_ev_ratio(Decimal("0.111111")) == Decimal("-1E-7")


def test_message_budget_fail_closed_and_emits_pressure() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    budget = SlidingWindowBrokerMessageBudget(
        max_messages_per_minute=3,
        pressure_ratio=Decimal("0.67"),
    )
    cache = PayoutRoutedDiffersProposalCache(
        message_budget=budget,
        event_sink=lambda name, fields: events.append((name, fields)),
    )

    assert cache.acquire_quote_budget(now_monotonic=1.0) is True
    assert cache.acquire_quote_budget(now_monotonic=2.0) is True
    assert cache.acquire_quote_budget(now_monotonic=3.0) is True
    assert cache.acquire_quote_budget(now_monotonic=4.0) is False

    assert events[-1][0] == "broker_message_budget_pressure"
    assert events[-1][1]["allowed"] is False
    assert DERIV_PROPOSAL_MESSAGE_BUDGET_PER_MINUTE == 300
    assert DERIV_DIFFERS_SESSION_DEFAULT_REQUESTS_PER_MINUTE == 30
    assert DERIV_DIFFERS_SESSION_DEFAULT_REQUESTS_PER_HOUR == 1_800


def test_session_stops_at_take_profit_stop_loss_and_toll_budget() -> None:
    base = PayoutRoutedDiffersSessionState(
        session_id="session-1",
        started_monotonic=1.0,
        starting_balance_minor_units=10_000,
    )

    take = apply_session_settlement(
        base,
        pnl_minor_units=400,
        stake_minor_units=100,
        payout_return_ratio=Decimal("0.090000"),
    )
    loss = apply_session_settlement(
        base,
        pnl_minor_units=-1200,
        stake_minor_units=100,
        payout_return_ratio=Decimal("0.090000"),
    )
    toll = apply_session_settlement(
        base,
        pnl_minor_units=0,
        stake_minor_units=10_000,
        payout_return_ratio=Decimal("0.090000"),
        config=PayoutRoutedDiffersConfig(session_toll_budget_minor_units=190),
    )

    assert take.state is PayoutRoutedDiffersState.STOPPED_TAKE_PROFIT
    assert loss.state is PayoutRoutedDiffersState.STOPPED_LOSS
    assert toll.state is PayoutRoutedDiffersState.STOPPED_TOLL_BUDGET


def test_strategy_catalog_registers_warmup_zero_demo_session() -> None:
    registry = default_digit_strategy_registry()
    manifest = registry.manifest(PAYOUT_ROUTED_DIFFERS_STRATEGY_ID)
    strategy = PayoutRoutedDiffersSessionStrategy()

    assert manifest.warmup_ticks == 0
    assert manifest.emitted_contracts == ("DIGITDIFF",)
    assert strategy.strategy_id is DerivDigitStrategyId.PAYOUT_ROUTED_DIFFERS_SESSION
    assert strategy.evaluate(()).reason_code == "SESSION_NO_FRESH_PROPOSAL"


def test_warmup_zero_allows_immediate_first_entry_from_fresh_proposal() -> None:
    config = DigitRiskConfig(
        active_strategy_id=PAYOUT_ROUTED_DIFFERS_STRATEGY_ID,
        enabled_strategy_ids=frozenset({PAYOUT_ROUTED_DIFFERS_STRATEGY_ID}),
        auto_select_symbol=True,
        stress_test_all_strategies_enabled=False,
    )
    cache = PayoutRoutedDiffersProposalCache(
        PayoutRoutedDiffersConfig(min_payout_return_ratio=Decimal("0.088"))
    )
    cache.store(_quote("R_100", "0.090000", received=9.9))
    runtime = _Runtime(config)
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "VRTC123456",
        _demo_snapshot,
        monotonic_clock=lambda: 10.0,
        proposal_cache=cache,
    )

    assert trader.evaluate_once() is True
    assert len(runtime.requests) == 1
    assert runtime.requests[0].strategy_id == PAYOUT_ROUTED_DIFFERS_STRATEGY_ID
    assert runtime.requests[0].symbol == "R_100"
    assert runtime.requests[0].prediction_digit == 0


def test_proposal_older_than_ttl_blocks_auto_trader_buy() -> None:
    config = DigitRiskConfig(
        active_strategy_id=PAYOUT_ROUTED_DIFFERS_STRATEGY_ID,
        enabled_strategy_ids=frozenset({PAYOUT_ROUTED_DIFFERS_STRATEGY_ID}),
        auto_select_symbol=True,
        stress_test_all_strategies_enabled=False,
    )
    cache = PayoutRoutedDiffersProposalCache(PayoutRoutedDiffersConfig())
    cache.store(_quote("R_100", "0.100000", received=7.0))
    runtime = _Runtime(config)
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "VRTC123456",
        _demo_snapshot,
        monotonic_clock=lambda: 10.1,
        proposal_cache=cache,
    )

    assert trader.evaluate_once() is False
    assert trader.last_reason == "SESSION_NO_FRESH_PROPOSAL"
    assert runtime.requests == []
