from __future__ import annotations

from collections.abc import Mapping

from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.tick_stream import DerivTickStream


class _RecordingTickTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[DerivOperation, dict[str, object]]] = []

    def request(
        self,
        operation: DerivOperation,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]:
        assert timeout > 0
        self.calls.append((operation, dict(payload)))
        return {
            "msg_type": "tick",
            "tick": {"epoch": 1_700_000_100, "quote": "100.120", "symbol": "R_100"},
            "subscription": {"id": "digit-stream-1"},
        }


def test_tick_stream_subscribes_with_exact_live_payload_and_updates_frequency() -> None:
    transport = _RecordingTickTransport()
    stream = DerivTickStream(transport)  # type: ignore[arg-type]

    first = stream.subscribe("R_100")
    second = stream.process_message(
        {
            "msg_type": "tick",
            "tick": {"epoch": 1_700_000_101, "quote": "100.127", "symbol": "R_100"},
            "subscription": {"id": "digit-stream-1"},
        }
    )
    snapshot = stream.snapshot()

    assert transport.calls == [(DerivOperation.TICKS, {"ticks": "R_100", "subscribe": 1})]
    assert first.quote.as_tuple().digits[-1] == 0
    assert second.quote.as_tuple().digits[-1] == 7
    assert snapshot.total_ticks == 2
    assert snapshot.frequency_counts[0] == 1
    assert snapshot.frequency_counts[7] == 1
    assert snapshot.transport_latency_microseconds >= 0


def test_tick_stream_deduplicates_the_same_market_tick() -> None:
    transport = _RecordingTickTransport()
    stream = DerivTickStream(transport)  # type: ignore[arg-type]

    tick = stream.subscribe("R_100")
    stream.ingest_market_tick(tick)

    assert stream.snapshot().total_ticks == 1


def test_tick_stream_rejects_impossible_simulated_clock_latency() -> None:
    transport = _RecordingTickTransport()
    stream = DerivTickStream(
        transport,  # type: ignore[arg-type]
        monotonic_clock=lambda: 10.0,
        wall_clock=lambda: 1_800_000_000.0,
    )

    stream.subscribe("R_100")

    assert stream.snapshot().transport_latency_microseconds == 0
