from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

BUSY_TIMEOUT_MS = 5_000
STORAGE_MARKER_SUFFIX = ".expected"


class DatabaseIntegrityError(RuntimeError):
    pass


class DatabaseMissingError(RuntimeError):
    pass


class IntegrityCheckMode(StrEnum):
    QUICK = "quick_check"
    FULL = "integrity_check"


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    mode: IntegrityCheckMode
    messages: tuple[str, ...]

    @property
    def is_healthy(self) -> bool:
        return self.messages == ("ok",)


def storage_marker_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{STORAGE_MARKER_SUFFIX}")


def ensure_database_presence_is_safe(path: Path) -> bool:
    """Return True for a legitimate first run, fail if an expected DB disappeared."""
    database_exists = path.exists()
    marker_exists = storage_marker_path(path).exists()
    if marker_exists and not database_exists:
        raise DatabaseMissingError("expected critical database is missing")
    return not database_exists and not marker_exists


def mark_database_expected(path: Path) -> None:
    marker = storage_marker_path(path)
    if marker.exists():
        return
    temporary = marker.with_name(f"{marker.name}.{uuid4().hex}.tmp")
    temporary.write_text("DUALTRADE_STATE_DB_V1\n", encoding="ascii")
    os.replace(temporary, marker)


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_MS / 1_000,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def configure_writer_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")


def verify_database_integrity(
    connection: sqlite3.Connection,
    mode: IntegrityCheckMode = IntegrityCheckMode.QUICK,
) -> IntegrityReport:
    """Use quick_check at startup; reserve the full check for diagnostics/backups."""
    try:
        rows = connection.execute(f"PRAGMA {mode.value}").fetchall()
    except sqlite3.DatabaseError as exc:
        raise DatabaseIntegrityError("SQLite could not verify database integrity") from exc
    report = IntegrityReport(mode=mode, messages=tuple(str(row[0]) for row in rows))
    if not report.is_healthy:
        raise DatabaseIntegrityError("SQLite reported database integrity errors")
    return report


def open_writer_connection(path: Path) -> sqlite3.Connection:
    """Open the critical store with conservative local durability settings.

    WAL lets read projections proceed while the Core writer commits. FULL asks SQLite
    to synchronize WAL commits before reporting success. A bounded five-second busy
    timeout absorbs short reader/checkpoint contention without hiding an unavailable DB.
    """
    connection = connect_database(path)
    configure_writer_connection(connection)
    return connection


def open_reader_connection(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(
        uri,
        timeout=BUSY_TIMEOUT_MS / 1_000,
        isolation_level=None,
        check_same_thread=False,
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA query_only = ON")
    return connection
