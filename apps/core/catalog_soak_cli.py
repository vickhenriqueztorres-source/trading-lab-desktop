"""CLI for the local CAT-19 24h replay and wall-clock soak."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from apps.core.catalog_soak import (
    MINUTES_PER_DAY,
    catalog_soak_payload,
    run_catalog_fault_matrix,
    run_catalog_process_soak,
    run_catalog_replay,
)


def _decimal_seconds(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("wall seconds must be decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise argparse.ArgumentTypeError("wall seconds must be non-negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DualTrade CAT-19 local catalog soak; no broker access"
    )
    parser.add_argument("--simulated-minutes", type=int, default=MINUTES_PER_DAY)
    parser.add_argument("--wall-seconds", type=_decimal_seconds, default=Decimal(0))
    parser.add_argument("--batch-epochs", type=int, default=64)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        replay = run_catalog_replay(simulated_minutes=args.simulated_minutes)
        faults = run_catalog_fault_matrix()
        process_soak = run_catalog_process_soak(
            wall_seconds=args.wall_seconds,
            batch_epochs=args.batch_epochs,
        )
    except ValueError as exc:
        print(json.dumps({"event": "catalog_soak_failed", "reason": str(exc)}))
        return 2
    payload = catalog_soak_payload(replay, faults, process_soak)
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
