"""P05 backup wrapper tests (R-OPS-1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from strategy_lab.collect.backup import BackupError, _pg_env, latest_backup_age_days, run_backup


def test_pg_env_parses_without_logging_password() -> None:
    """R-OPS-1/I-8: DB URL is converted to env vars for pg_dump, not CLI args."""
    result = _pg_env("postgresql://user:pass%3F@db.example.test:5432/postgres")
    assert result["PGHOST"] == "db.example.test"
    assert result["PGUSER"] == "user"
    assert result["PGPASSWORD"] == "pass?"


def test_backup_requires_external_tools(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """R-OPS-1: backup fails closed when pg_dump/age is not installed."""
    monkeypatch.setattr("strategy_lab.collect.backup.shutil.which", lambda name: None)
    with pytest.raises(BackupError, match="PG_DUMP_UNAVAILABLE"):
        run_backup(
            db_url="postgresql://user:pass@db.example.test/postgres",
            age_recipient="age1example",
            output_dir=tmp_path,
            now=datetime(2026, 9, 2, tzinfo=UTC),
        )


def test_latest_backup_age_alert_boundary(tmp_path) -> None:
    """R-OPS-1: status can warn when encrypted backup is older than 8 days."""
    backup = tmp_path / "20260902.sql.gz.age"
    backup.write_text("encrypted", encoding="utf-8")
    now_ts = int(datetime(2026, 9, 10, tzinfo=UTC).timestamp())
    old_ts = int(datetime(2026, 9, 2, tzinfo=UTC).timestamp())
    backup.touch()
    import os

    os.utime(backup, (old_ts, old_ts))
    assert latest_backup_age_days(tmp_path, now_ts=now_ts) == 8
