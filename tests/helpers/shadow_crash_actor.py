from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from packages.domain.models import Broker
from packages.market_data import CandleIngress
from packages.market_pipeline import (
    AcceptedCandleDispatcher,
    BackfillJobResult,
    MarketBackfillScheduler,
    MarketHealthGate,
    MarketHealthReason,
    MarketSeriesId,
    ReplaySessionDecisionPipeline,
)
from packages.persistence.strategy_data import StrategyDataDatabase
from packages.replay import ReplayEngine
from tests.replay.test_recoverable_replay import (
    catalog_factory,
    persistence_for,
    recoverable_request,
)

TARGET_COMMIT = 300


class FixedClock:
    def now(self) -> float:
        return 0.0


def run(action: str, database_path: Path, ready_path: Path) -> None:
    before_commit_count = 0

    def pause_at_target(point: str) -> None:
        nonlocal before_commit_count
        if point == "before_strategy_candle_commit":
            before_commit_count += 1
        target = {
            "shadow_before_commit": "before_strategy_candle_commit",
            "shadow_after_commit": "after_strategy_candle_commit",
        }.get(action)
        if target is None:
            raise ValueError(f"unknown shadow crash action: {action}")
        if point == target and before_commit_count == TARGET_COMMIT:
            ready_path.write_text(
                json.dumps({"action": action, "commit_count": before_commit_count}),
                encoding="utf-8",
            )
            threading.Event().wait()

    database = StrategyDataDatabase(database_path, fault_injector=pause_at_target)
    request = recoverable_request()
    persistence = persistence_for(database)
    ingress = CandleIngress(persistence.candles)
    for candle in request.candles:
        ingress.ingest(candle)
    identity = MarketSeriesId(
        Broker.DERIV,
        request.symbol,
        request.symbol,
        request.product,
        request.timeframe_seconds,
    )
    health = MarketHealthGate()
    health.register(identity, required_closed_candles=len(request.candles))
    health.complete_recovery(
        identity,
        generation=0,
        continuity_valid=True,
        clock_trusted=True,
        durable_closed_candles=len(request.candles),
        last_durable_close=request.candles[-1].close_time_ms,
        last_source_event=request.candles[-1].source_event_id,
    )
    session = ReplayEngine(catalog_factory).create_session(request, persistence=persistence)
    dispatcher = AcceptedCandleDispatcher(
        persistence.candles,
        health,
        ReplaySessionDecisionPipeline(session),
    )

    class DispatchJob:
        def recover(self, series_id: MarketSeriesId, generation: int) -> BackfillJobResult:
            for candle in request.candles:
                dispatcher.dispatch(series_id, candle)
            return BackfillJobResult(
                generation,
                True,
                request.candles[-1].close_time_ms,
                False,
                MarketHealthReason.INITIAL_WARMUP,
            )

    scheduler = MarketBackfillScheduler(FixedClock(), health, DispatchJob())
    scheduler.register(identity)
    scheduler.tick()
    raise RuntimeError("shadow crash actor passed its target without pausing")


if __name__ == "__main__":
    run(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
