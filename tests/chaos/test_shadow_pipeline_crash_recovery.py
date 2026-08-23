from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from packages.domain.models import Broker
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FixedClock:
    def now(self) -> float:
        return 0.0


def start_actor(action: str, database_path: Path) -> subprocess.Popen[str]:
    ready_path = database_path.parent / f"{action}.ready.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "tests.helpers.shadow_crash_actor",
            action,
            str(database_path),
            str(ready_path),
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready_path.exists():
            assert json.loads(ready_path.read_text(encoding="utf-8")) == {
                "action": action,
                "commit_count": 300,
            }
            return process
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                f"shadow crash actor exited before ready: {process.returncode}; {stdout}; {stderr}"
            )
        time.sleep(0.02)
    process.kill()
    process.wait(timeout=5)
    raise AssertionError(f"shadow crash actor did not become ready: {action}")


@pytest.mark.parametrize(
    ("action", "expected_checkpoint"),
    (
        ("shadow_before_commit", 299),
        ("shadow_after_commit", 300),
    ),
)
def test_scheduler_shadow_kill_around_commit_recovers_exact_hash(
    tmp_path: Path,
    action: str,
    expected_checkpoint: int,
) -> None:
    path = tmp_path / "strategy_data.db"
    process = start_actor(action, path)
    process.kill()
    process.wait(timeout=5)

    request = recoverable_request()
    database = StrategyDataDatabase(path)
    persistence = persistence_for(database)
    checkpoint = persistence.warmup.latest(request.context)
    assert checkpoint is not None
    assert checkpoint.candles_seen == expected_checkpoint
    session = ReplayEngine(catalog_factory).create_session(
        request,
        persistence=persistence,
        checkpoint=checkpoint,
    )
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
    dispatcher = AcceptedCandleDispatcher(
        persistence.candles,
        health,
        ReplaySessionDecisionPipeline(session),
    )

    class ResumeJob:
        def recover(self, series_id: MarketSeriesId, generation: int) -> BackfillJobResult:
            for candle in request.candles[checkpoint.candles_seen :]:
                dispatcher.dispatch(series_id, candle)
            return BackfillJobResult(
                generation,
                True,
                request.candles[-1].close_time_ms,
                False,
                MarketHealthReason.INITIAL_WARMUP,
            )

    scheduler = MarketBackfillScheduler(FixedClock(), health, ResumeJob())
    scheduler.register(identity, last_durable_close_epoch=request.candles[-1].close_time_ms)
    scheduler.tick()
    restored = session.complete()
    database.close()

    clean = ReplayEngine(catalog_factory).run(request)
    assert restored == clean
    assert restored.final_hash == clean.final_hash
