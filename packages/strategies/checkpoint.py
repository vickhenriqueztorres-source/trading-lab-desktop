from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from packages.domain.canonical import canonical_bytes
from packages.strategies.models import RuntimeContext


class RuntimePhase(StrEnum):
    CREATED = "CREATED"
    WARMING_UP = "WARMING_UP"
    READY = "READY"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class StrategyStateV1:
    candle_ids: tuple[str, ...]
    candles_seen: int
    version: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        if not self.candle_ids or len(set(self.candle_ids)) != len(self.candle_ids):
            raise ValueError("strategy state candle ids must be non-empty and unique")
        if self.candles_seen < len(self.candle_ids):
            raise ValueError("strategy state candles_seen is inconsistent")
        if any(len(value) != 64 for value in self.candle_ids):
            raise ValueError("strategy state candle id is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "candle_ids": list(self.candle_ids),
            "candles_seen": self.candles_seen,
            "version": self.version,
        }

    @property
    def state_sha256(self) -> str:
        return hashlib.sha256(canonical_bytes(self.to_payload())).hexdigest()

    @classmethod
    def from_payload(cls, payload: object) -> StrategyStateV1:
        if not isinstance(payload, dict) or set(payload) != {
            "candle_ids",
            "candles_seen",
            "version",
        }:
            raise ValueError("strategy state schema is invalid")
        candle_ids = payload["candle_ids"]
        candles_seen = payload["candles_seen"]
        version = payload["version"]
        if version != 1:
            raise ValueError("strategy state version is unsupported")
        if (
            not isinstance(candle_ids, list)
            or not all(isinstance(value, str) for value in candle_ids)
            or type(candles_seen) is not int
        ):
            raise ValueError("strategy state payload is invalid")
        return cls(tuple(candle_ids), candles_seen)


@dataclass(frozen=True, slots=True)
class WarmupCheckpoint:
    strategy_id: str
    strategy_version: str
    broker: str
    account_id: str
    product: str
    symbol: str
    timeframe_seconds: int
    configuration_version: str
    manifest_sha256: str
    config_sha256: str
    runtime_phase: RuntimePhase
    state: StrategyStateV1
    last_candle_id: str
    last_close_time_ms: int
    created_at_ms: int
    checkpoint_sha256: str

    def __post_init__(self) -> None:
        for field_name in (
            "strategy_id",
            "strategy_version",
            "broker",
            "account_id",
            "product",
            "symbol",
            "configuration_version",
            "last_candle_id",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty")
        if self.timeframe_seconds <= 0 or self.last_close_time_ms < 0 or self.created_at_ms < 0:
            raise ValueError("checkpoint time fields are invalid")
        if self.last_candle_id != self.state.candle_ids[-1]:
            raise ValueError("checkpoint last candle does not match state")
        for digest in (self.manifest_sha256, self.config_sha256, self.checkpoint_sha256):
            if len(digest) != 64:
                raise ValueError("checkpoint digest is invalid")
        if self.checkpoint_sha256 != self.compute_sha256():
            raise ValueError("checkpoint hash is invalid")

    @property
    def candles_seen(self) -> int:
        return self.state.candles_seen

    @property
    def state_sha256(self) -> str:
        return self.state.state_sha256

    def hash_payload(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "broker": self.broker,
            "config_sha256": self.config_sha256,
            "configuration_version": self.configuration_version,
            "created_at_ms": self.created_at_ms,
            "last_candle_id": self.last_candle_id,
            "last_close_time_ms": self.last_close_time_ms,
            "manifest_sha256": self.manifest_sha256,
            "product": self.product,
            "runtime_phase": self.runtime_phase.value,
            "state": self.state.to_payload(),
            "state_sha256": self.state_sha256,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "timeframe_seconds": self.timeframe_seconds,
        }

    def compute_sha256(self) -> str:
        return hashlib.sha256(canonical_bytes(self.hash_payload())).hexdigest()

    @classmethod
    def create(
        cls,
        context: RuntimeContext,
        *,
        manifest_sha256: str,
        config_sha256: str,
        runtime_phase: RuntimePhase,
        state: StrategyStateV1,
        last_close_time_ms: int,
        created_at_ms: int,
    ) -> WarmupCheckpoint:
        provisional = object.__new__(cls)
        values = {
            "strategy_id": context.strategy_id,
            "strategy_version": context.strategy_version,
            "broker": context.broker.value,
            "account_id": context.account_id,
            "product": context.product,
            "symbol": context.symbol,
            "timeframe_seconds": context.timeframe_seconds,
            "configuration_version": context.configuration_version,
            "manifest_sha256": manifest_sha256,
            "config_sha256": config_sha256,
            "runtime_phase": runtime_phase,
            "state": state,
            "last_candle_id": state.candle_ids[-1],
            "last_close_time_ms": last_close_time_ms,
            "created_at_ms": created_at_ms,
        }
        for name, value in values.items():
            object.__setattr__(provisional, name, value)
        object.__setattr__(provisional, "checkpoint_sha256", provisional.compute_sha256())
        provisional.__post_init__()
        return provisional
