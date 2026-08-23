from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from packages.domain.models import Broker, require_aware_utc


class ValidationStage(StrEnum):
    BACKTEST = "BACKTEST"
    WALK_FORWARD = "WALK_FORWARD"
    REPLAY = "REPLAY"
    PRACTICE = "PRACTICE"


class ValidationRepositoryProtocol(Protocol):
    def save_report(self, report: ValidationReport) -> None: ...

    def get_reports_for_strategy(
        self, strategy_id: str, version: str
    ) -> tuple[ValidationReport, ...]: ...

    def is_stage_approved(
        self,
        strategy_id: str,
        version: str,
        code_hash: str,
        stage: ValidationStage,
    ) -> bool: ...

    def release_eligible(self, strategy_id: str, version: str, code_hash: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    evidence_id: str
    strategy_id: str
    strategy_version: str
    report_id: str
    stage: ValidationStage
    approved: bool
    broker: Broker
    product: str
    symbol: str
    timeframe_seconds: int
    dataset_id: str
    period_start: datetime
    period_end: datetime
    metrics: tuple[tuple[str, Decimal], ...]

    def __post_init__(self) -> None:
        require_aware_utc(self.period_start, "period_start")
        require_aware_utc(self.period_end, "period_end")
        for field in (
            "evidence_id",
            "strategy_id",
            "strategy_version",
            "report_id",
            "product",
            "symbol",
            "dataset_id",
        ):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} cannot be empty")
        if self.period_end <= self.period_start or self.timeframe_seconds <= 0:
            raise ValueError("validation period/timeframe is invalid")
        metric_names = tuple(name for name, _ in self.metrics)
        if any(not name.strip() for name in metric_names) or len(set(metric_names)) != len(
            metric_names
        ):
            raise ValueError("metric names must be unique and non-empty")
        if any(not value.is_finite() for _, value in self.metrics):
            raise ValueError("validation metrics must be finite decimals")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    report_id: str
    strategy_id: str
    strategy_version: str
    code_hash: str
    stage: ValidationStage
    is_approved: bool
    metrics_json: str
    dataset_hash: str
    created_at_utc: datetime

    def __post_init__(self) -> None:
        require_aware_utc(self.created_at_utc, "created_at_utc")
        for field in (
            "report_id",
            "strategy_id",
            "strategy_version",
            "code_hash",
            "metrics_json",
            "dataset_hash",
        ):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} cannot be empty")
        if len(self.code_hash) != 64 or any(c not in "0123456789abcdef" for c in self.code_hash):
            raise ValueError("code_hash must be a 64-char lowercase hex string")


class ValidationRegistry:
    _REQUIRED = frozenset(ValidationStage)

    def __init__(self, repository: ValidationRepositoryProtocol | None = None) -> None:
        self._lock = threading.Lock()
        self._evidence: dict[str, ValidationEvidence] = {}
        self._reports: dict[str, ValidationReport] = {}
        self._repo = repository

    def record(self, evidence: ValidationEvidence) -> None:
        with self._lock:
            existing = self._evidence.get(evidence.evidence_id)
            if existing is not None and existing != evidence:
                raise ValueError("conflicting validation evidence id")
            self._evidence[evidence.evidence_id] = evidence

    def record_report(self, report: ValidationReport) -> None:
        if self._repo is not None:
            self._repo.save_report(report)
        with self._lock:
            self._reports[report.report_id] = report

    def evidence_for(
        self, strategy_id: str, version: str, report_id: str
    ) -> tuple[ValidationEvidence, ...]:
        with self._lock:
            return tuple(
                evidence
                for evidence in self._evidence.values()
                if evidence.strategy_id == strategy_id
                and evidence.strategy_version == version
                and evidence.report_id == report_id
            )

    def reports_for(self, strategy_id: str, version: str) -> tuple[ValidationReport, ...]:
        if self._repo is not None:
            return self._repo.get_reports_for_strategy(strategy_id, version)
        with self._lock:
            return tuple(
                report
                for report in self._reports.values()
                if report.strategy_id == strategy_id and report.strategy_version == version
            )

    def is_stage_approved(
        self,
        strategy_id: str,
        version: str,
        code_hash: str,
        stage: ValidationStage,
        report_id: str | None = None,
    ) -> bool:
        if self._repo is not None:
            return self._repo.is_stage_approved(strategy_id, version, code_hash, stage)
        with self._lock:
            for report in self._reports.values():
                if (
                    report.strategy_id == strategy_id
                    and report.strategy_version == version
                    and report.code_hash == code_hash
                    and report.stage == stage
                    and report.is_approved
                ):
                    return True
            for ev in self._evidence.values():
                if (
                    ev.strategy_id == strategy_id
                    and ev.strategy_version == version
                    and ev.stage == stage
                    and ev.approved
                    and (report_id is None or ev.report_id == report_id)
                ):
                    return True
            return False

    def release_eligible_for_code(
        self,
        strategy_id: str,
        version: str,
        code_hash: str,
        report_id: str | None = None,
    ) -> bool:
        if self._repo is not None:
            return self._repo.release_eligible(strategy_id, version, code_hash)
        if report_id is not None and self.release_eligible(strategy_id, version, report_id):
            return True
        return all(
            self.is_stage_approved(strategy_id, version, code_hash, stage, report_id)
            for stage in self._REQUIRED
        )

    def release_eligible(self, strategy_id: str, version: str, report_id: str) -> bool:
        evidence = self.evidence_for(strategy_id, version, report_id)
        if evidence:
            approved_stages = {item.stage for item in evidence if item.approved}
            rejected_stages = {item.stage for item in evidence if not item.approved}
            return approved_stages == self._REQUIRED and not rejected_stages
        with self._lock:
            report = self._reports.get(report_id)
            if report is not None:
                return self.release_eligible_for_code(
                    strategy_id, version, report.code_hash, report_id
                )
        return False
