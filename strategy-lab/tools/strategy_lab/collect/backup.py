"""Encrypted pg_dump backup wrapper (R-OPS-1)."""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse


class BackupError(RuntimeError):
    pass


def backup_dir_default() -> Path:
    return Path.home() / "strategy-lab" / "backups"


def latest_backup_age_days(directory: Path, *, now_ts: int) -> int | None:
    backups = list(directory.glob("*.sql.gz.age"))
    if not backups:
        return None
    newest = max(int(path.stat().st_mtime) for path in backups)
    return (now_ts - newest) // 86400


def run_backup(
    *,
    db_url: str,
    age_recipient: str,
    output_dir: Path | None = None,
    now: datetime | None = None,
) -> Path:
    if not db_url or not age_recipient:
        raise BackupError("BACKUP_CONFIG_REQUIRED")
    if shutil.which("pg_dump") is None:
        raise BackupError("PG_DUMP_UNAVAILABLE")
    if shutil.which("age") is None:
        raise BackupError("AGE_UNAVAILABLE")
    target_dir = output_dir or backup_dir_default()
    target_dir.mkdir(parents=True, exist_ok=True)
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise BackupError("BACKUP_TIME_INVALID")
    target = target_dir / f"{instant.astimezone(UTC).strftime('%Y%m%d')}.sql.gz.age"
    if target.exists():
        raise BackupError("BACKUP_EXISTS")
    pg_env = _pg_env(db_url)
    with (
        subprocess.Popen(
            ["pg_dump", "--no-owner", "--no-privileges"],
            env={**os.environ, **pg_env},
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as dump,
        subprocess.Popen(
            ["age", "-r", age_recipient, "-o", str(target)],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as age,
    ):
        if dump.stdout is None or age.stdin is None:
            raise BackupError("BACKUP_PIPE_FAILED")
        with gzip.GzipFile(fileobj=age.stdin, mode="wb") as zipped:
            shutil.copyfileobj(dump.stdout, zipped)
        dump.stdout.close()
        dump_code = dump.wait(timeout=300)
        age_code = age.wait(timeout=300)
    if dump_code or age_code:
        target.unlink(missing_ok=True)
        raise BackupError("BACKUP_FAILED")
    return target


def _pg_env(db_url: str) -> dict[str, str]:
    parsed = urlparse(db_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise BackupError("BACKUP_DB_URL_INVALID")
    return {
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGDATABASE": parsed.path.lstrip("/") or "postgres",
        "PGUSER": unquote(parsed.username or "postgres"),
        "PGPASSWORD": unquote(parsed.password or ""),
    }
