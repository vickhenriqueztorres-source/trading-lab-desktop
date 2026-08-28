from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.risk import (
    ActiveExposurePort,
    GlobalRiskConfig,
    PersistedActiveExposurePort,
    RestoredExposure,
    RiskLedger,
)
from packages.domain.models import (
    Broker,
    Direction,
    Money,
    OrderCommand,
    OrderRequest,
    utc_now,
)
from packages.persistence.reader import StateReader
from packages.persistence.writer import (
    AccountBusyError,
    FinancialUnitOfWork,
    RiskLimitExceededError,
    SingleDatabaseWriter,
)
from packages.protocol.messages import WorkerSubmissionResult


class FailingExposurePort(ActiveExposurePort):
    def __init__(self) -> None:
        self.fail = True

    def active_reservations(self) -> tuple[RestoredExposure, ...]:
        if self.fail:
            raise OSError("simulated read failure")
        return ()


class MutableExposurePort(ActiveExposurePort):
    def __init__(self, exposures: tuple[RestoredExposure, ...] = ()) -> None:
        self.exposures = exposures

    def active_reservations(self) -> tuple[RestoredExposure, ...]:
        return self.exposures


class SpyWorker:
    def __init__(self) -> None:
        self.submit_count = 0

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        del command
        self.submit_count += 1
        raise AssertionError("risk failure must not reach the financial worker")


def request(
    name: str,
    *,
    broker: Broker = Broker.DERIV,
    account_id: str = "demo-account",
    symbol: str = "frxEURUSD",
    amount_minor: int = 1_000,
    currency: str = "USD",
) -> OrderRequest:
    return OrderRequest(
        correlation_id=f"corr-{name}-{uuid4()}",
        broker=broker,
        account_id=account_id,
        product="DIGITAL_OPTION",
        symbol=symbol,
        direction=Direction.CALL,
        amount=Money(amount_minor, currency),
        strategy_id="persisted-risk-test",
        strategy_version="1.0.0",
        deadline_at=utc_now() + timedelta(minutes=1),
    )


def persist_directly(writer: SingleDatabaseWriter, item: OrderRequest) -> str:
    intent_id = str(uuid4())
    reservation_id = str(uuid4())
    order_id = str(uuid4())
    command = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=item.correlation_id,
        intent_id=intent_id,
        order_id=order_id,
        broker=item.broker,
        account_id=item.account_id,
        product=item.product,
        symbol=item.symbol,
        direction=item.direction,
        amount=item.amount,
        deadline_at=item.deadline_at,
    )
    FinancialUnitOfWork(writer).persist(
        request=item,
        command=command,
        intent_id=intent_id,
        reservation_id=reservation_id,
        order_id=order_id,
        created_at=utc_now(),
    )
    return reservation_id


def test_exposure_is_derived_from_database_not_memory(tmp_path: Path) -> None:
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    ledger = RiskLedger(active_exposure_port=PersistedActiveExposurePort(reader))

    persist_directly(writer, request("direct", amount_minor=1_250))

    assert ledger.active_exposure_minor_units == 1_250
    assert ledger.get_metrics().global_exposure_minor_units == 1_250
    writer.close()


def test_released_reservation_disappears_without_memory_release(tmp_path: Path) -> None:
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    ledger = RiskLedger(active_exposure_port=PersistedActiveExposurePort(reader))
    reservation_id = persist_directly(writer, request("release", amount_minor=2_000))
    ledger.register_active_reservation(
        reservation_id,
        Broker.DERIV.value,
        "demo-account",
        "frxEURUSD",
        Money(2_000, "USD"),
    )

    writer.cancel_expired_pending_messages(utc_now() + timedelta(days=1))

    assert ledger.active_exposure_minor_units == 0
    assert ledger.reserve(request("after-release"), HealthGate()).amount.minor_units == 1_000
    writer.close()


def test_repeated_failed_paths_do_not_accumulate_phantom_exposure(tmp_path: Path) -> None:
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    ledger = RiskLedger(active_exposure_port=PersistedActiveExposurePort(reader))

    for index in range(20):
        reservation_id = persist_directly(writer, request(f"cycle-{index}"))
        ledger.register_active_reservation(
            reservation_id,
            Broker.DERIV.value,
            "demo-account",
            "frxEURUSD",
            Money(1_000, "USD"),
        )
        writer.cancel_expired_pending_messages(utc_now() + timedelta(days=1))

    assert ledger.active_exposure_minor_units == 0
    assert ledger.reserve(request("final"), HealthGate()).amount.minor_units == 1_000
    writer.close()


def test_symbol_exposure_uses_canonical_symbol_from_database(tmp_path: Path) -> None:
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    ledger = RiskLedger(active_exposure_port=PersistedActiveExposurePort(reader))
    persist_directly(writer, request("frx", account_id="a", symbol="frxEURUSD"))
    persist_directly(
        writer,
        request(
            "otc",
            broker=Broker.IQ_OPTION,
            account_id="b",
            symbol="OTC_EURUSD",
            amount_minor=1_500,
        ),
    )

    assert ledger.active_symbol_exposure_minor_units("EURUSD") == 2_500
    writer.close()


def test_reserve_fails_closed_and_gate_clears_after_successful_read() -> None:
    port = FailingExposurePort()
    gate = HealthGate()
    ledger = RiskLedger(active_exposure_port=port)

    with pytest.raises(RiskLimitExceededError) as error:
        ledger.reserve(request("failed-read"), gate)
    assert error.value.reason_code == "HG_EXPOSURE_UNKNOWN"
    assert gate.contains("HG_EXPOSURE_UNKNOWN")

    port.fail = False
    assert ledger.reserve(request("recovered-read"), gate).amount.minor_units == 1_000
    assert not gate.contains("HG_EXPOSURE_UNKNOWN")


