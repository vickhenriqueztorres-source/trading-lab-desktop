import json
from pathlib import Path

from tools.chaos_iqoption_network import validate


def test_chaos_log_gate_accepts_cached_recovery_without_quarantine(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    records = (
        {"event": "iqoption_http_login", "source": "manual"},
        {"event": "iqoption_recovery_connected", "reason_code": "EXECUTION_INTENT_PRESERVED"},
    )
    log_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    assert validate(log_path) == (True, ())


def test_chaos_log_gate_rejects_login_storm_and_quarantine(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    records = (
        {"event": "iqoption_http_login"},
        {"event": "iqoption_http_login"},
        {
            "event": "iqoption_connection_quarantine",
            "reason_code": "IQOPTION_HTTP_LOGIN_LIMIT_REACHED",
        },
    )
    log_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    passed, failures = validate(log_path)
    assert not passed
    assert len(failures) == 5
