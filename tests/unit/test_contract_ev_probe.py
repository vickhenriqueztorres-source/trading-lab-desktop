from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from apps.core.contract_ev_probe import (
    PROBE_SYMBOLS,
    ContractProbeSpec,
    build_contract_ev_probe_specs,
    run_contract_ev_probe,
    summarize_probe,
)
from apps.core.payout_routed_differs import SlidingWindowBrokerMessageBudget
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError


def test_contract_ev_probe_plan_is_bounded_to_65_public_proposals() -> None:
    specs = build_contract_ev_probe_specs()

    assert len(specs) == 65
    assert {spec.broker_symbol for spec in specs} == set(PROBE_SYMBOLS)
    assert len(build_contract_ev_probe_specs(("R_10", "R_100"))) == 26


def test_contract_ev_probe_uses_only_proposal_and_decimal_ev() -> None:
    transport = FakeDerivTransport(demo_authenticated=False)
    results = run_contract_ev_probe(
        transport,
        specs=build_contract_ev_probe_specs(("R_10",)),
        pause=lambda: None,
        monotonic_clock=lambda: 10.0,
    )

    assert len(results) == 13
    assert all(result.status == "OK" for result in results)
    assert transport.operation_counts == {DerivOperation.PROPOSAL: 13}
    assert transport.trading_write_requests == 0
    assert DerivOperation.BUY not in transport.operation_counts
    assert all(isinstance(result.payout_return_ratio, Decimal) for result in results)
    assert all(isinstance(result.ev_ratio, Decimal) for result in results)


def test_contract_ev_probe_fails_closed_when_budget_is_exhausted() -> None:
    transport = FakeDerivTransport(demo_authenticated=False)
    budget = SlidingWindowBrokerMessageBudget(max_messages_per_minute=1)
    specs = (
        ContractProbeSpec("R_10", "DIGITEVEN", None, 1, Decimal("0.5"), "Paridade"),
        ContractProbeSpec("R_10", "DIGITODD", None, 1, Decimal("0.5"), "Paridade"),
    )

    results = run_contract_ev_probe(
        transport,
        specs=specs,
        budget=budget,
        pause=lambda: None,
        monotonic_clock=lambda: 10.0,
    )

    assert [result.status for result in results] == ["OK", "SKIPPED"]
    assert results[1].reason_code == "BUDGET_EXHAUSTED"
    assert transport.operation_counts == {DerivOperation.PROPOSAL: 1}
    assert transport.trading_write_requests == 0


def test_contract_ev_probe_failure_path_never_uses_buy() -> None:
    transport = FakeDerivTransport(
        FakeDerivScenario.RATE_LIMIT,
        demo_authenticated=False,
    )
    specs = (ContractProbeSpec("R_10", "DIGITEVEN", None, 1, Decimal("0.5"), "Paridade"),)

    results = run_contract_ev_probe(transport, specs=specs, pause=lambda: None)

    assert results[0].status == "REJECTED"
    assert results[0].reason_code == "DERIV_RATE_LIMITED"
    assert transport.operation_counts == {DerivOperation.PROPOSAL: 1}
    assert transport.trading_write_requests == 0
    assert DerivOperation.BUY not in transport.operation_counts


def test_contract_ev_probe_never_sends_write_operation_even_if_transport_is_hostile() -> None:
    class RecordingTransport:
        def __init__(self) -> None:
            self.requests: list[tuple[DerivOperation, Mapping[str, object]]] = []

        def request(
            self,
            operation: DerivOperation,
            payload: Mapping[str, object],
            *,
            timeout: float,
        ) -> dict[str, object]:
            del timeout
            self.requests.append((operation, payload))
            raise DerivWorkerError(DerivErrorCategory.NETWORK_ERROR, "DERIV_NETWORK_ERROR")

    transport = RecordingTransport()
    specs = (ContractProbeSpec("R_100", "PUT", None, 10, Decimal("0.5"), "Rise/Fall"),)

    results = run_contract_ev_probe(transport, specs=specs, pause=lambda: None)

    assert results[0].reason_code == "DERIV_NETWORK_ERROR"
    assert [operation for operation, _payload in transport.requests] == [DerivOperation.PROPOSAL]
    assert all("buy" not in payload for _operation, payload in transport.requests)


def test_contract_ev_probe_summary_answers_core_questions() -> None:
    transport = FakeDerivTransport(demo_authenticated=False)
    results = run_contract_ev_probe(
        transport,
        specs=build_contract_ev_probe_specs(("R_10", "R_100")),
        pause=lambda: None,
        monotonic_clock=lambda: 10.0,
    )

    summary = summarize_probe(results)

    assert summary["payout_constante_entre_simbolos"] == "sim"
    assert summary["even_odd_iguais"] == "sim"
    assert summary["over4_under5_igual_even_odd"] == "sim"
