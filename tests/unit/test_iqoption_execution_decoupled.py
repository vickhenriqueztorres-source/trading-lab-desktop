from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from apps.core.execution_state import (
    ExecutionState,
    OperatorIntentStore,
    StopReason,
    TransportSupervisor,
)
from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from tests.unit.test_iqoption_auto_trader import FakeClient, FakeRuntime


def _armed_trader():
    client = FakeClient([])
    runtime = FakeRuntime()
    transport = TransportSupervisor(initially_armed=True)
    transport.mark_up()
    safe_stop = Mock(wraps=transport.safe_stop)
    transport.safe_stop = safe_stop  # type: ignore[method-assign]
    recoveries: list[str] = []
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,
        risk_config_provider=IqOptionRiskConfig,
        operator_armed=lambda: True,
        recovery_notifier=recoveries.append,
        transport_supervisor=transport,
    )
    return trader, client, runtime, transport, safe_stop, recoveries


def _assert_transport_failures_never_disarm(reason: str, repetitions: int) -> None:
    trader, client, _, transport, safe_stop, recoveries = _armed_trader()

    for _ in range(repetitions):
        trader._notify_session_failure(client, reason)

    assert trader.execution_state in {ExecutionState.ARMED, ExecutionState.ARMED_DEGRADED}
    assert transport.armed_intent is True
    safe_stop.assert_not_called()
    assert recoveries == [reason]


def test_50_timeouts_no_disarm() -> None:
    _assert_transport_failures_never_disarm("REQUEST_TIMEOUT", 50)


def test_10_ws_closed_no_disarm() -> None:
    _assert_transport_failures_never_disarm("WS_CLOSED", 10)


def test_transport_down_skips_evaluation() -> None:
    trader, _, runtime, transport, safe_stop, _ = _armed_trader()
    trader.on_transport_down("REQUEST_TIMEOUT")

    trader._evaluate_cycle()

    assert transport.state is ExecutionState.ARMED_DEGRADED
    assert trader.status_reason == "TRANSPORT_DOWN"
    assert runtime.requests == []
    assert trader._failures.failures == {}
    assert any(
        name == "iqoption_evaluation_skipped" and fields["reason_code"] == "TRANSPORT_DOWN"
        for name, fields in runtime.events
    )
    safe_stop.assert_not_called()


def test_armed_persisted_across_restart(tmp_path) -> None:
    first = TransportSupervisor(intent_store=OperatorIntentStore(tmp_path))
    first.arm()
    # Safe process shutdown blocks this process but preserves the operator's
    # durable choice. The dedicated bot-off command uses the default True.
    first.safe_stop(caller=StopReason.USER_COMMAND, persist_intent=False)

    restarted = TransportSupervisor(intent_store=OperatorIntentStore(tmp_path))

    assert restarted.armed_intent is True
    assert restarted.state is ExecutionState.ARMED_DEGRADED
    assert (tmp_path / "operator_intent.json").read_text(encoding="utf-8") == (
        '{"armed":true,"schema_version":1}'
    )
    assert not (tmp_path / "operator_intent.json.tmp").exists()
