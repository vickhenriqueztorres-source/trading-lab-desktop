from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from packages.strategy_catalog.validation import ValidationReport, ValidationStage

if TYPE_CHECKING:
    from packages.persistence.strategy_data import StrategyDataDatabase


class SqliteValidationRepository:
    """Durably stores and queries strategy validation reports in strategy_data.db."""

    _REQUIRED_STAGES = (
        ValidationStage.BACKTEST,
        ValidationStage.WALK_FORWARD,
        ValidationStage.REPLAY,
        ValidationStage.PRACTICE,
    )

    def __init__(self, database: StrategyDataDatabase) -> None:
        self._db = database

    def save_report(self, report: ValidationReport) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO strategy_validation_reports (
                    report_id,
                    strategy_id,
                    strategy_version,
                    code_hash,
                    stage,
                    is_approved,
                    metrics_json,
                    dataset_hash,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.strategy_id,
                    report.strategy_version,
                    report.code_hash,
                    report.stage.value,
                    1 if report.is_approved else 0,
                    report.metrics_json,
                    report.dataset_hash,
                    report.created_at_utc.isoformat(),
                ),
            )

    def get_reports_for_strategy(
        self,
        strategy_id: str,
        version: str,
    ) -> tuple[ValidationReport, ...]:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                SELECT
                    report_id,
                    strategy_id,
                    strategy_version,
                    code_hash,
                    stage,
                    is_approved,
                    metrics_json,
                    dataset_hash,
                    created_at_utc
                FROM strategy_validation_reports
                WHERE strategy_id = ? AND strategy_version = ?
                ORDER BY created_at_utc ASC
                """,
                (strategy_id, version),
            )
            rows = cursor.fetchall()

        reports: list[ValidationReport] = []
        for row in rows:
            reports.append(self._row_to_report(row))
        return tuple(reports)

    def is_stage_approved(
        self,
        strategy_id: str,
        version: str,
        code_hash: str,
        stage: ValidationStage,
    ) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                SELECT 1
                FROM strategy_validation_reports
                WHERE strategy_id = ?
                  AND strategy_version = ?
                  AND code_hash = ?
                  AND stage = ?
                  AND is_approved = 1
                LIMIT 1
                """,
                (strategy_id, version, code_hash, stage.value),
            )
            return cursor.fetchone() is not None

    def release_eligible(
        self,
        strategy_id: str,
        version: str,
        code_hash: str,
    ) -> bool:
        return all(
            self.is_stage_approved(strategy_id, version, code_hash, stage)
            for stage in self._REQUIRED_STAGES
        )

    @staticmethod
    def _row_to_report(row: tuple[object, ...]) -> ValidationReport:
        dt_str = str(row[8])
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return ValidationReport(
            report_id=str(row[0]),
            strategy_id=str(row[1]),
            strategy_version=str(row[2]),
            code_hash=str(row[3]),
            stage=ValidationStage(str(row[4])),
            is_approved=bool(row[5]),
            metrics_json=str(row[6]),
            dataset_hash=str(row[7]),
            created_at_utc=dt,
        )
