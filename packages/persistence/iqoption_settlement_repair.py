from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from packages.domain.models import Broker, ExternalOrderStatus, ReconciliationEvidence

REPAIR_PREDICATE_VERSION = 1
REPAIR_DETECTED_REASON = "IQOPTION_STATUS_QUERY_ZERO_PNL"
REPAIR_RESOLUTION_SOURCE = "AUDITED_SETTLEMENT_REPAIR"


class SettlementRepairOperationStatus(StrEnum):
    CREATED = "CREATED"
    VERIFIED = "VERIFIED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    COMPLETED_NO_CHANGE = "COMPLETED_NO_CHANGE"
    UNRESOLVED = "UNRESOLVED"
    BLOCKED = "BLOCKED"
    IDEMPOTENT = "IDEMPOTENT"


@dataclass(frozen=True, slots=True)
class SettlementRepairResult:
    status: SettlementRepairOperationStatus
    batch_id: str
    affected_count: int
    reason_code: str | None = None


def discover_zero_pnl_status_query_settlements(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    actor: str,
    request_reason: str,
    discovered_at: datetime,
    max_candidates: int,
) -> SettlementRepairResult:
    existing = connection.execute(
        "SELECT * FROM iqoption_settlement_repair_batches WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    if existing is not None:
        if (
            int(existing["predicate_version"]) == REPAIR_PREDICATE_VERSION
            and str(existing["requested_by"]) == actor
            and str(existing["request_reason"]) == request_reason
        ):
            return SettlementRepairResult(
                SettlementRepairOperationStatus.IDEMPOTENT,
                batch_id,
                int(existing["candidate_count"]),
            )
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            batch_id,
            0,
            "REPAIR_BATCH_ID_CONFLICT",
        )

    candidates = connection.execute(
        """
        SELECT o.order_id, o.intent_id, o.state, o.realized_pnl_minor,
               o.resolution_source, o.resolution_evidence_id, o.resolved_at,
               o.updated_at, o.broker_order_id, o.account_id, o.pnl_application_count,
               ti.product, ti.symbol, ti.direction, ti.amount_minor, ti.currency,
               re.order_id AS evidence_order_id, re.source AS evidence_source,
               re.external_status AS evidence_status,
               re.realized_pnl_minor AS evidence_pnl_minor
        FROM orders o
        JOIN trade_intents ti ON ti.intent_id = o.intent_id
        LEFT JOIN reconciliation_evidence re
          ON re.evidence_id = o.resolution_evidence_id
        WHERE o.broker = 'IQ_OPTION'
          AND o.state = 'SETTLED'
          AND o.realized_pnl_minor = 0
          AND o.resolution_source = 'STATUS_QUERY'
        ORDER BY o.created_at, o.order_id
        LIMIT ?
        """,
        (max_candidates + 1,),
    ).fetchall()
    if len(candidates) > max_candidates:
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            batch_id,
            0,
            "REPAIR_CANDIDATE_LIMIT_EXCEEDED",
        )

    connection.execute(
        """
        INSERT INTO iqoption_settlement_repair_batches(
            batch_id, predicate_version, state, requested_by, request_reason,
            discovered_at, candidate_count
        ) VALUES (?, ?, 'DISCOVERED', ?, ?, ?, ?)
        """,
        (
            batch_id,
            REPAIR_PREDICATE_VERSION,
            actor,
            request_reason,
            discovered_at.isoformat(),
            len(candidates),
        ),
    )
    for row in candidates:
        original_fingerprint = _order_fingerprint(row)
        repair_id = _repair_id(str(row["order_id"]), row["resolution_evidence_id"])
        evidence_conflict = _original_evidence_conflict(row)
        state = "CONFLICT" if evidence_conflict is not None else "PENDING_EVIDENCE"
        connection.execute(
            """
            INSERT INTO iqoption_settlement_repairs(
                repair_id, batch_id, order_id, state, detected_reason,
                original_state, original_pnl_minor, original_resolution_source,
                original_resolution_evidence_id, original_resolved_at,
                original_updated_at, original_fingerprint, last_reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repair_id,
                batch_id,
                row["order_id"],
                state,
                REPAIR_DETECTED_REASON,
                row["state"],
                row["realized_pnl_minor"],
                row["resolution_source"],
                row["resolution_evidence_id"],
                row["resolved_at"],
                row["updated_at"],
                original_fingerprint,
                evidence_conflict,
            ),
        )
        _append_event(
            connection,
            repair_id=repair_id,
            event_type="CANDIDATE_DISCOVERED",
            previous_state=None,
            new_state=state,
            actor=actor,
            reason_code=evidence_conflict or REPAIR_DETECTED_REASON,
            evidence_id=row["resolution_evidence_id"],
            previous_pnl_minor=0,
            proposed_pnl_minor=None,
            occurred_at=discovered_at,
        )
    return SettlementRepairResult(
        SettlementRepairOperationStatus.CREATED,
        batch_id,
        len(candidates),
    )


def record_settlement_verification(
    connection: sqlite3.Connection,
    *,
    repair_id: str,
    evidence: ReconciliationEvidence,
    actor: str,
    verified_at: datetime,
) -> SettlementRepairResult:
    row = _repair_projection(connection, repair_id)
    if row is None:
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            "",
            0,
            "REPAIR_NOT_FOUND",
        )
    batch_id = str(row["batch_id"])
    evidence_hash = _evidence_hash(evidence)
    existing_evidence = connection.execute(
        "SELECT order_id, canonical_hash FROM reconciliation_evidence WHERE evidence_id = ?",
        (evidence.evidence_id,),
    ).fetchone()
    if row["verification_evidence_id"] == evidence.evidence_id:
        if (
            existing_evidence is not None
            and existing_evidence["order_id"] == row["order_id"]
            and existing_evidence["canonical_hash"] == evidence_hash
        ):
            return SettlementRepairResult(
                SettlementRepairOperationStatus.IDEMPOTENT,
                batch_id,
                0,
            )
        return _conflict(
            connection,
            row,
            actor,
            verified_at,
            "REPAIR_EVIDENCE_ID_CONFLICT",
        )
    if row["state"] not in {"PENDING_EVIDENCE", "UNRESOLVED"}:
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            batch_id,
            0,
            "REPAIR_STATE_NOT_VERIFIABLE",
        )

    current_fingerprint = _order_fingerprint(row)
    if current_fingerprint != row["original_fingerprint"]:
        return _conflict(
            connection,
            row,
            actor,
            verified_at,
            "REPAIR_ORIGINAL_ORDER_CHANGED",
        )
    mismatch = _verification_conflict(row, evidence)
    if mismatch is not None:
        return _conflict(connection, row, actor, verified_at, mismatch)
    if existing_evidence is not None:
        if (
            existing_evidence["order_id"] != row["order_id"]
            or existing_evidence["canonical_hash"] != evidence_hash
        ):
            return _conflict(
                connection,
                row,
                actor,
                verified_at,
                "REPAIR_EVIDENCE_ID_CONFLICT",
            )
    else:
        _insert_reconciliation_evidence(
            connection,
            str(row["order_id"]),
            evidence,
            evidence_hash,
            verified_at,
        )

    proposed = evidence.realized_pnl_minor
    if proposed is None:
        raise AssertionError("validated settlement evidence has no P&L")
    next_state = (
        "CONFIRMED_NO_CHANGE" if proposed == row["original_pnl_minor"] else ("READY_FOR_APPROVAL")
    )
    connection.execute(
        """
        UPDATE iqoption_settlement_repairs
        SET state = ?, verification_evidence_id = ?, proposed_pnl_minor = ?,
            verified_at = ?, last_reason_code = NULL
        WHERE repair_id = ?
        """,
        (next_state, evidence.evidence_id, proposed, verified_at.isoformat(), repair_id),
    )
    connection.execute(
        """
        UPDATE iqoption_settlement_repair_batches
        SET state = 'DISCOVERED', last_reason_code = NULL
        WHERE batch_id = ? AND state = 'BLOCKED'
        """,
        (batch_id,),
    )
    _append_event(
        connection,
        repair_id=repair_id,
        event_type="EVIDENCE_VERIFIED",
        previous_state=str(row["state"]),
        new_state=next_state,
        actor=actor,
        reason_code=("REPAIR_CONFIRMED_NO_CHANGE" if proposed == 0 else None),
        evidence_id=evidence.evidence_id,
        previous_pnl_minor=int(row["original_pnl_minor"]),
        proposed_pnl_minor=proposed,
        occurred_at=verified_at,
    )
    status = (
        SettlementRepairOperationStatus.COMPLETED_NO_CHANGE
        if next_state == "CONFIRMED_NO_CHANGE"
        else SettlementRepairOperationStatus.VERIFIED
    )
    return SettlementRepairResult(status, batch_id, 1)


def mark_settlement_verification_unresolved(
    connection: sqlite3.Connection,
    *,
    repair_id: str,
    actor: str,
    reason_code: str,
    occurred_at: datetime,
) -> SettlementRepairResult:
    row = _repair_projection(connection, repair_id)
    if row is None:
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            "",
            0,
            "REPAIR_NOT_FOUND",
        )
    batch_id = str(row["batch_id"])
    if row["state"] == "UNRESOLVED" and row["last_reason_code"] == reason_code:
        return SettlementRepairResult(SettlementRepairOperationStatus.IDEMPOTENT, batch_id, 0)
    if row["state"] not in {"PENDING_EVIDENCE", "UNRESOLVED"}:
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            batch_id,
            0,
            "REPAIR_STATE_NOT_VERIFIABLE",
        )
    connection.execute(
        """
        UPDATE iqoption_settlement_repairs
        SET state = 'UNRESOLVED', last_reason_code = ?
        WHERE repair_id = ?
        """,
        (reason_code, repair_id),
    )
    _append_event(
        connection,
        repair_id=repair_id,
        event_type="EVIDENCE_UNAVAILABLE",
        previous_state=str(row["state"]),
        new_state="UNRESOLVED",
        actor=actor,
        reason_code=reason_code,
        evidence_id=None,
        previous_pnl_minor=int(row["original_pnl_minor"]),
        proposed_pnl_minor=None,
        occurred_at=occurred_at,
    )
    return SettlementRepairResult(
        SettlementRepairOperationStatus.UNRESOLVED,
        batch_id,
        1,
        reason_code,
    )


def approve_settlement_repair_batch(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    actor: str,
    approval_reason: str,
    approved_at: datetime,
) -> SettlementRepairResult:
    batch = connection.execute(
        "SELECT * FROM iqoption_settlement_repair_batches WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    if batch is None:
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            batch_id,
            0,
            "REPAIR_BATCH_NOT_FOUND",
        )
    if batch["state"] in {"APPROVED", "APPLIED", "COMPLETED_NO_CHANGE"}:
        return SettlementRepairResult(
            SettlementRepairOperationStatus.IDEMPOTENT,
            batch_id,
            0,
        )
    repairs = connection.execute(
        """
        SELECT * FROM iqoption_settlement_repairs
        WHERE batch_id = ? ORDER BY order_id
        """,
        (batch_id,),
    ).fetchall()
    allowed = {"READY_FOR_APPROVAL", "CONFIRMED_NO_CHANGE"}
    if not repairs or any(str(item["state"]) not in allowed for item in repairs):
        connection.execute(
            """
            UPDATE iqoption_settlement_repair_batches
            SET state = 'BLOCKED', last_reason_code = 'REPAIR_EVIDENCE_INCOMPLETE'
            WHERE batch_id = ?
            """,
            (batch_id,),
        )
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            batch_id,
            0,
            "REPAIR_EVIDENCE_INCOMPLETE",
        )
    ready = [item for item in repairs if item["state"] == "READY_FOR_APPROVAL"]
    if not ready:
        connection.execute(
            """
            UPDATE iqoption_settlement_repair_batches
            SET state = 'COMPLETED_NO_CHANGE', approved_by = ?, approval_reason = ?,
                approved_at = ?, last_reason_code = NULL
            WHERE batch_id = ?
            """,
            (actor, approval_reason, approved_at.isoformat(), batch_id),
        )
        return SettlementRepairResult(
            SettlementRepairOperationStatus.COMPLETED_NO_CHANGE,
            batch_id,
            len(repairs),
        )
    for item in ready:
        connection.execute(
            "UPDATE iqoption_settlement_repairs SET state = 'APPROVED' WHERE repair_id = ?",
            (item["repair_id"],),
        )
        _append_event(
            connection,
            repair_id=str(item["repair_id"]),
            event_type="CORRECTION_APPROVED",
            previous_state="READY_FOR_APPROVAL",
            new_state="APPROVED",
            actor=actor,
            reason_code="REPAIR_EXPLICITLY_APPROVED",
            evidence_id=item["verification_evidence_id"],
            previous_pnl_minor=int(item["original_pnl_minor"]),
            proposed_pnl_minor=int(item["proposed_pnl_minor"]),
            occurred_at=approved_at,
        )
    connection.execute(
        """
        UPDATE iqoption_settlement_repair_batches
        SET state = 'APPROVED', approved_by = ?, approval_reason = ?, approved_at = ?,
            last_reason_code = NULL
        WHERE batch_id = ?
        """,
        (actor, approval_reason, approved_at.isoformat(), batch_id),
    )
    return SettlementRepairResult(
        SettlementRepairOperationStatus.APPROVED,
        batch_id,
        len(ready),
    )


def apply_settlement_repair_batch(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    actor: str,
    applied_at: datetime,
) -> SettlementRepairResult:
    batch = connection.execute(
        "SELECT * FROM iqoption_settlement_repair_batches WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    if batch is None:
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            batch_id,
            0,
            "REPAIR_BATCH_NOT_FOUND",
        )
    if batch["state"] == "APPLIED":
        applied = connection.execute(
            """
            SELECT COUNT(*) AS total FROM iqoption_settlement_repairs
            WHERE batch_id = ? AND state = 'APPLIED'
            """,
            (batch_id,),
        ).fetchone()
        return SettlementRepairResult(
            SettlementRepairOperationStatus.IDEMPOTENT,
            batch_id,
            int(applied["total"]),
        )
    if batch["state"] != "APPROVED":
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            batch_id,
            0,
            "REPAIR_BATCH_NOT_APPROVED",
        )
    repairs = connection.execute(
        """
        SELECT r.*, o.state AS current_state, o.realized_pnl_minor AS current_pnl_minor,
               o.resolution_source AS current_resolution_source,
               o.resolution_evidence_id AS current_resolution_evidence_id,
               o.resolved_at AS current_resolved_at, o.updated_at AS current_updated_at,
               o.broker_order_id, o.account_id, o.pnl_application_count,
               ti.product, ti.symbol, ti.direction, ti.amount_minor, ti.currency,
               rr.state AS reservation_state, rr.release_count
        FROM iqoption_settlement_repairs r
        JOIN orders o ON o.order_id = r.order_id
        JOIN trade_intents ti ON ti.intent_id = o.intent_id
        JOIN risk_reservations rr ON rr.intent_id = o.intent_id
        WHERE r.batch_id = ? AND r.state = 'APPROVED'
        ORDER BY r.order_id
        """,
        (batch_id,),
    ).fetchall()
    if not repairs:
        return SettlementRepairResult(
            SettlementRepairOperationStatus.BLOCKED,
            batch_id,
            0,
            "REPAIR_BATCH_HAS_NO_APPROVED_CHANGES",
        )

    nonterminal = connection.execute(
        """
        SELECT 1 FROM orders
        WHERE broker = 'IQ_OPTION'
          AND state NOT IN ('SETTLED', 'REJECTED', 'SEND_BLOCKED')
        LIMIT 1
        """
    ).fetchone()
    if nonterminal is not None:
        return _block_batch(
            connection,
            batch_id,
            repairs,
            actor,
            applied_at,
            "REPAIR_MAINTENANCE_WINDOW_REQUIRED",
        )

    target_ids = tuple(str(item["order_id"]) for item in repairs)
    runtime = connection.execute(
        "SELECT state_json FROM iqoption_execution_state WHERE singleton = 1"
    ).fetchone()
    if runtime is not None and _payload_references_any(runtime["state_json"], target_ids):
        return _block_batch(
            connection,
            batch_id,
            repairs,
            actor,
            applied_at,
            "REPAIR_IQ_RUNTIME_REFERENCES_ORDER",
        )

    for item in repairs:
        reason = _application_conflict(connection, item)
        if reason is not None:
            return _block_batch(
                connection,
                batch_id,
                repairs,
                actor,
                applied_at,
                reason,
                conflict_repair_id=str(item["repair_id"]),
            )

    for item in repairs:
        changed = connection.execute(
            """
            UPDATE orders
            SET realized_pnl_minor = ?, resolution_source = ?,
                resolution_evidence_id = ?, resolved_at = ?
            WHERE order_id = ? AND state = 'SETTLED' AND realized_pnl_minor = ?
              AND resolution_source = 'STATUS_QUERY'
              AND resolution_evidence_id IS ? AND updated_at = ?
            """,
            (
                item["proposed_pnl_minor"],
                REPAIR_RESOLUTION_SOURCE,
                item["verification_evidence_id"],
                applied_at.isoformat(),
                item["order_id"],
                item["original_pnl_minor"],
                item["original_resolution_evidence_id"],
                item["original_updated_at"],
            ),
        ).rowcount
        if changed != 1:
            raise RuntimeError("repair compare-and-swap changed an unexpected row count")
        connection.execute(
            """
            UPDATE iqoption_settlement_repairs
            SET state = 'APPLIED', applied_at = ?, last_reason_code = NULL
            WHERE repair_id = ? AND state = 'APPROVED'
            """,
            (applied_at.isoformat(), item["repair_id"]),
        )
        _append_event(
            connection,
            repair_id=str(item["repair_id"]),
            event_type="CORRECTION_APPLIED",
            previous_state="APPROVED",
            new_state="APPLIED",
            actor=actor,
            reason_code="REPAIR_APPLIED_WITH_NEW_EVIDENCE",
            evidence_id=item["verification_evidence_id"],
            previous_pnl_minor=int(item["original_pnl_minor"]),
            proposed_pnl_minor=int(item["proposed_pnl_minor"]),
            occurred_at=applied_at,
        )
    connection.execute(
        """
        UPDATE iqoption_settlement_repair_batches
        SET state = 'APPLIED', applied_by = ?, applied_at = ?, last_reason_code = NULL
        WHERE batch_id = ?
        """,
        (actor, applied_at.isoformat(), batch_id),
    )
    return SettlementRepairResult(
        SettlementRepairOperationStatus.APPLIED,
        batch_id,
        len(repairs),
    )


def _repair_projection(connection: sqlite3.Connection, repair_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = connection.execute(
        """
        SELECT r.*, o.state AS current_state, o.realized_pnl_minor AS current_pnl_minor,
               o.resolution_source AS current_resolution_source,
               o.resolution_evidence_id AS current_resolution_evidence_id,
               o.resolved_at AS current_resolved_at, o.updated_at AS current_updated_at,
               o.broker_order_id, o.account_id, o.pnl_application_count,
               ti.product, ti.symbol, ti.direction, ti.amount_minor, ti.currency
        FROM iqoption_settlement_repairs r
        JOIN orders o ON o.order_id = r.order_id
        JOIN trade_intents ti ON ti.intent_id = o.intent_id
        WHERE r.repair_id = ?
        """,
        (repair_id,),
    ).fetchone()
    return row


def _original_evidence_conflict(row: sqlite3.Row) -> str | None:
    if row["resolution_evidence_id"] is None or row["evidence_order_id"] is None:
        return "REPAIR_ORIGINAL_EVIDENCE_MISSING"
    if row["evidence_order_id"] != row["order_id"]:
        return "REPAIR_ORIGINAL_EVIDENCE_ORDER_CONFLICT"
    if row["evidence_source"] != "STATUS_QUERY":
        return "REPAIR_ORIGINAL_EVIDENCE_SOURCE_CONFLICT"
    if row["evidence_status"] != "SETTLED" or row["evidence_pnl_minor"] != 0:
        return "REPAIR_ORIGINAL_EVIDENCE_VALUE_CONFLICT"
    return None


def _verification_conflict(
    row: sqlite3.Row,
    evidence: ReconciliationEvidence,
) -> str | None:
    comparisons = (
        (evidence.client_order_ref, row["order_id"], "REPAIR_CLIENT_ORDER_REF_CONFLICT"),
        (evidence.broker.value, Broker.IQ_OPTION.value, "REPAIR_BROKER_CONFLICT"),
        (evidence.account_id, row["account_id"], "REPAIR_ACCOUNT_CONFLICT"),
        (evidence.product, row["product"], "REPAIR_PRODUCT_CONFLICT"),
        (evidence.symbol, row["symbol"], "REPAIR_SYMBOL_CONFLICT"),
        (evidence.direction.value, row["direction"], "REPAIR_DIRECTION_CONFLICT"),
        (evidence.amount.minor_units, row["amount_minor"], "REPAIR_AMOUNT_CONFLICT"),
        (evidence.amount.currency, row["currency"], "REPAIR_CURRENCY_CONFLICT"),
    )
    for actual, expected, reason in comparisons:
        if actual != expected:
            return reason
    if evidence.source.value != "STATUS_QUERY":
        return "REPAIR_EVIDENCE_SOURCE_UNSUPPORTED"
    if evidence.external_status is not ExternalOrderStatus.SETTLED:
        return "REPAIR_EVIDENCE_NOT_TERMINAL"
    if evidence.broker_order_id is None or evidence.broker_order_id != row["broker_order_id"]:
        return "REPAIR_BROKER_ORDER_ID_CONFLICT"
    raw_hash = evidence.raw_reference_hash
    if (
        raw_hash is None
        or len(raw_hash) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in raw_hash)
    ):
        return "REPAIR_RAW_EVIDENCE_HASH_REQUIRED"
    pnl = evidence.realized_pnl_minor
    if pnl is None or pnl < -int(row["amount_minor"]):
        return "REPAIR_PNL_OUTSIDE_PROVABLE_RANGE"
    original_time = row["original_resolved_at"] or row["original_updated_at"]
    if evidence.observed_at.isoformat() <= str(original_time):
        return "REPAIR_EVIDENCE_NOT_NEWER"
    return None


def _application_conflict(connection: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    if _order_fingerprint(row) != row["original_fingerprint"]:
        return "REPAIR_ORIGINAL_ORDER_CHANGED"
    if int(row["pnl_application_count"]) != 1:
        return "REPAIR_PNL_APPLICATION_COUNT_CONFLICT"
    if row["reservation_state"] != "RELEASED" or int(row["release_count"]) != 1:
        return "REPAIR_RESERVATION_EFFECT_CONFLICT"
    consumed = connection.execute(
        """
        SELECT consumed FROM manifest_order_bindings WHERE order_id = ?
        """,
        (row["order_id"],),
    ).fetchone()
    if consumed is not None and int(consumed["consumed"]) != 0:
        return "REPAIR_DOWNSTREAM_ALREADY_CONSUMED"
    terminal_events = connection.execute(
        """
        SELECT result_minor FROM broker_order_events
        WHERE order_id = ? AND external_status = 'SETTLED'
        """,
        (row["order_id"],),
    ).fetchall()
    if any(event["result_minor"] != row["proposed_pnl_minor"] for event in terminal_events):
        return "REPAIR_BROKER_EVENT_CONFLICT"
    return None


def _block_batch(
    connection: sqlite3.Connection,
    batch_id: str,
    repairs: list[sqlite3.Row],
    actor: str,
    occurred_at: datetime,
    reason_code: str,
    *,
    conflict_repair_id: str | None = None,
) -> SettlementRepairResult:
    connection.execute(
        """
        UPDATE iqoption_settlement_repair_batches
        SET state = 'BLOCKED', last_reason_code = ? WHERE batch_id = ?
        """,
        (reason_code, batch_id),
    )
    impacted = (
        repairs
        if conflict_repair_id is None
        else [item for item in repairs if item["repair_id"] == conflict_repair_id]
    )
    for item in impacted:
        connection.execute(
            """
            UPDATE iqoption_settlement_repairs
            SET state = 'CONFLICT', last_reason_code = ? WHERE repair_id = ?
            """,
            (reason_code, item["repair_id"]),
        )
        _append_event(
            connection,
            repair_id=str(item["repair_id"]),
            event_type="APPLICATION_BLOCKED",
            previous_state=str(item["state"]),
            new_state="CONFLICT",
            actor=actor,
            reason_code=reason_code,
            evidence_id=item["verification_evidence_id"],
            previous_pnl_minor=int(item["original_pnl_minor"]),
            proposed_pnl_minor=int(item["proposed_pnl_minor"]),
            occurred_at=occurred_at,
        )
    return SettlementRepairResult(
        SettlementRepairOperationStatus.BLOCKED,
        batch_id,
        0,
        reason_code,
    )


def _conflict(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    actor: str,
    occurred_at: datetime,
    reason_code: str,
) -> SettlementRepairResult:
    connection.execute(
        """
        UPDATE iqoption_settlement_repairs
        SET state = 'CONFLICT', last_reason_code = ? WHERE repair_id = ?
        """,
        (reason_code, row["repair_id"]),
    )
    connection.execute(
        """
        UPDATE iqoption_settlement_repair_batches
        SET state = 'BLOCKED', last_reason_code = ? WHERE batch_id = ?
        """,
        (reason_code, row["batch_id"]),
    )
    _append_event(
        connection,
        repair_id=str(row["repair_id"]),
        event_type="VERIFICATION_CONFLICT",
        previous_state=str(row["state"]),
        new_state="CONFLICT",
        actor=actor,
        reason_code=reason_code,
        evidence_id=None,
        previous_pnl_minor=int(row["original_pnl_minor"]),
        proposed_pnl_minor=None,
        occurred_at=occurred_at,
    )
    return SettlementRepairResult(
        SettlementRepairOperationStatus.BLOCKED,
        str(row["batch_id"]),
        0,
        reason_code,
    )


def _append_event(
    connection: sqlite3.Connection,
    *,
    repair_id: str,
    event_type: str,
    previous_state: str | None,
    new_state: str,
    actor: str,
    reason_code: str | None,
    evidence_id: str | None,
    previous_pnl_minor: int | None,
    proposed_pnl_minor: int | None,
    occurred_at: datetime,
) -> None:
    prior = connection.execute(
        """
        SELECT sequence, event_hash FROM iqoption_settlement_repair_events
        WHERE repair_id = ? ORDER BY sequence DESC LIMIT 1
        """,
        (repair_id,),
    ).fetchone()
    sequence = 1 if prior is None else int(prior["sequence"]) + 1
    previous_hash = None if prior is None else str(prior["event_hash"])
    payload = {
        "repair_id": repair_id,
        "sequence": sequence,
        "event_type": event_type,
        "previous_state": previous_state,
        "new_state": new_state,
        "actor": actor,
        "reason_code": reason_code,
        "evidence_id": evidence_id,
        "previous_pnl_minor": previous_pnl_minor,
        "proposed_pnl_minor": proposed_pnl_minor,
        "occurred_at": occurred_at.isoformat(),
        "previous_event_hash": previous_hash,
    }
    event_hash = _hash_payload(payload)
    connection.execute(
        """
        INSERT INTO iqoption_settlement_repair_events(
            event_id, repair_id, sequence, event_type, previous_state, new_state,
            actor, reason_code, evidence_id, previous_pnl_minor, proposed_pnl_minor,
            occurred_at, previous_event_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"iqre-{uuid4()}",
            repair_id,
            sequence,
            event_type,
            previous_state,
            new_state,
            actor,
            reason_code,
            evidence_id,
            previous_pnl_minor,
            proposed_pnl_minor,
            occurred_at.isoformat(),
            previous_hash,
            event_hash,
        ),
    )


