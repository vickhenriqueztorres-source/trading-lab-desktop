"""CLI for the local, read-only catalog capacity benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from apps.core.catalog_benchmark import DEFAULT_EPOCHS, DEFAULT_SCENARIOS, run_catalog_benchmark


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DualTrade local catalog benchmark; no broker access"
    )
    parser.add_argument(
        "--scenario",
        choices=[scenario.name for scenario in DEFAULT_SCENARIOS],
        action="append",
        help="cenário a executar; padrão executa todos",
    )
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--output", type=Path, help="arquivo JSON local opcional")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected_names = set(args.scenario or [scenario.name for scenario in DEFAULT_SCENARIOS])
    selected = [scenario for scenario in DEFAULT_SCENARIOS if scenario.name in selected_names]
    try:
        results = [run_catalog_benchmark(scenario, epochs=args.epochs) for scenario in selected]
    except ValueError as exc:
        print(json.dumps({"event": "catalog_benchmark_failed", "reason": str(exc)}))
        return 2
    payload = {
        "event": "catalog_benchmark_completed",
        "network_calls": 0,
        "financial_actions": 0,
        "results": [result.to_payload() for result in results],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if all(result.admitted for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
