from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from packages.market_data.models import CandleEnvelope, ClosedCandle
from packages.market_data.store import (
    CandleStore,
    CandleStoreFullError,
    CandleStoreOutcome,
)


class CandleIngressStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class CandleIngressResult:
    status: CandleIngressStatus
    reason_code: str
    candle: ClosedCandle | None


class CandleIngress:
    def __init__(self, store: CandleStore) -> None:
        self._store = store

    def ingest_external(self, payload: object) -> CandleIngressResult:
        try:
            envelope = CandleEnvelope.from_external_payload(payload)
        except (TypeError, ValueError):
            return CandleIngressResult(
                CandleIngressStatus.INVALID,
                "MARKET_CANDLE_PAYLOAD_INVALID",
                None,
            )
        return self.ingest(envelope)

    def ingest(self, envelope: CandleEnvelope | ClosedCandle) -> CandleIngressResult:
        if isinstance(envelope, ClosedCandle):
            candle = envelope
        else:
            try:
                candle = ClosedCandle.from_envelope(envelope)
            except (TypeError, ValueError):
                return CandleIngressResult(
                    CandleIngressStatus.INVALID,
                    "MARKET_CANDLE_NOT_CLOSED_OR_INVALID",
                    None,
                )
        try:
            outcome = self._store.append(candle)
        except CandleStoreFullError:
            return CandleIngressResult(
                CandleIngressStatus.INVALID,
                "MARKET_CANDLE_STORE_CAPACITY",
                None,
            )
        if outcome is CandleStoreOutcome.STORED:
            return CandleIngressResult(CandleIngressStatus.ACCEPTED, "CANDLE_ACCEPTED", candle)
        if outcome is CandleStoreOutcome.DUPLICATE:
            return CandleIngressResult(CandleIngressStatus.DUPLICATE, "CANDLE_DUPLICATE", candle)
        if outcome is CandleStoreOutcome.OUT_OF_ORDER:
            return CandleIngressResult(
                CandleIngressStatus.OUT_OF_ORDER,
                "CANDLE_OUT_OF_ORDER",
                candle,
            )
        return CandleIngressResult(CandleIngressStatus.INVALID, "CANDLE_GAP", candle)
