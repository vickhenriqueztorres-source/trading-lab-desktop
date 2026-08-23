from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from packages.persistence.strategy_data import StrategyDataDatabase
from packages.replay import ReplayEngine
from tests.replay.test_recoverable_replay import (
    catalog_factory,
    persistence_for,
    recoverable_request,
)

TARGET_COMMIT = 300


def run(action: str, database_path: Path, ready_path: Path) -> None:
    before_commit_count = 0

    def pause_at_target(point: str) -> None:
        nonlocal before_commit_count
        if point == "before_strategy_candle_commit":
            before_commit_count += 1
        target_point = {
            "strategy_before_commit": "before_strategy_candle_commit",
            "strategy_after_commit": "after_strategy_candle_commit",
        }.get(action)
        if target_point is None:
            raise ValueError(f"unknown strategy crash action: {action}")
        if point == target_point and before_commit_count == TARGET_COMMIT:
            ready_path.parent.mkdir(parents=True, exist_ok=True)
            ready_path.write_text(
                json.dumps({"action": action, "commit_count": before_commit_count}),
                encoding="utf-8",
            )
            threading.Event().wait()

    database = StrategyDataDatabase(database_path, fault_injector=pause_at_target)
    request = recoverable_request()
    session = ReplayEngine(catalog_factory).create_session(
        request,
        persistence=persistence_for(database),
    )
    session.process_many(request.candles[:TARGET_COMMIT])
    raise RuntimeError("strategy crash actor passed its target without pausing")


if __name__ == "__main__":
    run(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
