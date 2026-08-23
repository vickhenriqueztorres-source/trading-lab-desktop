from __future__ import annotations

import queue
import threading

from apps.core.broker_events import BrokerEventPump
from apps.core.health import HealthGate


class QueuedSource:
    def __init__(self) -> None:
        self.events: queue.Queue[object] = queue.Queue(maxsize=2)

    @property
    def pending_order_event_count(self) -> int:
        return self.events.qsize()

    def receive_order_event(self, timeout: float) -> object | None:
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            return None


class BlockingProcessor:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.processed = 0

    def process(self, _event: object) -> None:
        self.started.set()
        assert self.release.wait(timeout=1.0)
        self.processed += 1


def test_broker_event_drain_waits_for_queued_and_inflight_persistence() -> None:
    source = QueuedSource()
    processor = BlockingProcessor()
    pump = BrokerEventPump(source, processor, HealthGate())  # type: ignore[arg-type]
    source.events.put(object())
    pump.start()
    try:
        assert processor.started.wait(timeout=1.0)
        assert pump.pending_event_count == 1
        assert pump.drain(0.02) is False
        processor.release.set()
        assert pump.drain(1.0) is True
        assert processor.processed == 1
        assert pump.pending_event_count == 0
    finally:
        processor.release.set()
        pump.stop()
