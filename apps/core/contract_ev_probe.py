from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from apps.core.payout_routed_differs import SlidingWindowBrokerMessageBudget
from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.schema import DerivWorkerError
from apps.deriv_worker.validators import PUBLIC_WS_URL
from apps.deriv_worker.websocket_client import DerivWebSocketClient

PROBE_SYMBOLS = ("R_10", "R_25", "R_50", "R_75", "R_100")
FALLBACK_PROBE_SYMBOLS = ("R_10", "R_100")
PROBE_AMOUNT = Decimal("1.00")
PROBE_CURRENCY = "USD"
PROBE_DURATION_UNIT = "t"
PROBE_BUDGET_PER_MINUTE = 300
DECIMAL_6 = Decimal("0.000001")


class ProposalTransport(Protocol):
    def request(
        self,
        operation: DerivOperation,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class ContractProbeSpec:
    broker_symbol: str
    contract_type: str
    barrier: int | None
    duration_ticks: int
    theoretical_probability: Decimal
    group: str

    @property
    def fair_payout_return_ratio(self) -> Decimal:
        return (Decimal("1") - self.theoretical_probability) / self.theoretical_probability

    def proposal_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "proposal": 1,
            "amount": PROBE_AMOUNT,
            "basis": "stake",
            "contract_type": self.contract_type,
            "currency": PROBE_CURRENCY,
            "duration": self.duration_ticks,
            "duration_unit": PROBE_DURATION_UNIT,
            "underlying_symbol": self.broker_symbol,
        }
        if self.barrier is not None:
            payload["barrier"] = str(self.barrier)
        return payload


@dataclass(frozen=True, slots=True)
class ContractProbeResult:
    spec: ContractProbeSpec
    status: str
    ask_price: Decimal | None = None
    payout: Decimal | None = None
    payout_return_ratio: Decimal | None = None
    proposal_id: str | None = None
    received_monotonic: float | None = None
    latency_ms: Decimal | None = None
    reason_code: str | None = None

    @property
    def ev_ratio(self) -> Decimal | None:
        if self.payout_return_ratio is None:
            return None
        p = self.spec.theoretical_probability
        return p * self.payout_return_ratio - (Decimal("1") - p)

    @property
    def fair_distance_pp(self) -> Decimal | None:
        if self.payout_return_ratio is None:
            return None
        return (self.payout_return_ratio - self.spec.fair_payout_return_ratio) * Decimal("100")


def build_contract_ev_probe_specs(
    symbols: Sequence[str] = PROBE_SYMBOLS,
) -> tuple[ContractProbeSpec, ...]:
    specs: list[ContractProbeSpec] = []
    for symbol in symbols:
        specs.extend(
            (
                ContractProbeSpec(symbol, "DIGITEVEN", None, 1, Decimal("0.5"), "Paridade"),
                ContractProbeSpec(symbol, "DIGITODD", None, 1, Decimal("0.5"), "Paridade"),
                ContractProbeSpec(symbol, "DIGITOVER", 4, 1, Decimal("0.5"), "Alto/Baixo"),
                ContractProbeSpec(symbol, "DIGITUNDER", 5, 1, Decimal("0.5"), "Alto/Baixo"),
                ContractProbeSpec(symbol, "DIGITOVER", 2, 1, Decimal("0.7"), "Assimétrico"),
                ContractProbeSpec(symbol, "DIGITUNDER", 7, 1, Decimal("0.7"), "Assimétrico"),
                ContractProbeSpec(symbol, "CALL", None, 1, Decimal("0.5"), "Rise/Fall"),
                ContractProbeSpec(symbol, "PUT", None, 1, Decimal("0.5"), "Rise/Fall"),
                ContractProbeSpec(symbol, "CALL", None, 5, Decimal("0.5"), "Rise/Fall"),
                ContractProbeSpec(symbol, "PUT", None, 5, Decimal("0.5"), "Rise/Fall"),
                ContractProbeSpec(symbol, "CALL", None, 10, Decimal("0.5"), "Rise/Fall"),
                ContractProbeSpec(symbol, "PUT", None, 10, Decimal("0.5"), "Rise/Fall"),
                ContractProbeSpec(symbol, "DIGITMATCH", 0, 1, Decimal("0.1"), "Match"),
            )
        )
    return tuple(specs)


