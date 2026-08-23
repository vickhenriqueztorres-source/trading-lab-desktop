from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSpec, ReadOnlyWorkerSupervisor
from packages.brokers.deriv import (
    DerivCandleAdapter,
    DerivCandleHistoryPump,
    DerivCandleIngressBridge,
)
from packages.domain.models import Broker
from packages.market_data import CandleIngress
from packages.persistence.candle_repository import SqliteCandleRepository
from packages.persistence.strategy_data import StrategyDataDatabase
from packages.protocol.envelope import EndpointRole


@pytest.fixture
def deriv_supervisor() -> Iterator[ReadOnlyWorkerSupervisor]:
    supervisor = ReadOnlyWorkerSupervisor(
        HealthGate(),
        ReadOnlyWorkerSpec(
            module="apps.deriv_worker",
            role=EndpointRole.DERIV_WORKER,
            broker="DERIV",
        ),
    )
    supervisor.start()
    try:
        yield supervisor
    finally:
        supervisor.shutdown()


def make_pump(
    supervisor: ReadOnlyWorkerSupervisor,
    repository: SqliteCandleRepository,
) -> DerivCandleHistoryPump:
    return DerivCandleHistoryPump(
        supervisor.client,
        DerivCandleIngressBridge(
            DerivCandleAdapter(frozenset({"frxEURUSD"})),
            CandleIngress(repository),
        ),
        max_batch_size=16,
        now=lambda: datetime(2026, 8, 20, 12, tzinfo=UTC),
    )


def test_fake_deriv_subprocess_ipc_reconnect_and_persistent_ingress_are_idempotent(
    tmp_path: Path,
    deriv_supervisor: ReadOnlyWorkerSupervisor,
) -> None:
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    try:
        pump = make_pump(deriv_supervisor, repository)
        first = pump.backfill("frxEURUSD", 60, count=10)
        repeated = pump.backfill("frxEURUSD", 60, count=10)
        assert first.accepted_count == 1
        assert first.response_message_id
        assert first.correlation_id
        assert first.causation_id
        assert first.has_quality_failure is False
        assert repeated.duplicate_count == 1

        deriv_supervisor.restart()
        after_reconnect = make_pump(deriv_supervisor, repository).backfill(
            "frxEURUSD",
            60,
            count=10,
        )
        assert after_reconnect.duplicate_count == 1
        stored = repository.range((Broker.DERIV, "frxEURUSD", 60))
        assert len(stored) == 1
        assert stored[0].source == "DERIV_CANDLES_READ_ONLY"
        assert stored[0].source_event_id.startswith(
            f"{first.response_message_id}|{first.correlation_id}|{first.causation_id}|"
        )
        assert not (tmp_path / "state.db").exists()
    finally:
        database.close()
