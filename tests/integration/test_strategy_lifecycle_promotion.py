from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.persistence.strategy_data import StrategyDataDatabase
from packages.persistence.validation_repository import SqliteValidationRepository
from packages.strategies.models import RuntimeContext, StrategyImplementation
from packages.strategy_catalog.catalog import (
    StrategyCatalog,
    StrategyCatalogError,
    StrategyCatalogReason,
)
from packages.strategy_catalog.models import (
    DataRequirement,
    ParameterKind,
    ParameterSpec,
    ReleaseStatus,
    RiskClass,
    StrategyManifest,
)
from packages.strategy_catalog.validation import (
    ValidationRegistry,
    ValidationReport,
    ValidationStage,
)


class DummyStrategy:
    def __init__(self, artifact_bytes: bytes) -> None:
        self._bytes = artifact_bytes

    @property
    def artifact_bytes(self) -> bytes:
        return self._bytes

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        return None


def _make_strategy(code_hash: str) -> tuple[StrategyManifest, StrategyImplementation, bytes]:
    artifact_bytes = b"def evaluate(): return 1"
    actual_hash = hashlib.sha256(artifact_bytes).hexdigest()
    manifest = StrategyManifest(
        manifest_version=1,
        strategy_id="momentum_v1",
        version="1.0.0",
        code_hash=actual_hash,
        supported_brokers=(Broker.DERIV,),
        supported_products=("OPTION",),
        supported_timeframes=(60,),
        required_data=(DataRequirement.CLOSED_CANDLES,),
        warmup_candles=10,
        parameter_schema=(ParameterSpec("period", ParameterKind.INTEGER, True),),
        risk_class=RiskClass.STANDARD,
        validation_report_id="rep_master",
        release_status=ReleaseStatus.DRAFT,
        strategy_pack="STANDARD_PACK",
    )
    implementation = DummyStrategy(artifact_bytes)
    return manifest, implementation, artifact_bytes


def test_strategy_promotion_gate_enforces_all_stages(tmp_path: Path) -> None:
    db = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repo = SqliteValidationRepository(db)
    validation_registry = ValidationRegistry(repo)
    catalog = StrategyCatalog(validation_registry)

    manifest, implementation, artifact_bytes = _make_strategy("placeholder")
    entry = catalog.register(manifest, implementation, artifact_bytes)
    code_hash = entry.artifact_hash
    now = datetime.now(UTC)

    # 1. Attempting invalid skip: DRAFT -> RELEASED directly must fail
    with pytest.raises(StrategyCatalogError) as exc_info:
        catalog.promote_strategy("momentum_v1", "1.0.0", ReleaseStatus.RELEASED)
    assert exc_info.value.reason == StrategyCatalogReason.LIFECYCLE_INVALID

    # 2. Attempting DRAFT -> BACKTESTED without approved report must fail
    with pytest.raises(StrategyCatalogError) as exc_info:
        catalog.promote_strategy("momentum_v1", "1.0.0", ReleaseStatus.BACKTESTED)
    assert exc_info.value.reason == StrategyCatalogReason.VALIDATION_INCOMPLETE

    # 3. Add BACKTEST report and promote
    repo.save_report(
        ValidationReport(
            report_id="r1",
            strategy_id="momentum_v1",
            strategy_version="1.0.0",
            code_hash=code_hash,
            stage=ValidationStage.BACKTEST,
            is_approved=True,
            metrics_json="{}",
            dataset_hash="d1" * 32,
            created_at_utc=now,
        )
    )
    entry = catalog.promote_strategy("momentum_v1", "1.0.0", ReleaseStatus.BACKTESTED)
    assert entry.manifest.release_status is ReleaseStatus.BACKTESTED

    # 4. BACKTESTED -> WALK_FORWARD_VALIDATED requires WALK_FORWARD report
    with pytest.raises(StrategyCatalogError) as exc_info:
        catalog.promote_strategy("momentum_v1", "1.0.0", ReleaseStatus.WALK_FORWARD_VALIDATED)
    assert exc_info.value.reason == StrategyCatalogReason.VALIDATION_INCOMPLETE

    repo.save_report(
        ValidationReport(
            report_id="r2",
            strategy_id="momentum_v1",
            strategy_version="1.0.0",
            code_hash=code_hash,
            stage=ValidationStage.WALK_FORWARD,
            is_approved=True,
            metrics_json="{}",
            dataset_hash="d2" * 32,
            created_at_utc=now,
        )
    )
    entry = catalog.promote_strategy("momentum_v1", "1.0.0", ReleaseStatus.WALK_FORWARD_VALIDATED)
    assert entry.manifest.release_status is ReleaseStatus.WALK_FORWARD_VALIDATED

    # 5. WALK_FORWARD_VALIDATED -> REPLAY_VALIDATED
    repo.save_report(
        ValidationReport(
            report_id="r3",
            strategy_id="momentum_v1",
            strategy_version="1.0.0",
            code_hash=code_hash,
            stage=ValidationStage.REPLAY,
            is_approved=True,
            metrics_json="{}",
            dataset_hash="d3" * 32,
            created_at_utc=now,
        )
    )
    entry = catalog.promote_strategy("momentum_v1", "1.0.0", ReleaseStatus.REPLAY_VALIDATED)
    assert entry.manifest.release_status is ReleaseStatus.REPLAY_VALIDATED

    # 6. REPLAY_VALIDATED -> PRACTICE_VALIDATED
    repo.save_report(
        ValidationReport(
            report_id="r4",
            strategy_id="momentum_v1",
            strategy_version="1.0.0",
            code_hash=code_hash,
            stage=ValidationStage.PRACTICE,
            is_approved=True,
            metrics_json="{}",
            dataset_hash="d4" * 32,
            created_at_utc=now,
        )
    )
    entry = catalog.promote_strategy("momentum_v1", "1.0.0", ReleaseStatus.PRACTICE_VALIDATED)
    assert entry.manifest.release_status is ReleaseStatus.PRACTICE_VALIDATED

    # 7. PRACTICE_VALIDATED -> RELEASED
    entry = catalog.promote_strategy("momentum_v1", "1.0.0", ReleaseStatus.RELEASED)
    assert entry.manifest.release_status is ReleaseStatus.RELEASED