def run_contract_ev_probe(
    transport: ProposalTransport,
    *,
    specs: Iterable[ContractProbeSpec],
    budget: SlidingWindowBrokerMessageBudget | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    pause: Callable[[], None] | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[ContractProbeResult, ...]:
    request_budget = budget or SlidingWindowBrokerMessageBudget(
        max_messages_per_minute=PROBE_BUDGET_PER_MINUTE
    )
    results: list[ContractProbeResult] = []
    for spec in specs:
        started = monotonic_clock()
        if not request_budget.try_acquire(now_monotonic=started).allowed:
            results.append(ContractProbeResult(spec, "SKIPPED", reason_code="BUDGET_EXHAUSTED"))
            continue
        try:
            response = transport.request(
                DerivOperation.PROPOSAL,
                spec.proposal_payload(),
                timeout=timeout_seconds,
            )
        except DerivWorkerError as exc:
            results.append(ContractProbeResult(spec, "REJECTED", reason_code=exc.reason_code))
        except (OSError, TimeoutError) as exc:
            results.append(
                ContractProbeResult(spec, "REJECTED", reason_code=type(exc).__name__.upper())
            )
        else:
            received = monotonic_clock()
            results.append(_result_from_response(spec, response, received, received - started))
        if pause is not None:
            pause()
    return tuple(results)


def _result_from_response(
    spec: ContractProbeSpec,
    response: Mapping[str, object],
    received_monotonic: float,
    elapsed_seconds: float,
) -> ContractProbeResult:
    error = response.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        return ContractProbeResult(
            spec,
            "REJECTED",
            reason_code=str(code) if code is not None else "DERIV_PROPOSAL_REJECTED",
        )
    proposal = response.get("proposal")
    if not isinstance(proposal, Mapping):
        return ContractProbeResult(spec, "REJECTED", reason_code="DERIV_PROPOSAL_INVALID")
    try:
        proposal_id = str(proposal["id"])
        ask_price = Decimal(str(proposal["ask_price"]))
        payout = Decimal(str(proposal["payout"]))
    except (KeyError, ArithmeticError, ValueError) as exc:
        return ContractProbeResult(spec, "REJECTED", reason_code=type(exc).__name__.upper())
    if not ask_price.is_finite() or not payout.is_finite() or ask_price <= 0:
        return ContractProbeResult(spec, "REJECTED", reason_code="DERIV_PROPOSAL_INVALID")
    payout_return_ratio = (payout - ask_price) / ask_price
    latency_ms = (Decimal(str(elapsed_seconds)) * Decimal("1000")).quantize(DECIMAL_6)
    return ContractProbeResult(
        spec,
        "OK",
        ask_price=ask_price,
        payout=payout,
        payout_return_ratio=payout_return_ratio,
        proposal_id=proposal_id,
        received_monotonic=received_monotonic,
        latency_ms=latency_ms,
    )


def quantize6(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return str(value.quantize(DECIMAL_6, rounding=ROUND_HALF_UP))


def full_results_markdown(results: Sequence[ContractProbeResult]) -> str:
    lines = [
        "| Símbolo | Contrato | Barreira | Duração | Payout | EV | Distância justo (pp) | Status |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        spec = result.spec
        lines.append(
            "| "
            + " | ".join(
                (
                    spec.broker_symbol,
                    spec.contract_type,
                    "—" if spec.barrier is None else str(spec.barrier),
                    str(spec.duration_ticks),
                    quantize6(result.payout_return_ratio),
                    quantize6(result.ev_ratio),
                    quantize6(result.fair_distance_pp),
                    result.status if result.reason_code is None else result.reason_code,
                )
            )
            + " |"
        )
    return "\n".join(lines)


def quote_evidence_markdown(results: Sequence[ContractProbeResult]) -> str:
    lines = [
        "| Símbolo | Contrato | Barreira | Duração | Ask | Payout | Proposal ID | "
        "Recebido monotônico |",
        "|---|---|---:|---:|---:|---:|---|---:|",
    ]
    for result in results:
        spec = result.spec
        lines.append(
            "| "
            + " | ".join(
                (
                    spec.broker_symbol,
                    spec.contract_type,
                    "—" if spec.barrier is None else str(spec.barrier),
                    str(spec.duration_ticks),
                    "—" if result.ask_price is None else str(result.ask_price),
                    "—" if result.payout is None else str(result.payout),
                    result.proposal_id or "—",
                    "—"
                    if result.received_monotonic is None
                    else quantize6(Decimal(str(result.received_monotonic))),
                )
            )
            + " |"
        )
    return "\n".join(lines)


def ranking_markdown(results: Sequence[ContractProbeResult]) -> str:
    accepted = [result for result in results if result.ev_ratio is not None]
    accepted.sort(key=lambda result: result.ev_ratio or Decimal("-999"), reverse=True)
    lines = [
        "| Rank | Símbolo | Contrato | Barreira | Duração | Payout | EV | Distância justo (pp) |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for index, result in enumerate(accepted, start=1):
        spec = result.spec
        lines.append(
            "| "
            + " | ".join(
                (
                    str(index),
                    spec.broker_symbol,
                    spec.contract_type,
                    "—" if spec.barrier is None else str(spec.barrier),
                    str(spec.duration_ticks),
                    quantize6(result.payout_return_ratio),
                    quantize6(result.ev_ratio),
                    quantize6(result.fair_distance_pp),
                )
            )
            + " |"
        )
    lines.append("| ref | todos | DIGITDIFF | 0 | 1 | 0.090000 | -0.019000 | -1.111111 |")
    return "\n".join(lines)


def summarize_probe(results: Sequence[ContractProbeResult]) -> dict[str, str]:
    ok = [result for result in results if result.payout_return_ratio is not None]
    by_contract: dict[tuple[str, int, int | None], set[Decimal]] = {}
    for result in ok:
        spec = result.spec
        key = (spec.contract_type, spec.duration_ticks, spec.barrier)
        by_contract.setdefault(key, set()).add(result.payout_return_ratio or Decimal("0"))
    constant_between_symbols = all(len(values) == 1 for values in by_contract.values())

    def ratios(contract: str, duration: int, barrier: int | None) -> set[Decimal]:
        return by_contract.get((contract, duration, barrier), set())

    even_odd_equal = ratios("DIGITEVEN", 1, None) == ratios("DIGITODD", 1, None)
    parity = ratios("DIGITEVEN", 1, None) | ratios("DIGITODD", 1, None)
    high_low = ratios("DIGITOVER", 1, 4) | ratios("DIGITUNDER", 1, 5)
    high_low_equal_parity = bool(parity) and parity == high_low

    rf_by_duration: dict[int, list[Decimal]] = {1: [], 5: [], 10: []}
    for result in ok:
        if result.spec.contract_type in {"CALL", "PUT"}:
            rf_by_duration[result.spec.duration_ticks].append(
                result.payout_return_ratio or Decimal("0")
            )
    rf_average = {
        duration: sum(values, Decimal("0")) / Decimal(len(values)) if values else None
        for duration, values in rf_by_duration.items()
    }
    best_ev = max((result.ev_ratio for result in ok if result.ev_ratio is not None), default=None)
    beats_digitdiff = best_ev is not None and best_ev > Decimal("-0.019000")
    return {
        "payout_constante_entre_simbolos": "sim" if constant_between_symbols else "não",
        "even_odd_iguais": "sim" if even_odd_equal else "não",
        "over4_under5_igual_even_odd": "sim" if high_low_equal_parity else "não",
        "rise_fall_1": quantize6(rf_average[1]),
        "rise_fall_5": quantize6(rf_average[5]),
        "rise_fall_10": quantize6(rf_average[10]),
        "algum_supera_digitdiff": "sim" if beats_digitdiff else "não",
        "melhor_ev": quantize6(best_ev),
    }


def build_markdown_report(results: Sequence[ContractProbeResult]) -> str:
    summary = summarize_probe(results)
    return "\n\n".join(
        (
            "## Sonda EV por contrato — Deriv public proposal read-only",
            f"Total planejado/executado: {len(results)} proposals read-only. Zero `buy`.",
            full_results_markdown(results),
            "### Ranking por EV",
            ranking_markdown(results),
            "### Respostas objetivas",
            "\n".join(f"- {key}: {value}" for key, value in summary.items()),
            "### Evidência de cotação",
            quote_evidence_markdown(results),
        )
    )


def _parse_symbols(value: str) -> tuple[str, ...]:
    symbols = tuple(item.strip() for item in value.split(",") if item.strip())
    if not symbols:
        raise argparse.ArgumentTypeError("at least one symbol is required")
    return symbols


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deriv contract EV proposal probe")
    parser.add_argument("--symbols", type=_parse_symbols, default=PROBE_SYMBOLS)
    parser.add_argument("--pause-seconds", type=Decimal, default=Decimal("0.15"))
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)

    symbols = args.symbols
    specs = build_contract_ev_probe_specs(symbols)
    if len(specs) > PROBE_BUDGET_PER_MINUTE:
        symbols = FALLBACK_PROBE_SYMBOLS
        specs = build_contract_ev_probe_specs(symbols)
    client = DerivWebSocketClient(PUBLIC_WS_URL, demo_authenticated=False)
    try:
        results = run_contract_ev_probe(
            client,
            specs=specs,
            pause=lambda: time.sleep(float(args.pause_seconds)),
            timeout_seconds=args.timeout_seconds,
        )
    finally:
        client.close()
    print(build_markdown_report(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
