from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from packages.persistence.strategy_data import StrategyDataDatabase
from packages.persistence.validation_repository import SqliteValidationRepository
from packages.strategy_catalog.validation import ValidationReport, ValidationStage


def test_sqlite_validation_repository_crud(tmp_path: Path) -> None:
    db = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repo = SqliteValidationRepository(db)

    now = datetime.now(UTC)
    report_bt = ValidationReport(
        report_id="rep_bt_1",
        strategy_id="trend_follower",
        strategy_version="1.0.0",
        code_hash="a" * 64,
        stage=ValidationStage.BACKTEST,
        is_approved=True,
        metrics_json=json.dumps({"win_rate": "0.65", "profit_factor": "1.8"}),
        dataset_hash="d" * 64,
        created_at_utc=now,
    )

    report_wf = ValidationReport(
        report_id="rep_wf_1",
        strategy_id="trend_follower",
        strategy_version="1.0.0",
        code_hash="a" * 64,
        stage=ValidationStage.WALK_FORWARD,
        is_approved=True,
        metrics_json=json.dumps({"robustness": "0.75"}),
        dataset_hash="d" * 64,
        created_at_utc=now,
    )

    repo.save_report(report_bt)
    repo.save_report(report_wf)

    # Query reports
    reports = repo.get_reports_for_strategy("trend_follower", "1.0.0")
    assert len(reports) == 2
    assert reports[0].report_id == "rep_bt_1"
    assert reports[1].report_id == "rep_wf_1"

    # Query stage approval
    assert repo.is_stage_approved("trend_follower", "1.0.0", "a" * 64, ValidationStage.BACKTEST)
    assert repo.is_stage_approved("trend_follower", "1.0.0", "a" * 64, ValidationStage.WALK_FORWARD)
    assert not repo.is_stage_approved("trend_follower", "1.0.0", "a" * 64, ValidationStage.REPLAY)
    assert not repo.is_stage_approved("trend_follower", "1.0.0", "b" * 64, ValidationStage.BACKTEST)

    # Release eligibility (requires all 4 stages)
    assert not repo.release_eligible("trend_follower", "1.0.0", "a" * 64)

    # Add remaining stages
    repo.save_report(
        ValidationReport(
            report_id="rep_rep_1",
            strategy_id="trend_follower",
            strategy_version="1.0.0",
            code_hash="a" * 64,
            stage=ValidationStage.REPLAY,
            is_approved=True,
            metrics_json="{}",
            dataset_hash="d" * 64,
            created_at_utc=now,
        )
    )
    repo.save_report(
        ValidationReport(
            report_id="rep_prac_1",
            strategy_id="trend_follower",
            strategy_version="1.0.0",
            code_hash="a" * 64,
            stage=ValidationStage.PRACTICE,
            is_approved=True,
            metrics_json="{}",
            dataset_hash="d" * 64,
            created_at_utc=now,
        )
    )

    assert repo.release_eligible("trend_follower", "1.0.0", "a" * 64)
