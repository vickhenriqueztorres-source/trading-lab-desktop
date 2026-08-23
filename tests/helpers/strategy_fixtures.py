from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies import RuntimeContext
from packages.strategy_catalog import (
    DataRequirement,
    ReleaseStatus,
    RiskClass,
    StrategyCatalog,
    StrategyManifest,
    ValidationEvidence,
    ValidationRegistry,
    ValidationStage,
)


class FixedDirectionStrategy:
    def __init__(self, artifact_bytes: bytes, direction: Direction | None) -> None:
        self._artifact_bytes = artifact_bytes
        self.direction = direction

    @property
    def artifact_bytes(self) -> bytes:
        return self._artifact_bytes

    def evaluate(
        self,
        candles: Sequence[MarketCandle],
        context: RuntimeContext,
    ) -> Direction | None:
        assert candles
        assert context.symbol == candles[-1].broker_symbol
        return self.direction


def artifact_for(strategy_id: str, direction: Direction | None) -> bytes:
    direction_name = "NONE" if direction is None else direction.value
    return f"PACKAGED_PHASE0:{strategy_id}:{direction_name}:v1".encode("ascii")


def manifest_for(
    strategy_id: str,
    artifact: bytes,
    *,
    version: str = "1.0.0",
    status: ReleaseStatus = ReleaseStatus.RELEASED,
    warmup_candles: int = 1,
    strategy_pack: str = "phase0-candidates",
) -> StrategyManifest:
    return StrategyManifest(
        manifest_version=1,
        strategy_id=strategy_id,
        version=version,
        code_hash=hashlib.sha256(artifact).hexdigest(),
        supported_brokers=(Broker.DERIV, Broker.IQ_OPTION),
        supported_products=("DIGITAL_OPTION",),
        supported_timeframes=(60,),
        required_data=(DataRequirement.CLOSED_CANDLES,),
        warmup_candles=warmup_candles,
        parameter_schema=(),
        risk_class=RiskClass.CONSERVATIVE,
        validation_report_id=f"report-{strategy_id}-{version}",
        release_status=status,
        strategy_pack=strategy_pack,
    )


def record_release_evidence(
    registry: ValidationRegistry,
    manifest: StrategyManifest,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for stage in ValidationStage:
        registry.record(
            ValidationEvidence(
                evidence_id=f"evidence-{manifest.strategy_id}-{manifest.version}-{stage.value}",
                strategy_id=manifest.strategy_id,
                strategy_version=manifest.version,
                report_id=manifest.validation_report_id,
                stage=stage,
                approved=True,
                broker=Broker.DERIV,
                product="DIGITAL_OPTION",
                symbol="EURUSD",
                timeframe_seconds=60,
                dataset_id=f"synthetic-{stage.value.lower()}",
                period_start=start,
                period_end=start + timedelta(days=1),
                metrics=(("sample_count", Decimal("100")),),
            )
        )


def register_released(
    catalog: StrategyCatalog,
    registry: ValidationRegistry,
    strategy_id: str,
    direction: Direction | None,
    *,
    warmup_candles: int = 1,
) -> StrategyManifest:
    artifact = artifact_for(strategy_id, direction)
    manifest = manifest_for(strategy_id, artifact, warmup_candles=warmup_candles)
    record_release_evidence(registry, manifest)
    catalog.register(manifest, FixedDirectionStrategy(artifact, direction), artifact)
    return manifest


def context_for(
    strategy_id: str,
    *,
    account_id: str = "demo-account-1",
    symbol: str = "EURUSD",
    configuration_version: str = "config-1",
) -> RuntimeContext:
    return RuntimeContext(
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        broker=Broker.DERIV,
        account_id=account_id,
        product="DIGITAL_OPTION",
        symbol=symbol,
        timeframe_seconds=60,
        configuration_version=configuration_version,
    )


def candle_for(
    close_time: datetime,
    *,
    symbol: str = "EURUSD",
    closed: bool = True,
    rising: bool = True,
) -> MarketCandle:
    opened = close_time - timedelta(seconds=60)
    close = Decimal("101") if rising else Decimal("99")
    return MarketCandle(
        broker=Broker.DERIV,
        broker_symbol=symbol,
        timeframe_seconds=60,
        open_time=opened,
        close_time=close_time,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("98"),
        close=close,
        is_closed=closed,
    )


def unique_strategy_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"
