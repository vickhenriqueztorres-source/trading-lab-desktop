"""Durable research evidence and holdout reservation contracts (CAT-05).

This module keeps the research approval trail explicit and small.  Metadata goes
to Postgres through an `EvidenceRepository`; bulky trade logs/reports are stored
as hash-addressed private objects through an `ArtifactStore`.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from primitives.base import Direction

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.dataset import DatasetSnapshot
from strategy_lab.research.replay_simulator import TradeLog

EVIDENCE_METHOD_VERSION = "tl.research.evidence.v1"


class EvidenceUnavailable(RuntimeError):
    """Raised when durable evidence cannot be read/written safely."""


@dataclass(frozen=True)
class DatasetSnapshotRecord:
    fingerprint: str
    origin: str
    source: str
    asset: str
    timeframe_s: int
    from_ts: int
    to_ts: int
    present: int
    expected: int
    coverage: Decimal
    unresolved_in_session_gaps: int
    tick_volume_available: bool
    approval_eligible: bool
    public_evidence: dict[str, object]

    @classmethod
    def from_snapshot(cls, snapshot: DatasetSnapshot) -> DatasetSnapshotRecord:
        return cls(
            fingerprint=snapshot.fingerprint,
            origin=snapshot.identity.origin.value,
            source=snapshot.identity.source,
            asset=snapshot.identity.asset,
            timeframe_s=snapshot.identity.timeframe_s,
            from_ts=snapshot.identity.from_ts,
            to_ts=snapshot.identity.to_ts,
            present=snapshot.quality.present,
            expected=snapshot.quality.expected,
            coverage=snapshot.quality.coverage,
            unresolved_in_session_gaps=snapshot.quality.unresolved_in_session_gaps,
            tick_volume_available=snapshot.quality.tick_volume_available,
            approval_eligible=snapshot.quality.approval_eligible,
            public_evidence=snapshot.public_evidence(),
        )


@dataclass(frozen=True)
class HoldoutReservation:
    reservation_id: str
    dataset_fingerprint: str
    asset: str
    timeframe_s: int
    from_ts: int
    to_ts: int
    holdout_hash: str
    reserved_by_run_id: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        snapshot: DatasetSnapshot,
        holdout_range: tuple[int, int],
        holdout_hash: str,
    ) -> HoldoutReservation:
        from_ts, to_ts = holdout_range
        identity = snapshot.identity
        payload = {
            "asset": identity.asset,
            "dataset_fingerprint": snapshot.fingerprint,
            "from_ts": from_ts,
            "holdout_hash": holdout_hash,
            "run_id": run_id,
            "timeframe_s": identity.timeframe_s,
            "to_ts": to_ts,
        }
        return cls(
            reservation_id=_hash_payload(payload),
            dataset_fingerprint=snapshot.fingerprint,
            asset=identity.asset,
            timeframe_s=identity.timeframe_s,
            from_ts=from_ts,
            to_ts=to_ts,
            holdout_hash=holdout_hash,
            reserved_by_run_id=run_id,
        )

    @property
    def range_ts(self) -> tuple[int, int]:
        return (self.from_ts, self.to_ts)


@dataclass(frozen=True)
class ResearchAttemptRecord:
    run_id: str
    candidate_hash: str
    family: str
    asset: str
    timeframe: str
    params_canonical: dict[str, object]
    partition: str
    method_version: str
    seed: int
    status: str
    approval_state: str
    counts: dict[str, int]
    dataset_fingerprint: str

    @classmethod
    def from_candidate(
        cls,
        *,
        run_id: str,
        candidate: Candidate,
        partition: str,
        seed: int,
        status: str,
        approval_state: str,
        counts: dict[str, int],
        dataset_fingerprint: str,
    ) -> ResearchAttemptRecord:
        return cls(
            run_id=run_id,
            candidate_hash=candidate.hash(),
            family=candidate.family,
            asset=candidate.asset,
            timeframe=candidate.tf,
            params_canonical=_canonical_candidate_params(candidate),
            partition=partition,
            method_version=EVIDENCE_METHOD_VERSION,
            seed=seed,
            status=status,
            approval_state=approval_state,
            counts=dict(counts),
            dataset_fingerprint=dataset_fingerprint,
        )


@dataclass(frozen=True)
class EvidenceArtifact:
    sha256: str
    kind: str
    storage_path: str
    byte_count: int
    run_id: str
    candidate_hash: str | None = None


class ArtifactStore(Protocol):
    def put_hash_addressed(self, *, kind: str, payload: bytes) -> EvidenceArtifact: ...


class EvidenceRepository(Protocol):
    def record_dataset_snapshot(self, record: DatasetSnapshotRecord) -> None: ...
    def reserve_holdout(self, reservation: HoldoutReservation) -> None: ...
    def mark_holdout_opened(self, reservation_id: str, *, run_id: str, opened_at: int) -> None: ...
    def burn_holdout(self, reservation_id: str, *, run_id: str, burned_at: int) -> None: ...
    def rollback_holdout(self, reservation_id: str, *, run_id: str, reason: str) -> None: ...
    def burned_holdout_ranges(
        self, *, dataset_fingerprint: str, asset: str, timeframe_s: int
    ) -> tuple[tuple[int, int], ...]: ...
    def record_attempt(self, record: ResearchAttemptRecord) -> None: ...
    def record_artifact(self, artifact: EvidenceArtifact) -> None: ...


@dataclass
class MemoryArtifactStore:
    """Test/dry-run store that behaves like a private hash-addressed object bucket."""

    run_id: str
    candidate_hash: str | None = None
    objects: dict[str, bytes] = field(default_factory=dict)

    def put_hash_addressed(self, *, kind: str, payload: bytes) -> EvidenceArtifact:
        digest = hashlib.sha256(payload).hexdigest()
        path = f"private/evidence/sha256/{digest}.json"
        self.objects[path] = bytes(payload)
        return EvidenceArtifact(
            sha256=digest,
            kind=kind,
            storage_path=path,
            byte_count=len(payload),
            run_id=self.run_id,
            candidate_hash=self.candidate_hash,
        )


@dataclass
class FakeEvidenceRepository:
    """In-memory repository with locking to test fail-closed holdout behavior."""

    dataset_snapshots: dict[str, DatasetSnapshotRecord] = field(default_factory=dict)
    holdouts: dict[str, tuple[HoldoutReservation, str]] = field(default_factory=dict)
    attempts: dict[tuple[str, str, str], ResearchAttemptRecord] = field(default_factory=dict)
    artifacts: dict[str, EvidenceArtifact] = field(default_factory=dict)
    fail_reads: bool = False
    fail_writes: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_dataset_snapshot(self, record: DatasetSnapshotRecord) -> None:
        self._fail_if_write_disabled()
        self.dataset_snapshots[record.fingerprint] = record

    def reserve_holdout(self, reservation: HoldoutReservation) -> None:
        self._fail_if_write_disabled()
        key = _holdout_key(reservation)
        with self._lock:
            existing = self.holdouts.get(key)
            if existing is not None and existing[1] != "rolled_back":
                raise EvidenceUnavailable("RES_HOLDOUT_ALREADY_RESERVED")
            self.holdouts[key] = (reservation, "sealed")

    def mark_holdout_opened(self, reservation_id: str, *, run_id: str, opened_at: int) -> None:
        self._fail_if_write_disabled()
        self._transition_holdout(
            reservation_id,
            run_id=run_id,
            allowed=("sealed",),
            target="opened",
        )

    def burn_holdout(self, reservation_id: str, *, run_id: str, burned_at: int) -> None:
        self._fail_if_write_disabled()
        self._transition_holdout(
            reservation_id,
            run_id=run_id,
            allowed=("sealed", "opened"),
            target="burned",
        )

    def rollback_holdout(self, reservation_id: str, *, run_id: str, reason: str) -> None:
        self._fail_if_write_disabled()
        if not reason.strip():
            raise EvidenceUnavailable("RES_HOLDOUT_ROLLBACK_REASON_REQUIRED")
        self._transition_holdout(
            reservation_id,
            run_id=run_id,
            allowed=("sealed",),
            target="rolled_back",
        )

    def burned_holdout_ranges(
        self, *, dataset_fingerprint: str, asset: str, timeframe_s: int
    ) -> tuple[tuple[int, int], ...]:
        if self.fail_reads:
            raise EvidenceUnavailable("RES_EVIDENCE_READ_FAILED")
        ranges: list[tuple[int, int]] = []
        for reservation, state in self.holdouts.values():
            if (
                state == "burned"
                and reservation.dataset_fingerprint == dataset_fingerprint
                and reservation.asset == asset
                and reservation.timeframe_s == timeframe_s
            ):
                ranges.append(reservation.range_ts)
        return tuple(sorted(ranges))

    def record_attempt(self, record: ResearchAttemptRecord) -> None:
        self._fail_if_write_disabled()
        self.attempts[(record.run_id, record.candidate_hash, record.partition)] = record

    def record_artifact(self, artifact: EvidenceArtifact) -> None:
        self._fail_if_write_disabled()
        if artifact.sha256 in self.artifacts and self.artifacts[artifact.sha256] != artifact:
            raise EvidenceUnavailable("RES_ARTIFACT_HASH_CONFLICT")
        self.artifacts[artifact.sha256] = artifact

    def _transition_holdout(
        self,
        reservation_id: str,
        *,
        run_id: str,
        allowed: tuple[str, ...],
        target: str,
    ) -> None:
        with self._lock:
            for key, (reservation, state) in self.holdouts.items():
                if reservation.reservation_id != reservation_id:
                    continue
                if reservation.reserved_by_run_id != run_id:
                    raise EvidenceUnavailable("RES_HOLDOUT_RUN_MISMATCH")
                if state not in allowed:
                    raise EvidenceUnavailable("RES_HOLDOUT_STATE_INVALID")
                self.holdouts[key] = (reservation, target)
                return
        raise EvidenceUnavailable("RES_HOLDOUT_RESERVATION_NOT_FOUND")

    def _fail_if_write_disabled(self) -> None:
        if self.fail_writes:
            raise EvidenceUnavailable("RES_EVIDENCE_WRITE_FAILED")


def ensure_durable_evidence_available(
    *,
    production_eligible: bool,
    evidence_repository: EvidenceRepository | None,
) -> None:
    if production_eligible and evidence_repository is None:
        raise EvidenceUnavailable("RES_DURABLE_EVIDENCE_REQUIRED")


def refuse_if_persistent_holdout_burned(
    repository: EvidenceRepository,
    reservation: HoldoutReservation,
) -> None:
    for burned_from, burned_to in repository.burned_holdout_ranges(
        dataset_fingerprint=reservation.dataset_fingerprint,
        asset=reservation.asset,
        timeframe_s=reservation.timeframe_s,
    ):
        if max(reservation.from_ts, burned_from) < min(reservation.to_ts, burned_to):
            raise EvidenceUnavailable("RES_HOLDOUT_RANGE_BURNED")


def trade_log_artifact_payload(candidate: Candidate, trade_log: TradeLog) -> bytes:
    """Serialize replay evidence without duplicating candle JSON."""

    payload = {
        "candidate_hash": candidate.hash(),
        "excluded_missing_payout": trade_log.excluded_missing_payout,
        "excluded_settlement_gap": trade_log.excluded_settlement_gap,
        "trades": [
            {
                "asset": trade.asset,
                "direction": _direction_value(trade.direction),
                "payout_return_ratio": format(trade.payout_return_ratio, "f"),
                "profit_ratio": format(trade.profit_ratio, "f"),
                "ts": trade.ts,
                "update_count_at_signal": trade.update_count_at_signal,
                "won": trade.won,
            }
            for trade in trade_log.trades
        ],
    }
    return _canonical_json_bytes(payload)


def _canonical_candidate_params(candidate: Candidate) -> dict[str, object]:
    return {
        name: {
            key: format(value, "f") if isinstance(value, Decimal) else value
            for key, value in sorted(params.items())
        }
        for name, params in sorted(candidate.params.items())
    }


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _direction_value(direction: Direction) -> str:
    return direction


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _holdout_key(reservation: HoldoutReservation) -> str:
    return (
        f"{reservation.dataset_fingerprint}|{reservation.asset}|"
        f"{reservation.timeframe_s}|{reservation.from_ts}|{reservation.to_ts}"
    )
