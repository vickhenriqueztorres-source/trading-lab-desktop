from __future__ import annotations

from pathlib import Path

from packages.persistence.database import BUSY_TIMEOUT_MS, open_writer_connection


def test_writer_connection_enforces_durability_pragmas(tmp_path: Path) -> None:
    connection = open_writer_connection(tmp_path / "state.db")
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
    finally:
        connection.close()
