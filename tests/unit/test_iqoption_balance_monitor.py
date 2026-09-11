from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from apps.core.health import HealthGate
from apps.core.iqoption_balance_monitor import IQOptionBalanceMonitor, IQOptionBalanceQuality
from apps.core.iqoption_connection_safety import IQOptionMessageBudget
from packages.domain.market import BrokerAccountBalance
from packages.domain.models import Broker


def _balance(value: int, observed_at: datetime) -> BrokerAccountBalance:
    return BrokerAccountBalance(
        balance_minor_units=value,
        currency="USD",
        account_type="DEMO",
        observed_at_utc=observed_at,
    )


def test_probe_updates_balance_without_clock_or_strategy_dependency() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    values = iter((_balance(10_000, now), _balance(10_087, now + timedelta(seconds=5))))
    client = SimpleNamespace(broker_balance=lambda: next(values))
    gate = HealthGate()
    published: list[BrokerAccountBalance] = []
    monotonic = [0.0]
    wall = [now]
    monitor = IQOptionBalanceMonitor(
        SimpleNamespace(client=client),  # type: ignore[arg-type]
        gate,
        IQOptionMessageBudget(),
        utc_clock=lambda: wall[0],
        monotonic=lambda: monotonic[0],
        balance_notifier=published.append,
    )

    first = monitor.probe_once()
    monotonic[0] = 5.0
    wall[0] += timedelta(seconds=5)
    second = monitor.probe_once()

    assert first.balance is not None and first.balance.balance_minor_units == 10_000
    assert second.balance is not None and second.balance.balance_minor_units == 10_087
    assert second.is_fresh is True
    assert published[-1].observed_at_utc == now + timedelta(seconds=5)
    assert gate.state_for(Broker.IQ_OPTION.value, "IQOPTION_PRACTICE").is_open


def test_failed_refresh_keeps_number_for_diagnosis_but_blocks_entries_as_stale() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    def unavailable() -> BrokerAccountBalance:
        raise RuntimeError("offline")

    gate = HealthGate()
    monitor = IQOptionBalanceMonitor(
        SimpleNamespace(client=SimpleNamespace(broker_balance=unavailable)),  # type: ignore[arg-type]
        gate,
        IQOptionMessageBudget(),
        initial_balance=_balance(10_000, now),
        utc_clock=lambda: now + timedelta(seconds=16),
        monotonic=lambda: 16.0,
    )

    snapshot = monitor.probe_once()

    assert snapshot.balance is not None
    assert snapshot.balance.balance_minor_units == 10_000
    assert snapshot.is_fresh is False
    state = gate.state_for(Broker.IQ_OPTION.value, "IQOPTION_PRACTICE")
    assert state.is_open is False
    assert state.reason_code == "IQOPTION_BALANCE_STALE"


def test_transient_refresh_failure_retries_without_flapping_health_gate() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    wall = [now + timedelta(seconds=5)]

    def unavailable() -> BrokerAccountBalance:
        raise RuntimeError("temporary timeout")

    gate = HealthGate()
    monitor = IQOptionBalanceMonitor(
        SimpleNamespace(client=SimpleNamespace(broker_balance=unavailable)),  # type: ignore[arg-type]
        gate,
        IQOptionMessageBudget(),
        initial_balance=_balance(10_000, now),
        utc_clock=lambda: wall[0],
        monotonic=lambda: 5.0,
    )

    retrying = monitor.probe_once()

    assert retrying.is_fresh is True
    assert retrying.quality is IQOptionBalanceQuality.RETRYING
    assert retrying.consecutive_failures == 1
    assert gate.state_for(Broker.IQ_OPTION.value, "IQOPTION_PRACTICE").is_open

    wall[0] = now + timedelta(seconds=16)
    stale = monitor.probe_once()

    assert stale.is_fresh is False
    assert stale.quality is IQOptionBalanceQuality.STALE
    assert stale.consecutive_failures == 2
    assert not gate.state_for(Broker.IQ_OPTION.value, "IQOPTION_PRACTICE").is_open


def test_source_age_remains_stable_when_core_wall_clock_is_ahead() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    mono = [100.0]
    balance = BrokerAccountBalance(
        balance_minor_units=10_000,
        currency="USD",
        account_type="DEMO",
        observed_at_utc=now - timedelta(minutes=5),
        source_age_seconds=0.25,
        connection_generation=4,
        revision=7,
        source="GET_BALANCES",
    )
    monitor = IQOptionBalanceMonitor(
        SimpleNamespace(client=SimpleNamespace(broker_balance=lambda: balance)),  # type: ignore[arg-type]
        HealthGate(),
        IQOptionMessageBudget(),
        initial_balance=balance,
        utc_clock=lambda: now,
        monotonic=lambda: mono[0],
    )

    assert monitor.snapshot.is_fresh is True
    assert monitor.age_seconds == 0.25
    mono[0] += 5.0
    assert monitor.age_seconds == 5.25
    assert monitor.snapshot.is_fresh is True
