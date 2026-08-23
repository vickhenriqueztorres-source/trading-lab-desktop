from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from packages.domain.models import Broker
from packages.market_data import CandleStoreOutcome, ClosedCandle, SeriesKey
from packages.persistence.strategy_data import StrategyDataDatabase, StrategyDataError


class CandleAppendResult(StrEnum):
    STORED = "STORED"
    ALREADY_EXISTS = "ALREADY_EXISTS"


class CandleConflictError(StrategyDataError):
    reason_code = "CANDLE_CONFLICT"


class CandleRepository(Protocol):
    def store(self, candle: ClosedCandle) -> CandleAppendResult: ...

    def get(self, candle_id: str) -> ClosedCandle | None: ...

    def range(
        self,
        series_key: SeriesKey,
        *,
        start_close_ms: int | None = None,
        end_close_ms: int | None = None,
    ) -> tuple[ClosedCandle, ...]: ...


class SqliteCandleRepository:
    def __init__(self, database: StrategyDataDatabase) -> None:
        self._database = database

    def store(self, candle: ClosedCandle) -> CandleAppendResult:
        with self._database.transaction() as connection:
            by_id = connection.execute(
                "SELECT * FROM candles WHERE candle_id = ?", (candle.candle_id,)
            ).fetchone()
            if by_id is not None:
                if self._row_identity(by_id) != self._identity(candle):
                    raise CandleConflictError("candle id has incompatible persisted content")
                return CandleAppendResult.ALREADY_EXISTS
            same_close = connection.execute(
                """
                SELECT candle_id FROM candles
                WHERE broker = ? AND symbol = ? AND timeframe_seconds = ? AND close_time_ms = ?
                """,
                (*self._series_values(candle), candle.close_time_ms),
            ).fetchone()
            if same_close is not None:
                raise CandleConflictError("candle stream close already has different content")
            connection.execute(
                """
                INSERT INTO candles(
                    candle_id, broker, symbol, timeframe_seconds, open_time_ms, close_time_ms,
                    open_units, high_units, low_units, close_units, price_scale, source,
                    source_event_id, source_timestamp_ms, received_timestamp_ms, inserted_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candle.candle_id,
                    candle.broker.value,
                    candle.symbol,
                    candle.timeframe_seconds,
                    candle.open_time_ms,
                    candle.close_time_ms,
                    candle.open_units,
                    candle.high_units,
                    candle.low_units,
                    candle.close_units,
                    candle.price_scale,
                    candle.source,
                    candle.source_event_id,
                    candle.source_timestamp_ms,
                    candle.received_timestamp_ms,
                    candle.received_timestamp_ms,
                ),
            )
        return CandleAppendResult.STORED

    def append(self, candle: ClosedCandle) -> CandleStoreOutcome:
        existing = self.get(candle.candle_id)
        if existing is not None:
            if self._identity(existing) != self._identity(candle):
                raise CandleConflictError("candle id has incompatible persisted content")
            return CandleStoreOutcome.DUPLICATE
        rows = self._database.query(
            """
            SELECT close_time_ms FROM candles
            WHERE broker = ? AND symbol = ? AND timeframe_seconds = ?
            ORDER BY close_time_ms DESC LIMIT 1
            """,
            self._series_values(candle),
        )
        if rows:
            latest_close = int(rows[0]["close_time_ms"])
            if candle.close_time_ms <= latest_close or candle.open_time_ms < latest_close:
                return CandleStoreOutcome.OUT_OF_ORDER
            if candle.open_time_ms != latest_close:
                return CandleStoreOutcome.GAPPED
        result = self.store(candle)
        return (
            CandleStoreOutcome.STORED
            if result is CandleAppendResult.STORED
            else CandleStoreOutcome.DUPLICATE
        )

    def contains(self, candle_id: str) -> bool:
        return self.get(candle_id) is not None

    def exists(self, candle_id: str) -> bool:
        return self.contains(candle_id)

    def get(self, candle_id: str) -> ClosedCandle | None:
        rows = self._database.query("SELECT * FROM candles WHERE candle_id = ?", (candle_id,))
        return None if not rows else self._from_row(rows[0])

    def range(
        self,
        series_key: SeriesKey,
        *,
        start_close_ms: int | None = None,
        end_close_ms: int | None = None,
    ) -> tuple[ClosedCandle, ...]:
        broker, symbol, timeframe = series_key
        clauses = ["broker = ?", "symbol = ?", "timeframe_seconds = ?"]
        parameters: list[object] = [broker.value, symbol, timeframe]
        if start_close_ms is not None:
            clauses.append("close_time_ms >= ?")
            parameters.append(start_close_ms)
        if end_close_ms is not None:
            clauses.append("close_time_ms <= ?")
            parameters.append(end_close_ms)
        rows = self._database.query(
            f"SELECT * FROM candles WHERE {' AND '.join(clauses)} "
            "ORDER BY close_time_ms, candle_id",
            tuple(parameters),
        )
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _series_values(candle: ClosedCandle) -> tuple[object, ...]:
        return (candle.broker.value, candle.symbol, candle.timeframe_seconds)

    @staticmethod
    def _identity(candle: ClosedCandle) -> tuple[object, ...]:
        return (
            candle.broker.value,
            candle.symbol,
            candle.timeframe_seconds,
            candle.open_time_ms,
            candle.close_time_ms,
            *candle.price_units,
            candle.price_scale,
        )

    @staticmethod
    def _row_identity(row: object) -> tuple[object, ...]:
        values = row  # sqlite Row supports indexed mapping access.
        return (
            str(values["broker"]),  # type: ignore[index]
            str(values["symbol"]),  # type: ignore[index]
            int(values["timeframe_seconds"]),  # type: ignore[index]
            int(values["open_time_ms"]),  # type: ignore[index]
            int(values["close_time_ms"]),  # type: ignore[index]
            int(values["open_units"]),  # type: ignore[index]
            int(values["high_units"]),  # type: ignore[index]
            int(values["low_units"]),  # type: ignore[index]
            int(values["close_units"]),  # type: ignore[index]
            int(values["price_scale"]),  # type: ignore[index]
        )

    @staticmethod
    def _from_row(row: object) -> ClosedCandle:
        values = row
        candle = ClosedCandle(
            broker=Broker(str(values["broker"])),  # type: ignore[index]
            symbol=str(values["symbol"]),  # type: ignore[index]
            timeframe_seconds=int(values["timeframe_seconds"]),  # type: ignore[index]
            open_time_ms=int(values["open_time_ms"]),  # type: ignore[index]
            close_time_ms=int(values["close_time_ms"]),  # type: ignore[index]
            open_units=int(values["open_units"]),  # type: ignore[index]
            high_units=int(values["high_units"]),  # type: ignore[index]
            low_units=int(values["low_units"]),  # type: ignore[index]
            close_units=int(values["close_units"]),  # type: ignore[index]
            price_scale=int(values["price_scale"]),  # type: ignore[index]
            source=str(values["source"]),  # type: ignore[index]
            source_event_id=str(values["source_event_id"]),  # type: ignore[index]
            source_timestamp_ms=int(values["source_timestamp_ms"]),  # type: ignore[index]
            received_timestamp_ms=int(values["received_timestamp_ms"]),  # type: ignore[index]
        )
        if candle.candle_id != str(values["candle_id"]):  # type: ignore[index]
            raise CandleConflictError("persisted candle hash is invalid")
        return candle
