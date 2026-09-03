"""Daily collect orchestration with injectable dependencies (R-COL-1..13)."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from strategy_lab.collect.backfill import backfill_asset
from strategy_lab.collect.backup import backup_dir_default, latest_backup_age_days
from strategy_lab.collect.canary import run_canary
from strategy_lab.collect.clock import Clock
from strategy_lab.collect.invariants import check_invariants
from strategy_lab.collect.iq_client import LAB_ROOT, IQClient, IQClientProtocol
from strategy_lab.collect.payout_sampler import sample_payout
from strategy_lab.collect.repository import FakeRepository, Repository


def read_upstream_commit() -> str:
    return (LAB_ROOT / "vendor/iqoptionapi/UPSTREAM_COMMIT").read_text(encoding="utf-8").strip()


def run_collect(
    *,
    assets: list[str],
    repository: Repository,
    clock: Clock,
    client_factory: Callable[[], IQClientProtocol] = IQClient,
    dry_run: bool = False,
    payout_only: bool = False,
    initial_from_ts: int | None = None,
    check_ntp: bool = True,
) -> dict[str, object]:
    started = time.monotonic()
    now_ts = clock.now_ts()
    asset_reports: list[dict[str, object]] = []
    report: dict[str, object] = {
        "event": "strategy_lab_collect_report",
        "run_id": uuid.uuid4().hex,
        "status": "ok",
        "dry_run": dry_run,
        "payout_only": payout_only,
        "started_at": now_ts,
        "assets": asset_reports,
        "duration_s": "0.000000",
    }
    completed = False
    if check_ntp:
        clock.check_ntp()
    client = client_factory()
    try:
        client.login()
        run_canary(client)
        for asset in assets:
            asset_report: dict[str, object] = {"asset": asset}
            payout = sample_payout(
                client=client,
                repository=repository,
                asset=asset,
                now_ts=now_ts,
                dry_run=dry_run,
            )
            asset_report["payout_return_ratio"] = None if payout is None else str(payout)
            if not payout_only:
                result = backfill_asset(
                    client=client,
                    repository=repository,
                    asset=asset,
                    now_ts=now_ts,
                    upstream_commit=read_upstream_commit(),
                    initial_from_ts=initial_from_ts,
                    dry_run=dry_run,
                )
                asset_report.update(
                    {
                        "fetched": result.fetched,
                        "written": result.written,
                        "gaps_in_session": result.gaps_in_session,
                        "gaps_out_session": result.gaps_out_session,
                        "next_watermark": result.next_watermark,
                    }
                )
                if isinstance(repository, FakeRepository):
                    stored = [
                        candle
                        for (stored_asset, _ts), (candle, _source) in repository.candles.items()
                        if stored_asset == asset
                    ]
                    issues = check_invariants(sorted(stored, key=lambda candle: candle.ts))
                    if issues:
                        asset_report["invariant_issues"] = [issue.code for issue in issues]
                        report["status"] = "suspect"
            asset_reports.append(asset_report)
        completed = True
    except Exception:
        report["status"] = "aborted"
        report["error"] = "COLLECT_ABORTED"
        raise
    finally:
        client.logout()
        report["duration_s"] = f"{time.monotonic() - started:.6f}"
        if completed and not dry_run:
            repository.record_run(report)
    return report


def status_report(repository: FakeRepository, *, now_ts: int) -> dict[str, object]:
    last_started = repository.runs[-1].get("started_at") if repository.runs else None
    stale = isinstance(last_started, int) and now_ts - last_started > 3 * 86400
    unresolved_in_session = sum(1 for gap in repository.gaps if gap.in_session and not gap.resolved)
    backup_age_days = latest_backup_age_days(backup_dir_default(), now_ts=now_ts)
    return {
        "event": "strategy_lab_collect_status",
        "status": repository.last_status,
        "last_run_stale": stale or last_started is None,
        "unresolved_in_session_gaps": unresolved_in_session,
        "supabase_paused": False,
        "latest_backup_age_days": backup_age_days,
        "backup_stale": backup_age_days is None or backup_age_days > 8,
    }


def fake_fixture_path() -> Path:
    return LAB_ROOT / "tests/fixtures/canary.json"


def to_json(data: dict[str, object]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
