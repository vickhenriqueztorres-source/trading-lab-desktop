"""Faults are injected in local transports only; no external order is sent."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_failures import IQFailurePolicy, RecoveryMode
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.iqoption_worker.order_session import IQOptionOrderSession
from packages.brokers.iqoption.community_read_only import IQOptionExternalError
from packages.domain.models import Broker, Direction, Money, OrderCommand, OrderState, WorkerOutcome
from packages.persistence.writer import SingleDatabaseWriter
from tests.unit.test_iqoption_auto_trader import (
    FakeClient,
    FakeRuntime,
    _falling_prices,
    _make_candles,
    explicit_signal_catalog,
)

NOW = datetime(2026, 9, 3, 3, tzinfo=UTC)


def setup_trader(tmp_path=None, *, reason="IQOPTION_PURCHASE_TIME_EXPIRED", auto=False):
    clock = [0.0]
    config = [IqOptionRiskConfig(symbol="AUTO" if auto else "EURUSD-OTC")]
    runtime = FakeRuntime(OrderState.REJECTED, reason)
    if tmp_path is not None:
        runtime.writer = SingleDatabaseWriter(tmp_path / "state.db")
    client = FakeClient([])
    quotes = []

    def history(symbol, **kwargs):
        candles = _make_candles(_falling_prices(), symbol=symbol)
        end = NOW + timedelta(seconds=int(clock[0]) // 60 * 60)
        delta = end - candles[-1].close_time
        return [], [
            replace(c, open_time=c.open_time + delta, close_time=c.close_time + delta)
            for c in candles
        ]

    client.market_history = history
    client.iqoption_binary_payout = lambda symbol: quotes.append(symbol) or Decimal("0.85")
    cat = explicit_signal_catalog(("EURUSD-OTC", "GBPUSD-OTC"))

    def build():
        return IqOptionAutoTrader(
            lambda: SimpleNamespace(client=client),
            lambda: runtime,
            lambda: config[0],
            lambda: True,
            utc_clock=lambda: NOW + timedelta(seconds=clock[0]),
            monotonic=lambda: clock[0],
            catalog_provider=lambda: cat,
            monitor_provider=lambda: SimpleNamespace(ready=True),
        )

    return build(), runtime, client, clock, config, quotes, build


def test_transient_rejection_recovers_only_on_new_signal_without_rearm():
    trader, runtime, _, clock, _, quotes, _ = setup_trader()
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    clock[0] = 31
    runtime.reader.state = OrderState.ACCEPTED
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1  # same candle even after backoff
    clock[0] = 60
    trader._evaluate_cycle()
    assert len(runtime.requests) == 2
    assert len(quotes) == 2
    assert not trader._failures.failures
    assert runtime.requests[0].correlation_id != runtime.requests[1].correlation_id
    assert any(name == "iqoption_execution_recovered" for name, _ in runtime.events)


@pytest.mark.parametrize(
    "reason",
    [
        "IQOPTION_PURCHASE_TIME_EXPIRED",
        "IQOPTION_STAKE_BELOW_BROKER_MINIMUM",
        "IQOPTION_ORDER_REJECTED_REMOTE",
    ],
)
def test_asset_failure_does_not_block_other_asset(reason):
    trader, runtime, _, _, _, _, _ = setup_trader(reason=reason, auto=True)
    trader._evaluate_cycle()
    runtime.reader.state = OrderState.ACCEPTED
    trader._evaluate_cycle()
    assert [r.symbol for r in runtime.requests] == ["EURUSD-OTC", "GBPUSD-OTC"]
    assert "EURUSD-OTC" in trader._failures.failures


def test_rate_limit_blocks_iq_session_and_waits_for_budget():
    trader, runtime, _, clock, _, quotes, _ = setup_trader(
        reason="IQOPTION_RATE_LIMITED", auto=True
    )
    trader._evaluate_cycle()
    clock[0] = 59
    trader._evaluate_cycle()
    assert len(runtime.requests) == len(quotes) == 1
    assert trader._failures.failures["*"].mode == RecoveryMode.PROBE
    clock[0] = 60
    trader._scan_cursor = 1
    runtime.reader.state = OrderState.ACCEPTED
    trader._evaluate_cycle()
    assert len(runtime.requests) == 2


def test_minimum_requires_corrected_configuration_and_fresh_signal():
    trader, runtime, _, clock, config, _, _ = setup_trader(
        reason="IQOPTION_STAKE_BELOW_BROKER_MINIMUM"
    )
    trader._evaluate_cycle()
    trader.begin_new_run()
    clock[0] = 60
    config[0] = replace(config[0], daily_stop_loss_minor_units=2000)
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1  # unrelated limit is not a broker correction
    config[0] = replace(config[0], stake_minor_units=200)
    runtime.reader.state = OrderState.ACCEPTED
    trader._evaluate_cycle()
    assert len(runtime.requests) == 2
    assert runtime.requests[-1].amount.minor_units == 200


def test_restart_and_rearm_preserve_failure_and_signal(tmp_path):
    trader, runtime, _, clock, _, _, build = setup_trader(tmp_path)
    try:
        trader._evaluate_cycle()
        clock[0] = 10
        restarted = build()
        restarted.begin_new_run()
        restarted._evaluate_cycle()
        assert len(runtime.requests) == 1
        assert restarted._failures.failures["EURUSD-OTC"].retry_at == 30
        runtime.reader.state = OrderState.ACCEPTED
        clock[0] = 60
        restarted._evaluate_cycle()
        assert len(runtime.requests) == 2
        again = build()
        again.begin_new_run()
        again._evaluate_cycle()
        assert len(runtime.requests) == 2
    finally:
        runtime.writer.close()


def test_read_only_probe_failure_reschedules_without_buy():
    trader, runtime, client, clock, _, _, _ = setup_trader(reason="IQOPTION_ACTIVE_SUSPENDED")
    trader._evaluate_cycle()
    clock[0] = 300
    client.iqoption_binary_payout = lambda symbol: None
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    assert trader._failures.failures["EURUSD-OTC"].retry_at > clock[0]


def test_rejection_storm_is_bounded_and_not_reset_by_read_only_success():
    policy = IQFailurePolicy()
    for index in range(5):
        failure = policy.record(
            "IQOPTION_TEMPORARILY_UNAVAILABLE",
            "EURUSD",
            IqOptionRiskConfig(),
            index * 2000,
            confirmed_rejection=True,
        )
    assert failure.mode == RecoveryMode.MANUAL
    assert policy.blocked("EURUSD", IqOptionRiskConfig(), 10**8)
    assert policy.blocked("EURUSD-OTC", IqOptionRiskConfig(), 10**8) is None


def test_unknown_never_becomes_transient_and_exposure_blocks_other_symbols():
    trader, runtime, _, clock, _, _, _ = setup_trader(auto=True)
    runtime.reader.state = OrderState.UNKNOWN
    trader._evaluate_cycle()
    runtime.reader.list_nonterminal_orders = lambda: [
        {"broker": Broker.IQ_OPTION.value, "state": "UNKNOWN"}
    ]
    clock[0] = 100000
    trader.begin_new_run()
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    assert trader.status_reason == "IQOPTION_ORDER_IN_FLIGHT"


def test_disarmed_or_real_never_resumes():
    trader, runtime, _, clock, _, _, _ = setup_trader()
    trader._evaluate_cycle()
    clock[0] = 60
    trader._operator_armed = lambda: False
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    trader._operator_armed = lambda: True
    trader._account_type_provider = lambda: "REAL"
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1


def test_current_risk_limits_still_block_after_backoff():
    trader, runtime, _, clock, config, _, _ = setup_trader()
    trader._evaluate_cycle()
    trader._daily_profit_loss = -Decimal(config[0].daily_stop_loss_minor_units) / 100
    clock[0] = 60
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    assert trader.status_reason == "IQOPTION_STOP_LOSS_REACHED"


def command():
    return OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id=str(uuid4()),
        order_id=str(uuid4()),
        broker=Broker.IQ_OPTION,
        account_id="IQOPTION_PRACTICE",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        direction=Direction.CALL,
        amount=Money(100, "USD"),
        deadline_at=NOW + timedelta(days=1),
        duration=1,
        duration_unit="m",
    )


class ReplyTransport:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def request(self, name, payload, **kwargs):
        self.calls.append(name)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.parametrize(
    "reply",
    [
        {},
        {"status": True},
        {"status": "false"},
        {"status": False, "id": 12},
        {"status": False, "result": {"id": 12}},
        {"status": True, "id": True},
        {"status": True, "id": 0},
        {"status": True, "id": []},
        IQOptionExternalError("IQOPTION_ORDER_RESPONSE_INVALID"),
        TimeoutError(),
        ValueError(),
    ],
)
def test_malformed_or_failed_response_after_buy_is_unknown(reply):
    transport = ReplyTransport(reply)
    session = IQOptionOrderSession(transport)
    result = session.submit_order(command())
    assert result.outcome == WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND
    assert transport.calls == ["buy"]
    assert len(session._tracked_by_ref) == 1


def test_explicit_rejection_is_terminal_but_no_retry():
    transport = ReplyTransport({"status": False, "reason": "IQOPTION_ACTIVE_SUSPENDED"})
    result = IQOptionOrderSession(transport).submit_order(command())
    assert result.outcome == WorkerOutcome.REJECTED
    assert transport.calls == ["buy"]


def test_prevalidation_rejects_without_transport():
    transport = ReplyTransport({"status": True, "id": 12})
    result = IQOptionOrderSession(transport).submit_order(replace(command(), account_id="REAL"))
    assert result.outcome == WorkerOutcome.REJECTED
    assert transport.calls == []


def test_database_failure_before_dispatch_sends_nothing(tmp_path):
    trader, runtime, _, _, _, _, _ = setup_trader(tmp_path)
    runtime.writer.close()
    trader._evaluate_cycle()
    assert not runtime.requests
    assert trader.status_reason == "IQOPTION_EXECUTION_STATE_UNAVAILABLE"


def test_authoritative_health_gate_failure_is_not_a_permanent_latch():
    trader, runtime, _, clock, _, _, _ = setup_trader()
    runtime.failure = RuntimeError("Health Gate blocked: HG_AUTH_REQUIRED")
    trader._evaluate_cycle()
    assert trader.status_reason == "HG_AUTH_REQUIRED"
    assert not trader._failures.failures
    runtime.failure = None  # provider has recovered, not a manual ARM
    runtime.reader.state = OrderState.ACCEPTED
    clock[0] = 60
    trader._evaluate_cycle()
    assert len(runtime.requests) == 2
    assert trader.status_reason.startswith("ORDEM_ACEITA")


def test_storm_and_unknown_reason_remain_blocked_across_restart(tmp_path):
    trader, runtime, _, clock, _, _, build = setup_trader(
        tmp_path, reason="IQOPTION_UNRECOGNIZED_ERROR"
    )
    try:
        trader._evaluate_cycle()
        clock[0] = 86400
        restarted = build()
        restarted.begin_new_run()
        restarted._evaluate_cycle()
        assert len(runtime.requests) == 1
        assert restarted._failures.failures["EURUSD-OTC"].mode == RecoveryMode.MANUAL
    finally:
        runtime.writer.close()


def test_state_corruption_is_fail_closed(tmp_path):
    trader, runtime, _, _, _, _, _ = setup_trader(tmp_path)
    try:
        runtime.writer.save_iqoption_execution_state({"version": 999})
        trader._evaluate_cycle()
        assert not runtime.requests
        assert trader.status_reason == "IQOPTION_EXECUTION_STATE_UNAVAILABLE"
    finally:
        runtime.writer.close()


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"message": "ambiguous"},
        {"status": False, "id": 123},
    ],
)
def test_community_adapter_does_not_invent_rejection_when_id_is_missing(raw):
    from packages.brokers.iqoption.community_read_only import IQOptionCommunityReadOnlySession

    owner = SimpleNamespace(
        _selected_balance=lambda: {"id": 1},
        _binary_expiration=lambda duration: 123,
        _active_id=lambda symbol: 1,
        _request_message=lambda *a, **kw: {"msg": raw},
    )
    with pytest.raises(IQOptionExternalError, match="IQOPTION_ORDER_RESPONSE_INVALID"):
        IQOptionCommunityReadOnlySession._buy_binary_option(
            owner,
            {"active": "EURUSD-OTC", "direction": "call", "price": "1.00"},
            timeout=2,
        )


def test_community_prevalidation_proves_not_sent():
    from packages.brokers.iqoption.community_read_only import IQOptionCommunityReadOnlySession

    calls = []
    owner = SimpleNamespace(
        _selected_balance=lambda: {"id": None},
        _request_message=lambda *a, **kw: calls.append(a),
    )
    with pytest.raises(IQOptionExternalError) as raised:
        IQOptionCommunityReadOnlySession._buy_binary_option(
            owner,
            {"active": "EURUSD-OTC", "direction": "call", "price": "1.00"},
            timeout=2,
        )
    assert raised.value.submission_not_sent
    assert calls == []


def test_explicit_arm_requires_candle_closed_after_arm():
    trader, runtime, _, clock, _, _, _ = setup_trader()
    runtime.reader.state = OrderState.ACCEPTED
    clock[0] = 10
    trader.begin_new_run()
    trader._evaluate_cycle()
    assert runtime.requests == []
    assert trader.status_reason == "IQOPTION_NEW_SIGNAL_REQUIRED_AFTER_ARM"
    clock[0] = 60
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1


def test_ticket_expiry_before_admission_does_not_latch_account():
    trader, runtime, _, clock, _, _, _ = setup_trader()
    submit = runtime.submit

    def expire_ticket(request):
        clock[0] += 3
        trader.validate_runtime_entry(request)  # Core calls this before reserving.

    runtime.submit = expire_ticket
    trader._evaluate_cycle()
    assert runtime.requests == []
    assert trader.status_reason == "IQOPTION_PAYOUT_STALE"
    assert not trader._failures.failures
    runtime.submit = submit
    runtime.reader.state = OrderState.ACCEPTED
    clock[0] = 60
    trader._evaluate_cycle()
    assert len(runtime.requests) == 1


def test_pre_admission_wait_preserves_older_consumed_epoch():
    trader, runtime, _, clock, _, _, _ = setup_trader()
    runtime.reader.state = OrderState.ACCEPTED
    trader._evaluate_cycle()
    prior = dict(trader._last_evaluated_epochs)
    runtime.failure = RuntimeError("pending")
    runtime.failure.reason_code = "MANIFEST_MONITOR_PENDING"
    clock[0] = 60
    trader._evaluate_cycle()
    assert trader._last_evaluated_epochs == prior
