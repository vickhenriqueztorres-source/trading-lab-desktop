from __future__ import annotations

from datetime import timedelta

from apps.core.coordinator import OutboxDispatcher
from apps.core.health import HealthGate
from apps.core.reconciliation import ReconciliationReport
from apps.core.runtime import CoreRuntime
from packages.domain.models import Broker, Direction, Money, OrderCommand, utc_now


class _NeverSubmitWorker:
    def __init__(self) -> None:
        self.calls = 0

    def submit_order(self, _command: OrderCommand) -> object:
        self.calls += 1
        raise AssertionError("stopped command crossed the worker boundary")


class _ClaimedWriter:
    def __init__(self, command: OrderCommand) -> None:
        self.command = command
        self.not_sent_reason: str | None = None

    def claim_next_message(self, *_args: object, **_kwargs: object) -> OrderCommand:
        return self.command

    def record_dispatch_not_sent(
        self,
        _command: OrderCommand,
        *,
        reason_code: str,
        now: object,
    ) -> None:
        del now
        self.not_sent_reason = reason_code


def _command() -> OrderCommand:
    now = utc_now()
    return OrderCommand(
        message_id="message-1",
        correlation_id="correlation-1",
        intent_id="intent-1",
        order_id="order-1",
        broker=Broker.IQ_OPTION,
        account_id="IQOPTION_PRACTICE",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        direction=Direction.CALL,
        amount=Money(minor_units=100, currency="USD"),
        deadline_at=now + timedelta(seconds=30),
        duration=1,
        duration_unit="m",
    )


def test_arm_one_broker_does_not_clear_other_stop(tmp_path) -> None:
    runtime = CoreRuntime(tmp_path)
    runtime.start()
    try:
        runtime.stop_new_entries_for(Broker.DERIV, "deriv-demo")
        runtime.stop_new_entries_for(Broker.IQ_OPTION, "IQOPTION_PRACTICE")

        assert runtime.resume_new_entries_for(Broker.IQ_OPTION, "IQOPTION_PRACTICE") is True
        assert runtime.health_gate.state_for("IQ_OPTION", "IQOPTION_PRACTICE").is_open
        assert runtime.health_gate.state_for("DERIV", "deriv-demo").reason_code == "HG_SAFE_STOP"
    finally:
        runtime.shutdown()


def test_arm_one_broker_cannot_clear_global_stop(tmp_path) -> None:
    runtime = CoreRuntime(tmp_path)
    runtime.start()
    try:
        runtime.stop_new_entries()

        assert runtime.resume_new_entries_for(Broker.IQ_OPTION, "IQOPTION_PRACTICE") is False
        assert runtime.health_gate.global_state.reason_code == "HG_SAFE_STOP"
    finally:
        runtime.shutdown()


def test_reconciliation_does_not_rearm_scoped_broker(tmp_path) -> None:
    runtime = CoreRuntime(tmp_path)
    runtime.start()
    try:
        runtime.stop_new_entries_for(Broker.IQ_OPTION, "IQOPTION_PRACTICE")

        runtime._on_reconciliation_cycle_completed(ReconciliationReport(()))

        assert runtime.health_gate.state_for("IQ_OPTION", "IQOPTION_PRACTICE").reason_code == (
            "HG_SAFE_STOP"
        )
    finally:
        runtime.shutdown()


def test_stop_after_claim_prevents_external_dispatch() -> None:
    command = _command()
    writer = _ClaimedWriter(command)
    worker = _NeverSubmitWorker()
    gate = HealthGate()
    dispatcher = OutboxDispatcher(writer, worker, gate)  # type: ignore[arg-type]
    gate.block_scope(command.broker.value, command.account_id, "HG_SAFE_STOP")

    claimed = dispatcher.dispatch_next(
        broker=command.broker.value,
        account_id=command.account_id,
    )

    assert claimed is command
    assert worker.calls == 0
    assert writer.not_sent_reason == "HG_SAFE_STOP"