def test_missing_exposure_port_is_explicitly_fail_closed() -> None:
    gate = HealthGate()
    ledger = RiskLedger()

    with pytest.raises(RiskLimitExceededError) as error:
        ledger.reserve(request("missing-port"), gate)

    assert error.value.reason_code == "HG_EXPOSURE_UNKNOWN"
    assert gate.contains("HG_EXPOSURE_UNKNOWN")


def test_restore_failure_preserves_previous_state() -> None:
    port = MutableExposurePort()
    ledger = RiskLedger(active_exposure_port=port)
    valid = {
        "reservation_id": "valid",
        "broker": "DERIV",
        "account_id": "demo",
        "amount_minor": 100,
        "currency": "USD",
        "symbol": "R_100",
    }
    ledger.restore([valid])
    before = dict(ledger._restored_reservations)

    with pytest.raises(ValueError, match="invalid persisted risk reservation"):
        ledger.restore([valid, {**valid, "reservation_id": "bad", "amount_minor": 0}])

    assert ledger._restored_reservations == before


def test_register_active_reservation_validates_and_is_idempotent() -> None:
    port = MutableExposurePort()
    ledger = RiskLedger(active_exposure_port=port)
    args = ("reservation", "DERIV", "demo", "R_100", Money(100, "USD"))

    with pytest.raises(ValueError, match="invalid active risk reservation"):
        ledger.register_active_reservation("bad", "DERIV", "demo", "R_100", Money(0, "USD"))
    ledger.register_active_reservation(*args)
    ledger.register_active_reservation(*args)
    with pytest.raises(ValueError, match="cannot be overwritten"):
        ledger.register_active_reservation(
            "reservation", "DERIV", "demo", "R_100", Money(101, "USD")
        )
    with pytest.raises(ValueError, match="currency does not match"):
        ledger.register_active_reservation(
            "eur-reservation", "DERIV", "other", "R_100", Money(100, "EUR")
        )


def test_register_active_reservation_respects_persisted_account_unique() -> None:
    port = MutableExposurePort(
        (RestoredExposure("existing", "DERIV", "demo", Money(100, "USD"), "R_100"),)
    )
    ledger = RiskLedger(active_exposure_port=port)

    with pytest.raises(ValueError, match="account already has"):
        ledger.register_active_reservation("new", "DERIV", "demo", "R_100", Money(100, "USD"))


def test_unique_active_reservation_per_account_is_respected(tmp_path: Path) -> None:
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    first = persist_directly(writer, request("first", amount_minor=750))

    with pytest.raises(AccountBusyError):
        persist_directly(writer, request("second", amount_minor=900))

    active = reader.list_active_reservations()
    assert len(active) == 1
    assert active[0]["reservation_id"] == first
    assert active[0]["amount_minor"] == 750
    writer.close()


def test_mixed_currency_exposure_is_rejected_not_summed() -> None:
    mixed = MutableExposurePort(
        (
            RestoredExposure("usd", "DERIV", "a", Money(100, "USD"), "R_100"),
            RestoredExposure("eur", "IQ_OPTION", "b", Money(100, "EUR"), "EURUSD"),
        )
    )
    ledger = RiskLedger(active_exposure_port=mixed)

    with pytest.raises(RiskLimitExceededError) as error:
        _ = ledger.active_exposure_minor_units
    assert error.value.reason_code == "HG_EXPOSURE_CURRENCY_MISMATCH"


def test_exposure_path_preserves_integer_minor_units() -> None:
    exposure = RestoredExposure("integer", "DERIV", "demo", Money(125, "USD"), "R_100")
    ledger = RiskLedger(active_exposure_port=MutableExposurePort((exposure,)))

    total = ledger.active_exposure_minor_units
    symbol_total = ledger.active_symbol_exposure_minor_units("R_100")

    assert type(exposure.amount.minor_units) is int
    assert type(total) is int
    assert type(symbol_total) is int


def test_global_and_symbol_limits_are_enforced_from_database(tmp_path: Path) -> None:
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    config = GlobalRiskConfig(
        global_max_exposure_minor_units=3_000,
        max_exposure_per_symbol_minor_units=2_000,
    )
    ledger = RiskLedger(config, active_exposure_port=PersistedActiveExposurePort(reader))
    persist_directly(writer, request("existing", amount_minor=1_500))

    with pytest.raises(RiskLimitExceededError) as symbol_error:
        ledger.reserve(request("symbol", amount_minor=600), HealthGate())
    assert symbol_error.value.reason_code == "HG_SYMBOL_EXPOSURE_LIMIT_EXCEEDED"

    with pytest.raises(RiskLimitExceededError) as global_error:
        ledger.reserve(request("global", symbol="R_100", amount_minor=1_600), HealthGate())
    assert global_error.value.reason_code == "HG_GLOBAL_EXPOSURE_EXCEEDED"
    writer.close()


def test_risk_path_does_not_send_order(tmp_path: Path) -> None:
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    worker = SpyWorker()
    ledger = RiskLedger(active_exposure_port=FailingExposurePort())
    coordinator = OrderCoordinator(writer, worker, HealthGate(), risk_ledger=ledger)

    with pytest.raises(RiskLimitExceededError) as error:
        coordinator.submit(request("no-send"))

    assert error.value.reason_code == "HG_EXPOSURE_UNKNOWN"
    assert worker.submit_count == 0
    writer.close()
