"""R-COL-1/2/13: real collection bootstrap, fake IO and no financial messages."""

import hashlib
import json
from decimal import Decimal

import pytest
from primitives import Candle
from strategy_lab import cli
from strategy_lab.collect.canary import CANARY_FIXTURE, CanaryMismatch, run_canary
from strategy_lab.collect.clock import Clock
from strategy_lab.collect.iq_client import IQClient
from strategy_lab.collect.preflight import collection_preflight
from strategy_lab.collect.recorded_canary import (
    MAX_CANARY_BYTES,
    RecordedCanaryError,
    load_recorded_canary,
)
from strategy_lab.collect.recorder import record_fixture
from strategy_lab.collect.repository import FakeRepository, RepositoryError
from strategy_lab.collect.runner import run_collect

START = 1700000040
NOW = START + 600


class PriceOnlyClient:
    """Strict fake: it has no financial API, only public-price reads."""

    def __init__(self) -> None:
        self.calls = []
        self.rows = [
            Candle(
                ts=START + i * 60,
                o=Decimal("1.1"),
                h=Decimal("1.2"),
                l=Decimal("1.0"),
                c=Decimal("1.15"),
                tick_vol=5,
            )
            for i in range(5)
        ]

    def login(self):
        self.calls.append("login")

    def logout(self):
        self.calls.append("logout")

    def fetch_candles(self, asset, tf_s, n, end_ts):
        self.calls.append("candles")
        assert asset == "EURUSD-OTC" and tf_s == 60
        return [c for c in self.rows if c.ts < end_ts][-n:]

    def fetch_payout(self, asset):
        self.calls.append("payout")
        return Decimal("0.85")

    def list_assets(self):
        return ["EURUSD-OTC"]


@pytest.fixture
def recorded(tmp_path):
    path = tmp_path / "five-prices.json"
    record_fixture(
        asset="EURUSD-OTC",
        from_ts=START,
        to_ts=START + 300,
        output=path,
        client_factory=PriceOnlyClient,
        now_ts=NOW,
    )
    return path


