"""R-VEND-3: deterministic fixture conversion, rejection and pacing."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from primitives import Candle
from strategy_lab.collect.iq_client import (
    FakeIQClient,
    InvalidCandleError,
    IQClient,
    IQClientError,
    IQClientProtocol,
    convert_candle,
    parse_catalog,
)

FIXTURE = Path(__file__).parent / "fixtures/iq/synthetic-EURUSD-OTC.json"
START = 1700000040
END = START + 180
NOW = END + 300


def fake(**kwargs):
    return FakeIQClient(FIXTURE, now=lambda: NOW, pause=lambda seconds: None, **kwargs)


def raw():
    return json.loads(FIXTURE.read_text())["candles"][0]


def test_fake_decimal_payout_otc_and_protocol():
    """R-VEND-3: OHLC exact, payout ratio not percent, OTC never aliases spot."""
    client = fake()
    assert isinstance(client, IQClientProtocol)
    client.login()
    assert client.list_assets() == ["EURUSD-OTC"]
    assert client.fetch_payout("EURUSD-OTC") == Decimal("0.87")
    candles = client.fetch_candles("EURUSD-OTC", 60, 3, END)
    assert len(candles) == 3
    assert type(candles[0].c) is Decimal
    assert candles[0].c == Decimal("1.07007")
    assert isinstance(candles[0], Candle)
    with pytest.raises(IQClientError, match="IQ_ASSET_UNAVAILABLE"):
        client.fetch_candles("EURUSD", 60, 1, END)
    client.logout()
    with pytest.raises(IQClientError, match="IQ_NOT_CONNECTED"):
        client.list_assets()


def test_injected_pause_between_all_backend_calls():
    """R-VEND-3: pause for catalog/candles too; idempotent login emits no extra call."""
    pauses = []
    client = FakeIQClient(
        FIXTURE,
        now=lambda: NOW,
        pause=pauses.append,
        jitter=lambda: 1.25,
    )
    client.login()
    client.login()
    client.fetch_candles("EURUSD-OTC", 60, 3, END)
    client.fetch_payout("EURUSD-OTC")
    client.logout()
    assert pauses == [1.25, 1.25, 1.25]


@pytest.mark.parametrize(
    "field,value",
    [
        ("open", "NaN"),
        ("close", "Infinity"),
        ("min", "1.99"),
        ("max", "0"),
        ("volume", -1),
        ("volume", True),
        ("from", START + 1),
        ("from", True),
        ("from", -60),
        ("to", START + 61),
        ("close", {}),
        ("close", "1e999999"),
        ("close", "1.1" * 100),
    ],
)
def test_invalid_candle_raises_sanitized_payload(field, value):
    """R-VEND-3, I-7/I-8: any invalid value aborts and arbitrary data stays private."""
    payload = raw()
    payload[field] = value
    payload["account"] = {"private": "must_not_escape"}
    with pytest.raises(InvalidCandleError) as caught:
        convert_candle(payload, tf_s=60, end_ts=END, now_ts=NOW)
    assert "account" not in caught.value.payload
    assert "must_not_escape" not in str(caught.value)
    assert "open" in caught.value.payload


def test_numeric_invalid_raw_payload_is_preserved_without_credentials():
    """R-VEND-3: diagnostic OHLC is retained; non-price fields are never retained."""
    payload = raw()
    payload["max"] = "0.10"
    with pytest.raises(InvalidCandleError) as caught:
        convert_candle(payload, tf_s=60, end_ts=END, now_ts=NOW)
    assert caught.value.payload == payload


def test_missing_field_and_non_mapping_rows_rejected():
    """R-VEND-3: a missing field cannot become a default candle."""
    payload = raw()
    del payload["volume"]
    for bad in (payload, [], None):
        with pytest.raises(InvalidCandleError):
            convert_candle(bad, tf_s=60, end_ts=END, now_ts=NOW)


def test_batch_all_or_nothing(tmp_path):
    """R-VEND-3: first valid row must not mask second invalid row."""
    data = json.loads(FIXTURE.read_text())
    data["candles"][1]["min"] = "2"
    fixture = tmp_path / "invalid.json"
    fixture.write_text(json.dumps(data))
    client = FakeIQClient(fixture, now=lambda: NOW, pause=lambda seconds: None)
    client.login()
    with pytest.raises(InvalidCandleError):
        client.fetch_candles("EURUSD-OTC", 60, 3, END)


@pytest.mark.parametrize("change", ["duplicate", "reverse"])
def test_duplicate_or_reverse_batch_rejected(tmp_path, change):
    """R-VEND-3: timestamps must be strictly increasing."""
    data = json.loads(FIXTURE.read_text())
    if change == "duplicate":
        data["candles"][1] = data["candles"][0]
    else:
        data["candles"].reverse()
    fixture = tmp_path / "invalid.json"
    fixture.write_text(json.dumps(data))
    client = FakeIQClient(fixture, now=lambda: NOW, pause=lambda seconds: None)
    client.login()
    with pytest.raises(IQClientError, match="IQ_CANDLE_ORDER_INVALID"):
        client.fetch_candles("EURUSD-OTC", 60, 3, END)


@pytest.mark.parametrize(
    "args",
    [
        ("../x", 60, 1, END),
        ("EURUSD-OTC", 1, 1, END),
        ("EURUSD-OTC", 60, 1001, END),
        ("EURUSD-OTC", 60, True, END),
        ("EURUSD-OTC", 60, 1, NOW),
        ("EURUSD-OTC", 60, 0, END),
    ],
)
def test_invalid_query_has_no_network(args):
    """R-VEND-3: input validation precedes any connection/catalog query."""
    client = fake()
    with pytest.raises(IQClientError):
        client.fetch_candles(*args)
    assert client._backend.calls == []


@pytest.mark.parametrize("delay", [-1, 0, 2.01, float("nan")])
def test_invalid_pause_fail_closed(delay):
    """R-VEND-3: pacing injection cannot accidentally remove the production limit."""
    client = fake(jitter=lambda: delay)
    client.login()
    with pytest.raises(IQClientError, match="IQ_INVALID_PAUSE"):
        client.list_assets()


def test_guarded_current_candle_and_higher_timeframe():
    """R-VEND-3, I-3: strict current-candle margin and higher-TF closure."""
    with pytest.raises(InvalidCandleError):
        convert_candle(raw(), tf_s=60, end_ts=END, now_ts=START + 60)
    with pytest.raises(InvalidCandleError):
        convert_candle(raw(), tf_s=300, end_ts=END, now_ts=NOW)


def test_catalog_decimal_and_missing_payout():
    """R-VEND-3: no binary fallback and no coercive zero for missing turbo payout."""

    def catalog(commission):
        return {
            "result": {
                "turbo": {
                    "actives": {
                        "7": {
                            "name": "front.EURUSD-OTC",
                            "option": {"profit": {"commission": commission}},
                        }
                    }
                }
            }
        }

    assert parse_catalog(catalog(None)) == {"EURUSD-OTC": (7, None)}
    assert parse_catalog(catalog("13.123456"))["EURUSD-OTC"][1] == Decimal("0.86876544")
    for bad in (-1, 101, "NaN", "redacted"):
        with pytest.raises(IQClientError, match="IQ_INVALID_CATALOG"):
            parse_catalog(catalog(bad))
    with pytest.raises(IQClientError):
        parse_catalog({"result": {"binary": {"actives": {}}}})


def test_login_failure_is_not_success():
    """R-VEND-3: failed login leaves the client disconnected and cleans up."""

    class Broken:
        closed = False

        def connect(self):
            raise RuntimeError("synthetic_private_data")

        def close(self):
            self.closed = True

    backend = Broken()
    client = IQClient(backend=backend)
    with pytest.raises(IQClientError, match="^IQ_LOGIN_FAILED$"):
        client.login()
    assert backend.closed
    with pytest.raises(IQClientError, match="IQ_NOT_CONNECTED"):
        client.list_assets()
