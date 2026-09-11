"""Opt-in, single-session Practice payout diagnostic; never a trading route."""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.core.iqoption_connection_safety import (
    IQOptionConnectionSafetyController,
    IQOptionConnectionSafetyStore,
)
from apps.launcher.instance import LauncherInstanceGuard
from packages.brokers.iqoption.community_read_only import (
    IQOPTION_ACTIVE_IDS,
    IQOptionAccountMode,
    IQOptionCommunityReadOnlySession,
)
from packages.brokers.iqoption.credentials import IQOptionCredentialVault
from packages.protocol.errors import ProtocolErrorCode


def describe(response: Mapping[str, Any], symbol: str) -> dict[str, object]:
    """Only shape/type/identity evidence; never raw account or authentication data."""
    result: dict[str, object] = {}
    msg = response.get("msg")
    turbo = msg.get("turbo") if isinstance(msg, Mapping) else None
    actives = turbo.get("actives") if isinstance(turbo, Mapping) else None
    if isinstance(actives, Mapping):
        result["matching_ids"] = [
            str(key)
            for key, value in actives.items()
            if str(key).isdigit()
            and isinstance(value, Mapping)
            and value.get("name") == "front." + symbol
        ]
    node: Any = response
    for field in ("msg", "turbo", "actives", str(IQOPTION_ACTIVE_IDS[symbol])):
        node = node.get(field) if isinstance(node, Mapping) else None
        result[field + "_type"] = type(node).__name__
    if isinstance(node, Mapping):
        result["exact_name"] = node.get("name") == "front." + symbol
        for field in ("enabled", "is_suspended"):
            value = node.get(field)
            result[field + "_type"] = type(value).__name__
            if type(value) in (bool, int) and value in (False, True):
                result[field + "_value"] = value
        option = node.get("option")
        result["option_type"] = type(option).__name__
        profit = option.get("profit") if isinstance(option, Mapping) else None
        result["profit_type"] = type(profit).__name__
        commission = profit.get("commission") if isinstance(profit, Mapping) else None
        result["commission_type"] = type(commission).__name__
    return result


class ProbeSession(IQOptionCommunityReadOnlySession):
    symbol: str
    evidence: dict[str, object]

    def _send(self, payload: Mapping[str, object]) -> None:
        name = payload.get("name")
        allowed = name in {"authenticate", "timesync", "heartbeat"}
        if name == "sendMessage":
            body = payload.get("msg")
            allowed = isinstance(body, Mapping) and body.get("name") in {
                "get-profile",
                "get-balances",
                "get-initialization-data",
            }
        if not allowed:
            raise RuntimeError("PROBE_WRITE_FORBIDDEN")
        super()._send(payload)

    def _request_initialization(self, timeout: float) -> dict[str, Any]:
        started = time.monotonic()
        response = super()._request_initialization(timeout)
        self.evidence = describe(response, self.symbol)
        self.evidence["supported_assets"] = {
            asset: describe(response, asset) for asset in IQOPTION_ACTIVE_IDS
        }
        self.evidence["request_elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--symbol", choices=tuple(IQOPTION_ACTIVE_IDS), required=True)
    parser.add_argument("--run-external-read-only", action="store_true", required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    guard = LauncherInstanceGuard(args.profile_dir)
    session: ProbeSession | None = None
    controller: IQOptionConnectionSafetyController | None = None
    report: dict[str, object] = {
        "collected_at": datetime.now(UTC).isoformat(),
        "symbol": args.symbol,
        "mode": "PRACTICE",
        "financial_messages": 0,
    }
    try:
        guard.acquire()  # Never compete with the running operator session.
        credentials = IQOptionCredentialVault(args.profile_dir / "broker_credentials").load()
        if credentials is None or credentials.account_mode != "practice":
            raise RuntimeError("PROBE_SAVED_PRACTICE_REQUIRED")
        controller = IQOptionConnectionSafetyController(
            IQOptionConnectionSafetyStore(args.profile_dir / "core")
        )
        admission = controller.admit_http_login(source="manual")
        if not admission.allowed:
            raise RuntimeError(admission.reason_code)
        session = ProbeSession(
            credentials.email, credentials.password, IQOptionAccountMode.PRACTICE
        )
        session.symbol, session.evidence = args.symbol, {}
        session.connect()
        controller.record_success()
        report["payout"] = str(session.get_binary_payout(args.symbol))
        report["result"] = "OK"
    except Exception as exc:
        raw_reason = getattr(exc, "reason_code", str(exc) if isinstance(exc, RuntimeError) else "")
        try:
            reason = ProtocolErrorCode(raw_reason).value
        except ValueError:
            reason = "PROBE_FAILED"
        report["result"] = reason
        report["error_type"] = type(exc).__name__
        if controller is not None and raw_reason:
            controller.record_failure(reason)
    finally:
        if session is not None:
            report["evidence"] = session.evidence
            session.close()
        guard.release()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["result"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
