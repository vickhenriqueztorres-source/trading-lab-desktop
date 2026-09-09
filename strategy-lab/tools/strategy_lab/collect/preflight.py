"""Local collection prerequisites without network access (R-COL-1/2/13)."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from strategy_lab.collect.credentials import load_credentials
from strategy_lab.collect.recorded_canary import RecordedCanaryError, load_recorded_canary


def collection_preflight(*, canary_path: Path, now_ts: int) -> dict[str, object]:
    """Report missing setup, not broker readiness or research approval."""
    blockers: list[str] = []
    try:
        credential = load_credentials()
        del credential
    except Exception:
        blockers.append("IQ_COLLECTION_CREDENTIALS_UNAVAILABLE")
    try:
        db_url = urlsplit(os.environ.get("SUPABASE_DB_URL", ""))
        if db_url.scheme not in {"postgres", "postgresql"} or not db_url.hostname:
            blockers.append("SUPABASE_DB_URL_REQUIRED")
    except ValueError:
        blockers.append("SUPABASE_DB_URL_REQUIRED")
    try:
        load_recorded_canary(canary_path, now_ts=now_ts)
    except RecordedCanaryError as exc:
        blockers.append(str(exc))
    return {
        "event": "strategy_lab_collection_preflight",
        "status": "blocked" if blockers else "local_prerequisites_ready",
        "blockers": blockers,
        "network_checked": False,
        "broker_authenticated": False,
        "research_ready": False,
    }
