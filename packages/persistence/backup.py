from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from packages.observability.events import EventSink, NullEventSink
from packages.persistence.database import IntegrityCheckMode, verify_database_integrity
from packages.persistence.writer import SingleDatabaseWriter


class BackupError(RuntimeError):
    pass


class DatabaseBackupService:
    """Create a committed SQLite snapshot without copying live WAL files."""

    def __init__(
        self,
        writer: SingleDatabaseWriter,
        event_sink: EventSink | None = None,
    ) -> None:
        self._writer = writer
        self._event_sink = event_sink or NullEventSink()

    def create_backup(self, destination: Path) -> Path:
        source = self._writer.path.resolve()
        resolved_destination = destination.resolve()
        if resolved_destination == source:
            raise BackupError("backup destination must differ from state.db")
        if destination.exists():
            raise BackupError("backup destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.{uuid4().hex}.partial")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(temporary, isolation_level=None)
            connection.execute("PRAGMA foreign_keys = ON")
            self._writer.backup_to_connection(connection)
            verify_database_integrity(connection, IntegrityCheckMode.FULL)
            connection.close()
            connection = None
            os.replace(temporary, destination)
        except Exception as exc:
            if connection is not None:
                connection.close()
            temporary.unlink(missing_ok=True)
            if isinstance(exc, BackupError):
                raise
            raise BackupError("consistent SQLite backup failed") from exc
        self._event_sink.emit("database_backup_created", check_mode="integrity_check")
        return destination
