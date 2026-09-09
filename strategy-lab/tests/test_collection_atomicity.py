"""R-COL-5/9: all assets/lots validate before one transaction; bounded memory."""

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from primitives import Candle
from strategy_lab.collect.buffer import CollectionBuffer
from strategy_lab.collect.clock import Clock
from strategy_lab.collect.iq_client import IQClientError
from strategy_lab.collect.payout_sampler import sample_payout
from strategy_lab.collect.pg_repository import PostgresRepository
from strategy_lab.collect.repository import FakeRepository, RepositoryError, source_for_asset
from strategy_lab.collect.runner import run_collect

START = 1700000040


def test_payout_timestamp_uses_receipt_hour_after_collection_delay():
    """R-COL-8: slow login/backfill cannot backdate a newly observed payout."""
    repository = FakeRepository()
    before = START // 3600 * 3600 + 3599
    after = before + 2
    sample_payout(
        client=BatchClient(),
        repository=repository,
        asset="EURUSD",
        now_ts=before,
        observed_clock=lambda: after,
    )
    assert ("EURUSD", after) in repository.payout_observations
    assert ("EURUSD", after // 3600 * 3600) in repository.payouts
    assert ("EURUSD", before // 3600 * 3600) not in repository.payouts


def candles(start, count):
    return [
        Candle(
            ts=start + i * 60,
            o=Decimal("1"),
            h=Decimal("1.01"),
            l=Decimal("0.99"),
            c=Decimal("1"),
            tick_vol=1,
        )
        for i in range(count)
    ]


class BatchClient:
    def __init__(self, *, fail_asset=None, corrupt_after=0, jump=False):
        self.fail_asset = fail_asset
        self.corrupt_after = corrupt_after
        self.batches = 0
        self.closed = False
        self.jump = jump

    def login(self):
        pass

    def logout(self):
        self.closed = True

    def fetch_payout(self, asset):
        return Decimal("0.85")

    def list_assets(self):
        return ["EURUSD", "GBPUSD"]

    def fetch_candles(self, asset, tf_s, n, end_ts):
        self.batches += 1
        rows = candles(end_ts - n * 60, n)
        if asset == self.fail_asset or (self.corrupt_after and self.batches > self.corrupt_after):
            rows[-1] = rows[-1].model_copy(update={"h": Decimal("0")})
        if self.jump and n >= 16:
            last = rows[-1]
            rows[-1] = last.model_copy(
                update={
                    "o": Decimal("3"),
                    "c": Decimal("3"),
                    "h": Decimal("3.01"),
                    "l": Decimal("2.99"),
                }
            )
        return rows


def run(client, repository, monkeypatch, *, count=30, assets=None):
    # The immutable canary itself has separate end-to-end tests. Isolate post-canary failure here.
    monkeypatch.setattr("strategy_lab.collect.runner.run_canary", lambda *args, **kwargs: None)
    return run_collect(
        assets=assets or ["EURUSD"],
        repository=repository,
        clock=Clock(lambda: START + (count + 1) * 60),
        client_factory=lambda: client,
        check_ntp=False,
        initial_from_ts=START,
    )


@pytest.mark.parametrize("scenario", ["late_batch", "second_asset", "jump"])
def test_invalid_late_data_never_partially_persists(monkeypatch, scenario):
    """R-COL-5/9: late bad data cannot leave earlier candles or payout persisted."""
    repository = FakeRepository()
    client = BatchClient(
        corrupt_after=1 if scenario == "late_batch" else 0,
        fail_asset="GBPUSD" if scenario == "second_asset" else None,
        jump=scenario == "jump",
    )
    with pytest.raises((IQClientError, RepositoryError)):
        run(
            client,
            repository,
            monkeypatch,
            count=1001 if scenario == "late_batch" else 30,
            assets=["EURUSD", "GBPUSD"],
        )
    assert repository.candles == {} and repository.payouts == {}
    assert repository.payout_observations == {} and repository.gaps == [] and repository.runs == []
    assert client.closed


def test_transaction_failure_rolls_back_prices_payout_and_run(monkeypatch):
    """R-COL-5/I-7: persistence failure after valid data rolls back the whole run."""

    class FailingRepository(FakeRepository):
        def record_run(self, report):
            raise RepositoryError("TEST_COMMIT_FAILURE")

    repository = FailingRepository()
    with pytest.raises(RepositoryError, match="TEST_COMMIT_FAILURE"):
        run(BatchClient(), repository, monkeypatch)
    assert repository.candles == {} and repository.payouts == {}
    assert repository.payout_observations == {} and repository.durable_watermarks == {}


def test_valid_collection_is_idempotent_and_reports_actual_inserts(monkeypatch):
    """R-COL-3/6/10: three runs same clock => one candle set and one payout observation."""
    repository = FakeRepository()
    reports = [run(BatchClient(), repository, monkeypatch) for _ in range(3)]
    assert [report["assets"][0]["written"] for report in reports] == [30, 0, 0]
    assert len(repository.candles) == 30 and len(repository.payout_observations) == 1
    assert len(repository.runs) == 3


def test_collection_buffer_exhaustion_fails_before_backend_write(monkeypatch):
    """R-COL-5/I-7: bounded memory cannot silently truncate an oversized run."""
    monkeypatch.setattr("strategy_lab.collect.buffer.MAX_BUFFERED_RECORDS", 10)
    repository = FakeRepository()
    with pytest.raises(RepositoryError, match="COL_BUFFER_BUDGET_EXHAUSTED"):
        run(BatchClient(), repository, monkeypatch)
    assert repository.candles == {} and repository.payouts == {} and repository.runs == []


def test_series_invariant_checks_cover_batch_boundary(monkeypatch):
    """R-COL-9: retained validation context detects a discontinuity between lots."""
    repository = FakeRepository()
    buffer = CollectionBuffer(repository)
    source = source_for_asset("EURUSD", "test")
    buffer.upsert_candles(candles(START, 1000), source)
    next_rows = [
        c.model_copy(
            update={
                "o": Decimal("3"),
                "h": Decimal("3.01"),
                "l": Decimal("2.99"),
                "c": Decimal("3"),
            }
        )
        for c in candles(START + 1000 * 60, 2)
    ]
    buffer.upsert_candles(next_rows, source)
    with pytest.raises(RepositoryError, match="COL_COLLECTION_INVARIANT_FAILED"):
        buffer.validate()
    assert repository.candles == {}


def test_postgres_transaction_reuses_connection_and_propagates_rollback(monkeypatch):
    """R-COL-5: writer uses one connection context; driver gets the failure for rollback."""
    import strategy_lab.collect.pg_repository as module

    outcomes = []
    connection = object()

    @contextmanager
    def connect(url):
        outcomes.append("connect")
        try:
            yield connection
        except RepositoryError:
            outcomes.append("rollback")
            raise
        else:
            outcomes.append("commit")

    original_import = module.importlib.import_module
    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(connect=connect) if name == "psycopg" else original_import(name)
        ),
    )
    repository = PostgresRepository("postgresql://fake@localhost/test")
    with pytest.raises(RepositoryError, match="failure"), repository.transaction():
        with repository._connect() as first, repository._connect() as second:
            assert first is connection and second is connection
        raise RepositoryError("failure")
    assert outcomes == ["connect", "rollback"]
    assert repository._transaction_connection is None
    with repository.transaction(), repository._connect() as third:
        assert third is connection
    assert outcomes == ["connect", "rollback", "connect", "commit"]
