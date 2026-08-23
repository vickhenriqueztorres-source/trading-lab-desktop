from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


class MigrationError(RuntimeError):
    reason_code = "DB_MIGRATION_FAILED"


class MigrationChecksumMismatch(MigrationError):
    reason_code = "MIGRATION_CHECKSUM_MISMATCH"


class UnsupportedMigrationError(MigrationError):
    reason_code = "MIGRATION_UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        source = "\n".join(self.statements).encode("utf-8")
        return hashlib.sha256(source).hexdigest()


INITIAL_STATE = Migration(
    version=1,
    name="0001_initial_state",
    statements=(
        """
        CREATE TABLE trade_intents (
            intent_id TEXT PRIMARY KEY,
            correlation_id TEXT NOT NULL,
            broker TEXT NOT NULL,
            account_id TEXT NOT NULL,
            product TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
            currency TEXT NOT NULL CHECK (length(currency) = 3),
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE risk_reservations (
            reservation_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL UNIQUE REFERENCES trade_intents(intent_id),
            broker TEXT NOT NULL,
            account_id TEXT NOT NULL,
            amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
            currency TEXT NOT NULL CHECK (length(currency) = 3),
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            released_at TEXT,
            release_reason TEXT,
            reconciliation_evidence TEXT
        )
        """,
        """
        CREATE UNIQUE INDEX uq_active_reservation_per_account
        ON risk_reservations(broker, account_id)
        WHERE state = 'ACTIVE'
        """,
        """
        CREATE TABLE outbox_messages (
            message_id TEXT PRIMARY KEY,
            correlation_id TEXT NOT NULL,
            intent_id TEXT NOT NULL UNIQUE REFERENCES trade_intents(intent_id),
            message_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            dispatch_started_at TEXT,
            dispatched_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0)
        )
        """,
        """
        CREATE INDEX ix_outbox_dispatch
        ON outbox_messages(state, available_at, created_at)
        """,
        """
        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL UNIQUE REFERENCES trade_intents(intent_id),
            broker TEXT NOT NULL,
            account_id TEXT NOT NULL,
            broker_order_id TEXT,
            state TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            realized_pnl_minor INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE processed_order_events (
            event_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL REFERENCES trade_intents(intent_id),
            new_state TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            processed_at TEXT NOT NULL
        )
        """,
    ),
)

OUTBOX_STATE_REASON = Migration(
    version=2,
    name="0002_outbox_state_reason",
    statements=("ALTER TABLE outbox_messages ADD COLUMN state_reason TEXT",),
)

RECONCILIATION = Migration(
    version=3,
    name="0003_reconciliation",
    statements=(
        """
        CREATE TABLE reconciliation_evidence (
            evidence_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            client_order_ref TEXT NOT NULL,
            broker_order_id TEXT,
            external_status TEXT NOT NULL,
            broker TEXT NOT NULL,
            account_id TEXT NOT NULL,
            product TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
            currency TEXT NOT NULL CHECK (length(currency) = 3),
            realized_pnl_minor INTEGER,
            raw_reference_hash TEXT,
            evidence_version INTEGER NOT NULL CHECK (evidence_version > 0),
            canonical_hash TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX ix_reconciliation_evidence_order
        ON reconciliation_evidence(order_id, observed_at)
        """,
        """
        CREATE TABLE reconciliation_attempts (
            attempt_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(order_id),
            correlation_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            result TEXT NOT NULL,
            reason_code TEXT,
            evidence_id TEXT REFERENCES reconciliation_evidence(evidence_id)
        )
        """,
        """
        CREATE INDEX ix_reconciliation_attempts_order
        ON reconciliation_attempts(order_id, started_at)
        """,
        "ALTER TABLE orders ADD COLUMN resolution_source TEXT",
        """
        ALTER TABLE orders ADD COLUMN resolution_evidence_id TEXT
        REFERENCES reconciliation_evidence(evidence_id)
        """,
        "ALTER TABLE orders ADD COLUMN resolved_at TEXT",
    ),
)

BROKER_ORDER_EVENTS = Migration(
    version=4,
    name="0004_broker_order_events",
    statements=(
        """
        CREATE TABLE broker_order_events (
            event_id TEXT PRIMARY KEY,
            order_id TEXT REFERENCES orders(order_id),
            event_version INTEGER NOT NULL CHECK (event_version > 0),
            broker TEXT NOT NULL,
            account_id TEXT NOT NULL,
            client_order_ref TEXT NOT NULL,
            broker_order_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            external_sequence INTEGER CHECK (external_sequence > 0),
            external_status TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            product TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
            currency TEXT NOT NULL CHECK (length(currency) = 3),
            result_minor INTEGER,
            result_currency TEXT,
            evidence_hash TEXT NOT NULL,
            processing_result TEXT NOT NULL,
            reason_code TEXT,
            conflict_count INTEGER NOT NULL DEFAULT 0 CHECK (conflict_count >= 0),
            last_conflicting_hash TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX ix_broker_order_events_order
        ON broker_order_events(order_id, occurred_at)
        """,
        """
        CREATE INDEX ix_broker_order_event_sequence
        ON broker_order_events(order_id, external_sequence)
        WHERE order_id IS NOT NULL AND external_sequence IS NOT NULL
        """,
        "ALTER TABLE orders ADD COLUMN last_external_sequence INTEGER",
        "ALTER TABLE orders ADD COLUMN last_broker_event_id TEXT",
        """
        ALTER TABLE orders ADD COLUMN pnl_application_count INTEGER NOT NULL DEFAULT 0
        CHECK (pnl_application_count >= 0)
        """,
        """
        ALTER TABLE risk_reservations ADD COLUMN release_count INTEGER NOT NULL DEFAULT 0
        CHECK (release_count >= 0)
        """,
        """
        UPDATE orders
        SET pnl_application_count = 1
        WHERE state = 'SETTLED' AND realized_pnl_minor IS NOT NULL
        """,
        """
        UPDATE risk_reservations
        SET release_count = 1
        WHERE state = 'RELEASED'
        """,
    ),
)

MIGRATIONS = (
    INITIAL_STATE,
    OUTBOX_STATE_REASON,
    RECONCILIATION,
    BROKER_ORDER_EVENTS,
)


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: tuple[Migration, ...] = MIGRATIONS,
) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                applied_at TEXT NOT NULL,
                checksum TEXT NOT NULL
            )
            """
        )
        connection.execute("COMMIT")

        applied = {
            row["version"]: row
            for row in connection.execute(
                "SELECT version, name, checksum FROM schema_migrations"
            ).fetchall()
        }
        known_versions = {migration.version for migration in migrations}
        unexpected = set(applied) - known_versions
        if unexpected:
            raise UnsupportedMigrationError(
                f"database has unsupported migrations: {sorted(unexpected)}"
            )

        for migration in migrations:
            existing = applied.get(migration.version)
            if existing is not None:
                if existing["name"] != migration.name or existing["checksum"] != migration.checksum:
                    raise MigrationChecksumMismatch(
                        f"published migration {migration.version} does not match its checksum"
                    )
                continue
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, name, applied_at, checksum)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        migration.version,
                        migration.name,
                        datetime.now(UTC).isoformat(),
                        migration.checksum,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
    except MigrationError:
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise MigrationError("failed to apply database migrations") from exc
