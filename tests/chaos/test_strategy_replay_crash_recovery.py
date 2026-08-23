from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from packages.persistence.strategy_data import StrategyDataDatabase
from packages.replay import ReplayEngine
from tests.replay.test_recoverable_replay import (
    catalog_factory,
    persistence_for,
    recoverable_request,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def start_strategy_actor(
    action: str,
    database_path: Path,
) -> subprocess.Popen[str]:
    ready_path = database_path.parent / f"{action}.ready.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "tests.helpers.strategy_crash_actor",
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
            payload = json.loads(ready_path.read_text(encoding="utf-8"))
            assert payload == {"action": action, "commit_count": 300}
            return process
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                f"strategy crash actor exited before ready: {process.returncode}; "
                f"{stdout}; {stderr}"
            )
        time.sleep(0.02)
    process.kill()
    process.wait(timeout=5)
    raise AssertionError(f"strategy crash actor did not become ready: {action}")


def kill_actor(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


@pytest.mark.parametrize(
    ("action", "expected_committed_candles"),
    (
        ("strategy_before_commit", 299),
        ("strategy_after_commit", 300),
    ),
)
def test_kill_around_atomic_candle_commit_restores_exact_replay(
    tmp_path: Path,
    action: str,
    expected_committed_candles: int,
) -> None:
    database_path = tmp_path / "strategy_data.db"
    process = start_strategy_actor(action, database_path)
    kill_actor(process)

    request = recoverable_request()
    database = StrategyDataDatabase(database_path)
    persistence = persistence_for(database)
    try:
        checkpoint = persistence.warmup.latest(request.context)
        assert checkpoint is not None
        assert checkpoint.candles_seen == expected_committed_candles
        journal = persistence.journal.events_for_run(request.run_id)
        assert journal
        assert journal[-1].event.candle_id == checkpoint.last_candle_id

        restored = ReplayEngine(catalog_factory).create_session(
            request,
            persistence=persistence,
            checkpoint=checkpoint,
        )
        boundary_index = checkpoint.candles_seen - 1
        restored.process_many(request.candles[boundary_index:])
        restored_result = restored.complete()
        restored_checkpoint = restored.checkpoint()
    finally:
        database.close()

    clean = ReplayEngine(catalog_factory).create_session(request)
    clean.process_many(request.candles)
    clean_result = clean.complete()
    clean_checkpoint = clean.checkpoint()

    assert restored_result == clean_result
    assert restored_checkpoint.state_sha256 == clean_checkpoint.state_sha256
    assert restored_result.final_hash == clean_result.final_hash