def rehash(path, data):
    unsigned = {key: value for key, value in data.items() if key != "sha256"}
    data["sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(data), encoding="utf-8")


def test_recorded_reference_compares_five_exact_candles_and_does_not_write(recorded):
    """R-COL-2: full timestamp/OHLC/volume equality; zero financial methods."""
    reference = load_recorded_canary(recorded, now_ts=NOW)
    client = PriceOnlyClient()
    run_canary(client, reference=reference)
    assert client.calls == ["candles"] * 5
    assert len(reference.candles) == 5


def test_changed_real_reference_aborts_before_repository_write(recorded):
    """R-COL-2/I-7: mismatch never persists payout, candles, gaps or run."""
    client = PriceOnlyClient()
    client.rows[2] = client.rows[2].model_copy(update={"c": Decimal("1.16")})
    repository = FakeRepository()
    with pytest.raises(CanaryMismatch):
        run_collect(
            assets=["EURUSD-OTC"],
            repository=repository,
            clock=Clock(lambda: NOW),
            client_factory=lambda: client,
            canary_path=recorded,
            check_ntp=False,
        )
    assert repository.candles == {} and repository.payouts == {} and repository.runs == []
    assert "payout" not in client.calls
    assert client.calls[-1] == "logout"


def test_synthetic_reference_never_enters_real_collection():
    """R-COL-2: immutable CI fixture cannot be selected for live collection."""
    with pytest.raises(RecordedCanaryError, match="COL_RECORDED_CANARY_INVALID"):
        run_collect(
            assets=["EURUSD-OTC"],
            repository=FakeRepository(),
            clock=Clock(lambda: NOW),
            canary_path=CANARY_FIXTURE,
            client_factory=lambda: pytest.fail("No client allowed"),
        )


@pytest.mark.parametrize(
    "damage",
    [
        "hash",
        "current",
        "future",
        "gap",
        "bounds",
        "count",
        "vendor",
        "secret_field",
        "nan",
        "float",
        "boolean",
        "volume",
    ],
)
def test_hostile_recorded_canary_rejected_before_login(recorded, damage):
    """R-COL-2/I-7/I-8: malformed reference fails closed even with recomputed hash."""
    data = json.loads(recorded.read_text())
    if damage == "hash":
        data["candles"][0]["close"] = "1.16"
        recorded.write_text(json.dumps(data))
    else:
        if damage == "current":
            data["collected_at"] = START + 300
        elif damage == "future":
            data["collected_at"] = NOW + 60
        elif damage == "gap":
            data["candles"][1]["from"] = START
        elif damage == "bounds":
            data["candles"][0]["min"] = "5"
        elif damage == "count":
            data["count"] = 4
        elif damage == "vendor":
            data["upstream_commit"] = "0" * 40
        elif damage == "secret_field":
            data["credentials"] = "must-never-appear"
        elif damage == "nan":
            data["candles"][0]["close"] = "NaN"
        elif damage == "float":
            data["candles"][0]["close"] = 1.15
        elif damage == "boolean":
            data["schema_version"] = True
        elif damage == "volume":
            data["candles"][0]["volume"] = -1
        rehash(recorded, data)
    with pytest.raises(RecordedCanaryError, match="^COL_RECORDED_CANARY_INVALID$"):
        run_collect(
            assets=["EURUSD-OTC"],
            repository=FakeRepository(),
            clock=Clock(lambda: NOW),
            canary_path=recorded,
            client_factory=lambda: pytest.fail("No client allowed"),
        )


def test_canary_size_and_duplicate_keys_rejected(recorded):
    """R-COL-2: cap input size; duplicate keys cannot override reference fields."""
    text = recorded.read_text()
    recorded.write_text(text.replace("{", '{"count":5,', 1))
    with pytest.raises(RecordedCanaryError):
        load_recorded_canary(recorded, now_ts=NOW)
    recorded.write_bytes(b" " * (MAX_CANARY_BYTES + 1))
    with pytest.raises(RecordedCanaryError):
        load_recorded_canary(recorded, now_ts=NOW)


def test_real_client_requires_reference_before_login():
    """R-COL-2: the external IQClient cannot silently use CI synthetic prices."""
    client = IQClient(credential_provider=lambda: pytest.fail("No credential read"))
    with pytest.raises(RecordedCanaryError, match="COL_RECORDED_CANARY_REQUIRED"):
        run_collect(
            assets=["EURUSD"],
            repository=FakeRepository(),
            clock=Clock(lambda: NOW),
            client_factory=lambda: client,
            check_ntp=False,
        )


def test_preflight_reports_all_missing_without_network_or_secret(monkeypatch, tmp_path, capsys):
    """R-COL-1/13: missing setup is actionable, all errors are scrubbed."""
    from strategy_lab.collect import preflight

    def denied():
        raise RuntimeError("must-never-appear")

    monkeypatch.setattr(preflight, "load_credentials", denied)
    monkeypatch.setenv("SUPABASE_DB_URL", "https://not-a-postgres-url.invalid")
    path = tmp_path / "missing.json"
    assert cli.main(["collection-preflight", "--canary-file", str(path)]) == 1
    text = capsys.readouterr().out
    report = json.loads(text)
    assert report["blockers"] == [
        "IQ_COLLECTION_CREDENTIALS_UNAVAILABLE",
        "SUPABASE_DB_URL_REQUIRED",
        "COL_RECORDED_CANARY_REQUIRED",
    ]
    assert report["network_checked"] is False and report["research_ready"] is False
    assert "must-never-appear" not in text


def test_preflight_ready_is_not_authentication_or_research_approval(monkeypatch, recorded):
    """R-COL-13: local prerequisites never masquerade as external confirmation."""
    monkeypatch.setattr("strategy_lab.collect.preflight.load_credentials", lambda: object())
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://test@localhost/test")
    report = collection_preflight(canary_path=recorded, now_ts=NOW)
    assert report["status"] == "local_prerequisites_ready"
    assert report["blockers"] == []
    assert report["broker_authenticated"] is False and report["research_ready"] is False


def test_live_collect_preflight_does_not_construct_repository_when_blocked(monkeypatch, tmp_path):
    """R-COL-1/2: missing reference must stop before DB creation or broker login."""
    monkeypatch.setattr(cli, "PostgresRepository", lambda **kw: pytest.fail("No DB allowed"))
    monkeypatch.setattr("strategy_lab.collect.preflight.load_credentials", lambda: object())
    assert cli.main(["collect", "--canary-file", str(tmp_path / "missing.json")]) == 1


def test_collect_exposes_only_stable_failure_codes(monkeypatch, capsys):
    """I-8: database/driver details can never be copied into CLI diagnostics."""
    marker = "private-db-value-must-not-appear"
    monkeypatch.setattr(
        cli,
        "collection_preflight",
        lambda **kwargs: {"blockers": []},
    )

    def fail_repository(**kwargs):
        raise RepositoryError(marker)

    monkeypatch.setattr(cli, "PostgresRepository", fail_repository)
    assert cli.main(["collect"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "COLLECT_ABORTED"
    assert marker not in output


def test_record_canary_cli_uses_existing_read_only_recorder(monkeypatch, tmp_path, capsys):
    """R-VEND-3/R-COL-2: capture is exactly five closed candles, no DB calls."""
    calls = []
    monkeypatch.setattr(Clock, "check_ntp", lambda self: calls.append("ntp"))

    def record(**kwargs):
        calls.append(kwargs)
        return {"count": 5, "asset": kwargs["asset"], "sha256": "a" * 64}

    monkeypatch.setattr(cli, "record_fixture", record)
    assert (
        cli.main(
            [
                "record-canary",
                "--asset",
                "EURUSD-OTC",
                "--from",
                str(START),
                "--output",
                str(tmp_path / "canary.json"),
            ]
        )
        == 0
    )
    assert calls[0] == "ntp" and calls[1]["to_ts"] == START + 300
    assert json.loads(capsys.readouterr().out)["count"] == 5
