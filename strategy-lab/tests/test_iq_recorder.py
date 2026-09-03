"""R-VEND-3: recording is bounded, price-only, fail-closed and manual."""

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from primitives import Candle
from strategy_lab import cli
from strategy_lab.collect.iq_client import FakeIQClient, IQClientError
from strategy_lab.collect.recorder import record_fixture

START = 1700000040
END = START + 60000
NOW = END + 300


class SimulatedRecording:
    """Test-only generated series; not evidence of real broker data."""

    def __init__(self, size=1000, *, broken=False):
        self.size = size
        self.broken = broken
        self.closed = False
        self.calls = []

    def login(self):
        self.calls.append("login")

    def logout(self):
        self.closed = True
        self.calls.append("logout")

    def fetch_candles(self, asset, tf_s, n, end_ts):
        self.calls.append("fetch_candles")
        if self.broken:
            raise RuntimeError("test_generated_error")
        return [
            Candle(
                ts=START + 60 * index,
                o=Decimal("1.1"),
                h=Decimal("1.2"),
                l=Decimal("1.0"),
                c=Decimal("1.15"),
                tick_vol=5,
            )
            for index in range(self.size)
        ]


def test_record_1000_in_temp_only_and_roundtrip_fake(tmp_path):
    """R-VEND-3: 1000 synthetic test rows prove writer, not the real-data acceptance."""
    simulated = SimulatedRecording()
    output = tmp_path / "recording.json"
    result = record_fixture(
        asset="EURUSD-OTC",
        from_ts=START,
        to_ts=END,
        output=output,
        client_factory=lambda: simulated,
        now_ts=NOW,
    )
    assert simulated.closed
    assert simulated.calls == ["login", "fetch_candles", "logout"]
    assert result["count"] == 1000
    data = json.loads(output.read_text())
    digest = data.pop("sha256")
    assert (
        hashlib.sha256(
            json.dumps(
                data,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        == digest
    )
    assert set(data["candles"][0]) == {"from", "to", "open", "max", "min", "close", "volume"}
    fake = FakeIQClient(output, pause=lambda seconds: None, now=lambda: NOW)
    fake.login()
    assert fake.list_assets() == ["EURUSD-OTC"]
    assert fake.fetch_payout("EURUSD-OTC") is None
    assert len(fake.fetch_candles("EURUSD-OTC", 60, 1000, END)) == 1000
    fake.logout()


@pytest.mark.parametrize("count", [0, 999, 1001])
def test_gap_or_wrong_count_leaves_no_file(tmp_path, count):
    """R-VEND-3/I-7: never save a partial or oversize response as a full fixture."""
    output = tmp_path / "recording.json"
    client = SimulatedRecording(count)
    with pytest.raises(IQClientError, match="IQ_FIXTURE_COVERAGE_INCOMPLETE"):
        record_fixture(
            asset="EURUSD-OTC",
            from_ts=START,
            to_ts=END,
            output=output,
            client_factory=lambda: client,
            now_ts=NOW,
        )
    assert not output.exists()
    assert client.closed


def test_existing_fixture_never_overwritten(tmp_path):
    """R-VEND-3: existing operator price fixture is preserved, no login attempted."""
    path = tmp_path / "saved.json"
    path.write_text("preserve")
    client = SimulatedRecording()
    with pytest.raises(IQClientError, match="IQ_FIXTURE_EXISTS"):
        record_fixture(
            asset="EURUSD-OTC",
            from_ts=START,
            to_ts=END,
            output=path,
            client_factory=lambda: client,
            now_ts=NOW,
        )
    assert path.read_text() == "preserve"
    assert client.calls == []


@pytest.mark.parametrize(
    "start,end",
    [
        (START + 1, END),
        (START, END + 60),
        (END, START),
        (START, NOW),
        (-60, END),
    ],
)
def test_invalid_range_does_not_login(tmp_path, start, end):
    """R-VEND-3: max 1000, UTC grid and closed range enforced before credentials."""
    client = SimulatedRecording()
    with pytest.raises(IQClientError):
        record_fixture(
            asset="EURUSD-OTC",
            from_ts=start,
            to_ts=end,
            output=tmp_path / "x.json",
            client_factory=lambda: client,
            now_ts=NOW,
        )
    assert client.calls == []


def test_failure_still_closes_without_file(tmp_path):
    """R-VEND-3: broker failure cannot produce a fixture or leave a session active."""
    client = SimulatedRecording(broken=True)
    path = tmp_path / "failure.json"
    with pytest.raises(RuntimeError):
        record_fixture(
            asset="EURUSD-OTC",
            from_ts=START,
            to_ts=END,
            output=path,
            client_factory=lambda: client,
            now_ts=NOW,
        )
    assert client.closed
    assert not path.exists()


def test_disk_failure_removes_only_new_file(tmp_path, monkeypatch):
    """R-VEND-3: failed fsync cleans new partial output, not unrelated data."""
    client = SimulatedRecording()
    path = tmp_path / "failure.json"

    def fail(fd):
        raise OSError

    monkeypatch.setattr("strategy_lab.collect.recorder.os.fsync", fail)
    with pytest.raises(OSError):
        record_fixture(
            asset="EURUSD-OTC",
            from_ts=START,
            to_ts=END,
            output=path,
            client_factory=lambda: client,
            now_ts=NOW,
        )
    assert not path.exists()


def test_cli_no_secret_on_failure(monkeypatch, capsys, tmp_path):
    """R-VEND-3/I-8: arbitrary exceptions cannot leak in CLI JSON."""

    def fail(**kwargs):
        raise RuntimeError("private_data_must_not_be_printed")

    monkeypatch.setattr(cli, "record_fixture", fail)
    assert (
        cli.main(
            [
                "record-fixture",
                "--asset",
                "EURUSD-OTC",
                "--from",
                str(START),
                "--to",
                str(END),
                "--output",
                str(tmp_path / "output.json"),
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    assert "private_data" not in output.out + output.err
    assert json.loads(output.out)["status"] == "failed"


def test_cli_success_reports_only_summary(monkeypatch, capsys, tmp_path):
    """R-VEND-3: CLI emits minimal public provenance, no full response."""
    monkeypatch.setattr(cli, "record_fixture", lambda **kw: {"count": 1000, "sha256": "0" * 64})
    assert (
        cli.main(
            [
                "record-fixture",
                "--asset",
                "EURUSD-OTC",
                "--from",
                str(START),
                "--to",
                str(END),
                "--output",
                str(tmp_path / "output.json"),
            ]
        )
        == 0
    )
    assert set(json.loads(capsys.readouterr().out)) == {"event", "count", "sha256"}


@pytest.mark.parametrize("text", ["2026-09-01T12:00:00", "not-a-date", "-1"])
def test_cli_rejects_ambiguous_timestamps(text):
    """R-VEND-3/I-2: timezone cannot be inferred from the workstation locale."""
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_epoch(text)


def test_cli_utc_offsets_are_equivalent():
    """R-VEND-3/I-2: UTC and explicit offset refer to the same instant."""
    assert cli.parse_epoch("2026-09-01T12:00:00Z") == cli.parse_epoch("2026-09-01T09:00:00-03:00")
    assert cli.parse_epoch(str(START)) == START


def test_committed_iq_fixtures_do_not_claim_live_collection():
    """R-VEND-3: initial synthetic fixture must not count as the real 1000-row acceptance."""
    root = Path(__file__).parent / "fixtures/iq"
    for path in root.glob("synthetic-*.json"):
        assert json.loads(path.read_text())["provenance"] == "synthetic"


def test_fake_rejects_modified_recorded_fixture(tmp_path):
    """R-VEND-3: a recorded source cannot be altered without invalidating provenance."""
    path = tmp_path / "recorded.json"
    record_fixture(
        asset="EURUSD-OTC",
        from_ts=START,
        to_ts=END,
        output=path,
        client_factory=SimulatedRecording,
        now_ts=NOW,
    )
    data = json.loads(path.read_text())
    data["candles"][0]["close"] = "1.19"
    path.write_text(json.dumps(data))
    with pytest.raises(IQClientError, match="IQ_FIXTURE_INTEGRITY_FAILED"):
        FakeIQClient(path)