def _order_fingerprint(row: sqlite3.Row) -> str:
    def value(primary: str, fallback: str) -> Any:
        keys = row.keys()
        return row[primary] if primary in keys else row[fallback]

    return _hash_payload(
        {
            "order_id": row["order_id"],
            "state": value("current_state", "state"),
            "realized_pnl_minor": value("current_pnl_minor", "realized_pnl_minor"),
            "resolution_source": value("current_resolution_source", "resolution_source"),
            "resolution_evidence_id": value(
                "current_resolution_evidence_id", "resolution_evidence_id"
            ),
            "resolved_at": value("current_resolved_at", "resolved_at"),
            "updated_at": value("current_updated_at", "updated_at"),
            "broker_order_id": row["broker_order_id"],
            "account_id": row["account_id"],
            "product": row["product"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "amount_minor": row["amount_minor"],
            "currency": row["currency"],
            "pnl_application_count": row["pnl_application_count"],
        }
    )


def _repair_id(order_id: str, evidence_id: object) -> str:
    digest = hashlib.sha256(
        f"iqoption-settlement-repair-v1|{order_id}|{evidence_id}".encode()
    ).hexdigest()
    return f"iqr-{digest[:40]}"


def _evidence_hash(evidence: ReconciliationEvidence) -> str:
    return _hash_payload(evidence.to_payload())


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _insert_reconciliation_evidence(
    connection: sqlite3.Connection,
    order_id: str,
    evidence: ReconciliationEvidence,
    canonical_hash: str,
    received_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO reconciliation_evidence(
            evidence_id, order_id, source, observed_at, client_order_ref,
            broker_order_id, external_status, broker, account_id, product, symbol,
            direction, amount_minor, currency, realized_pnl_minor, raw_reference_hash,
            evidence_version, canonical_hash, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence.evidence_id,
            order_id,
            evidence.source.value,
            evidence.observed_at.isoformat(),
            evidence.client_order_ref,
            evidence.broker_order_id,
            evidence.external_status.value,
            evidence.broker.value,
            evidence.account_id,
            evidence.product,
            evidence.symbol,
            evidence.direction.value,
            evidence.amount.minor_units,
            evidence.amount.currency,
            evidence.realized_pnl_minor,
            evidence.raw_reference_hash,
            evidence.evidence_version,
            canonical_hash,
            received_at.isoformat(),
        ),
    )


def _payload_references_any(encoded: str, values: tuple[str, ...]) -> bool:
    try:
        payload = json.loads(encoded)
    except (TypeError, json.JSONDecodeError):
        return True

    def contains(value: object) -> bool:
        if isinstance(value, dict):
            return any(contains(item) for item in value.values())
        if isinstance(value, list):
            return any(contains(item) for item in value)
        return isinstance(value, str) and value in values

    return contains(payload)
