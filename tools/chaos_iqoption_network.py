"""Validate the structured log produced by the manual IQ Option network drill.

This tool deliberately doesn't toggle the host network or terminate processes.
Run the documented Wi-Fi/sleep/worker-kill sequence against the packaged EXE,
then pass its JSONL operational log here as the merge-gate assertion step.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from pathlib import Path


def _records(path: Path) -> Iterable[Mapping[str, object]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            candidate = line.strip()
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}") from exc
            if isinstance(payload, Mapping):
                yield payload


def validate(path: Path) -> tuple[bool, tuple[str, ...]]:
    records = tuple(_records(path))
    failures: list[str] = []
    http_logins = sum(
        str(item.get("event", "")).lower() == "iqoption_http_login" for item in records
    )
    limit_hits = sum(
        item.get("reason_code") == "IQOPTION_HTTP_LOGIN_LIMIT_REACHED" for item in records
    )
    quarantines = sum(item.get("event") == "iqoption_connection_quarantine" for item in records)
    connected = any(item.get("event") == "iqoption_recovery_connected" for item in records)
    intent_preserved = any(
        item.get("reason_code") == "EXECUTION_INTENT_PRESERVED" for item in records
    )
    if http_logins > 1:
        failures.append(f"IQOPTION_HTTP_LOGIN={http_logins}, expected <= 1")
    if limit_hits:
        failures.append(f"IQOPTION_HTTP_LOGIN_LIMIT_REACHED={limit_hits}, expected 0")
    if quarantines:
        failures.append(f"IQOPTION_CONNECTION_QUARANTINE={quarantines}, expected 0")
    if not connected:
        failures.append("final recovery connection evidence missing")
    if not intent_preserved:
        failures.append("armed_intent preservation evidence missing")
    return not failures, tuple(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="JSONL operational log exported after the drill")
    arguments = parser.parse_args()
    passed, failures = validate(arguments.log)
    if passed:
        print("PASS: HTTP login <= 1, no quarantine, connected with execution intent preserved")
        return 0
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
