"""P04 collect tests (R-COL-1..13)."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from primitives import Candle
from strategy_lab import cli
from strategy_lab.collect.backfill import backfill_asset, closed_candle_exclusive
from strategy_lab.collect.canary import CANARY_FIXTURE, CanaryMismatch, run_canary
from strategy_lab.collect.clock import Clock, ClockError
from strategy_lab.collect.gaps import classify_gaps
from strategy_lab.collect.invariants import check_invariants
from strategy_lab.collect.iq_client import FakeIQClient, IQClientError
from strategy_lab.collect.payout_sampler import sample_payout
from strategy_lab.collect.repository import FakeRepository
from strategy_lab.collect.runner import run_collect

START = 1700000040
NOW = 1700000400
UPSTREAM = "test-upstream"


class ListClient:
    def __init__(
        self, candles: list[Candle] | list[object], payout: Decimal | None = Decimal("0.87")
    ) -> None:
        self.candles = candles
        self.payout = payout
        self.calls: list[str] = []

    def login(self) -> None:
        self.calls.append("login")

    def logout(self) -> None:
        self.calls.append("logout")

    def fetch_candles(self, asset: str, tf_s: int, n: int, end_ts: int) -> list[Candle]:
        self.calls.append(f"candles:{asset}:{n}:{end_ts}")
        return [
            candle for candle in self.candles if isinstance(candle, Candle) and candle.ts < end_ts
        ][-n:]

    def fetch_payout(self, asset: str) -> Decimal | None:
        self.calls.append(f"payout:{asset}")
        return self.payout

    def list_assets(self) -> list[str]:
        return ["EURUSD-OTC"]


class InvalidBatchClient(ListClient):
    def __init__(self) -> None:
        super().__init__([])

    def fetch_candles(self, asset: str, tf_s: int, n: int, end_ts: int) -> list[Candle]:
        self.calls.append(f"candles:{asset}:{n}:{end_ts}")
        return [{"not": "a candle"}]  # type: ignore[list-item]


def candle(ts: int, close: str = "1.1") -> Candle:
    value = Decimal(close)
    return Candle(
        ts=ts, o=value, h=value + Decimal("0.01"), l=value - Decimal("0.01"), c=value, tick_vol=1
    )


def test_clock_ntp_accepts_small_skew_and_rejects_large_skew() -> None:
    """R-COL-1: NTP skew is checked through an injectable clock."""
    clock = Clock(lambda: 1000)
    clock.check_ntp(ntp_request=lambda server: 1004)
    with pytest.raises(ClockError, match="CLOCK_NTP_SKEW"):
        clock.check_ntp(ntp_request=lambda server: 1006)


def test_canary_fixture_matches() -> None:
    """R-COL-2: committed canary rows match the expected public fixture."""
    client = FakeIQClient(CANARY_FIXTURE, now=lambda: NOW)
    client.login()
    run_canary(client)
    client.logout()


def test_canary_mismatch_aborts_before_write(tmp_path: Path) -> None:
    """R-COL-2/I-7: canary mismatch aborts before any repository write."""
    raw = json.loads(CANARY_FIXTURE.read_text(encoding="utf-8"))
    raw["candles"][0]["close"] = "1.07006"
    fixture = tmp_path / "bad-canary.json"
    fixture.write_text(json.dumps(raw), encoding="utf-8")
    repository = FakeRepository()
    clock = Clock(lambda: NOW)
    with pytest.raises(CanaryMismatch):
        run_collect(
            assets=["EURUSD-OTC"],
            repository=repository,
            clock=clock,
            client_factory=lambda: FakeIQClient(fixture, now=clock.now_ts),
            dry_run=False,
            initial_from_ts=START,
            check_ntp=False,
        )
    assert repository.candles == {}
    assert repository.gaps == []
    assert repository.runs == []


def test_backfill_is_idempotent() -> None:
    """R-COL-3/R-COL-6: three runs leave exactly one stored candle set."""
    candles = [candle(START + index * 60) for index in range(5)]
    repository = FakeRepository()
    client = ListClient(candles)
    for _ in range(3):
        result = backfill_asset(
            client=client,
            repository=repository,
            asset="EURUSD-OTC",
            now_ts=NOW,
            upstream_commit=UPSTREAM,
            initial_from_ts=START,
        )
    assert result.written == 0
    assert len(repository.candles) == 5
    assert repository.watermark("EURUSD-OTC") == START + 4 * 60


def test_dst_and_current_candle_never_written() -> None:
    """R-COL-4/I-3: UTC epoch logic ignores DST and excludes the current candle."""
    europe_dst = timezone(timedelta(hours=2))
    local_epoch = int(datetime(2026, 3, 29, 2, 30, tzinfo=europe_dst).astimezone(UTC).timestamp())
    utc_epoch = int(datetime(2026, 3, 29, 0, 30, tzinfo=UTC).timestamp())
    for now_ts in [local_epoch, utc_epoch]:
        cutoff = closed_candle_exclusive(now_ts)
        current = now_ts // 60 * 60
        repository = FakeRepository()
        client = ListClient([candle(cutoff - 60), candle(cutoff), candle(current)])
        backfill_asset(
            client=client,
            repository=repository,
            asset="EURUSD-OTC",
            now_ts=now_ts,
            upstream_commit=UPSTREAM,
            initial_from_ts=cutoff - 60,
        )
        assert ("EURUSD-OTC", cutoff) not in repository.candles
        assert ("EURUSD-OTC", current) not in repository.candles


def test_invalid_candle_aborts_run_zero_writes() -> None:
    """R-COL-5/I-7: invalid batch contents fail closed before writing."""
    repository = FakeRepository()
    client = InvalidBatchClient()
    with pytest.raises(IQClientError, match="COL_CANDLE_BATCH_INVALID"):
        backfill_asset(
            client=client,
            repository=repository,
            asset="EURUSD-OTC",
            now_ts=NOW,
            upstream_commit=UPSTREAM,
            initial_from_ts=START,
        )
    assert repository.candles == {}


def test_gaps_classified_by_session() -> None:
    """R-COL-7: expected M1 gaps carry the in_session flag from the calendar."""
    monday = int(datetime(2026, 9, 7, 0, 0, tzinfo=UTC).timestamp())
    saturday = int(datetime(2026, 9, 5, 0, 0, tzinfo=UTC).timestamp())
    forex_gaps = classify_gaps("EURUSD", range(monday, monday + 120, 60), [], NOW)
    otc_gaps = classify_gaps("EURUSD-OTC", range(saturday, saturday + 120, 60), [], NOW)
    assert forex_gaps[0].in_session
    assert otc_gaps[0].in_session
    assert not classify_gaps("EURUSD", range(saturday, saturday + 120, 60), [], NOW)[0].in_session


def test_payout_hours_without_run_stay_zero_samples() -> None:
    """R-COL-8: only sampled hours increment payout samples."""
    repository = FakeRepository()
    client = ListClient([], payout=Decimal("0.87"))
    missing_hour = 1700002800
    assert sample_payout(
        client=client, repository=repository, asset="EURUSD-OTC", now_ts=START
    ) == Decimal("0.87")
    assert repository.payouts[("EURUSD-OTC", START // 3600 * 3600)].samples == 1
    assert ("EURUSD-OTC", missing_hour) not in repository.payouts


def test_invariant_jump_marks_suspect() -> None:
    """R-COL-9: jump beyond 8 ATR is reported as suspect."""
    series = [candle(START + index * 60, "1.0000") for index in range(15)]
    series.append(candle(START + 15 * 60, "1.5000"))
    assert [issue.code for issue in check_invariants(series)] == ["COL_SUSPECT_JUMP"]


def test_collect_dry_run_cli_prints_complete_report(capsys: pytest.CaptureFixture[str]) -> None:
    """R-COL-10/R-COL-11/R-COL-13: dry-run CLI uses fakes and emits JSON report."""
    assert cli.main(["collect", "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["event"] == "strategy_lab_collect_report"
    assert report["dry_run"] is True
    assert report["status"] == "ok"
    assert report["assets"][0]["asset"] == "EURUSD-OTC"
    assert "payout_return_ratio" in report["assets"][0]


def test_status_cli_reports_fake_repository_health(capsys: pytest.CaptureFixture[str]) -> None:
    """R-COL-13: status command has a stable JSON contract."""
    assert cli.main(["status", "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["event"] == "strategy_lab_collect_status"
    assert report["last_run_stale"] is True
    assert report["backup_stale"] is True


def test_no_secrets_in_logs(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-COL-1/I-8: arbitrary failures are scrubbed from CLI output."""
    secret = "operator@example.invalid"

    def fail(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError(secret)

    monkeypatch.setattr(cli, "run_collect", fail)
    assert cli.main(["collect", "--dry-run"]) == 1
    output = capsys.readouterr()
    assert secret not in output.out + output.err


def test_collect_modules_do_not_use_wall_clock_shortcuts() -> None:
    """R-COL-1/I-2: no time.time or naive datetime.now appears in collect modules."""
    root = Path(__file__).parents[1] / "tools/strategy_lab/collect"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "time":
                    assert node.func.attr != "time", path
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "datetime"
                    and node.func.attr == "now"
                ):
                    assert node.args or node.keywords, path
