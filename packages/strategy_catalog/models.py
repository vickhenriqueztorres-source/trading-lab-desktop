from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Self

from packages.domain.canonical import canonical_bytes
from packages.domain.models import Broker


class ReleaseStatus(StrEnum):
    DRAFT = "DRAFT"
    BACKTESTED = "BACKTESTED"
    WALK_FORWARD_VALIDATED = "WALK_FORWARD_VALIDATED"
    REPLAY_VALIDATED = "REPLAY_VALIDATED"
    PRACTICE_VALIDATED = "PRACTICE_VALIDATED"
    RELEASED = "RELEASED"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class DataRequirement(StrEnum):
    CLOSED_CANDLES = "CLOSED_CANDLES"


class RiskClass(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    STANDARD = "STANDARD"
    ELEVATED = "ELEVATED"


class ParameterKind(StrEnum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    ENUM = "ENUM"


def _unique_nonempty(values: tuple[str, ...], field: str) -> None:
    if not values or any(not value.strip() for value in values) or len(set(values)) != len(values):
        raise ValueError(f"{field} must contain unique non-empty values")


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    kind: ParameterKind
    required: bool

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("parameter name cannot be empty")

    def to_payload(self) -> dict[str, object]:
        return {"kind": self.kind.value, "name": self.name, "required": self.required}

    @classmethod
    def from_payload(cls, payload: object) -> Self:
        if not isinstance(payload, dict) or set(payload) != {"kind", "name", "required"}:
            raise ValueError("parameter spec schema is invalid")
        name = payload["name"]
        kind = payload["kind"]
        required = payload["required"]
        if not isinstance(name, str) or not isinstance(kind, str) or type(required) is not bool:
            raise ValueError("parameter spec fields are invalid")
        return cls(name=name, kind=ParameterKind(kind), required=required)


@dataclass(frozen=True, slots=True)
class StrategyManifest:
    manifest_version: int
    strategy_id: str
    version: str
    code_hash: str
    supported_brokers: tuple[Broker, ...]
    supported_products: tuple[str, ...]
    supported_timeframes: tuple[int, ...]
    required_data: tuple[DataRequirement, ...]
    warmup_candles: int
    parameter_schema: tuple[ParameterSpec, ...]
    risk_class: RiskClass
    validation_report_id: str
    release_status: ReleaseStatus
    strategy_pack: str

    def __post_init__(self) -> None:
        if self.manifest_version != 1:
            raise ValueError("unsupported strategy manifest version")
        for field in (
            "strategy_id",
            "version",
            "validation_report_id",
            "strategy_pack",
        ):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} cannot be empty")
        if len(self.code_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.code_hash
        ):
            raise ValueError("code_hash must be a lowercase SHA-256 hex digest")
        if not self.supported_brokers or len(set(self.supported_brokers)) != len(
            self.supported_brokers
        ):
            raise ValueError("supported_brokers must be non-empty and unique")
        _unique_nonempty(self.supported_products, "supported_products")
        if (
            not self.supported_timeframes
            or any(value <= 0 for value in self.supported_timeframes)
            or tuple(sorted(set(self.supported_timeframes))) != self.supported_timeframes
        ):
            raise ValueError("supported_timeframes must be sorted, unique and positive")
        if not self.required_data or len(set(self.required_data)) != len(self.required_data):
            raise ValueError("required_data must be non-empty and unique")
        if self.warmup_candles <= 0:
            raise ValueError("warmup_candles must be positive")
        parameter_names = tuple(spec.name for spec in self.parameter_schema)
        if len(set(parameter_names)) != len(parameter_names):
            raise ValueError("parameter_schema names must be unique")

    @property
    def key(self) -> tuple[str, str]:
        return (self.strategy_id, self.version)

    def with_status(self, status: ReleaseStatus) -> StrategyManifest:
        return replace(self, release_status=status)

    def to_payload(self) -> dict[str, object]:
        return {
            "code_hash": self.code_hash,
            "manifest_version": self.manifest_version,
            "parameter_schema": [spec.to_payload() for spec in self.parameter_schema],
            "release_status": self.release_status.value,
            "required_data": [item.value for item in self.required_data],
            "risk_class": self.risk_class.value,
            "strategy_id": self.strategy_id,
            "strategy_pack": self.strategy_pack,
            "supported_brokers": [broker.value for broker in self.supported_brokers],
            "supported_products": list(self.supported_products),
            "supported_timeframes": list(self.supported_timeframes),
            "validation_report_id": self.validation_report_id,
            "version": self.version,
            "warmup_candles": self.warmup_candles,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_payload())

    @classmethod
    def from_external_payload(cls, payload: object) -> StrategyManifest:
        if not isinstance(payload, dict):
            raise ValueError("manifest must be an object")
        required = {
            "code_hash",
            "manifest_version",
            "parameter_schema",
            "release_status",
            "required_data",
            "risk_class",
            "strategy_id",
            "strategy_pack",
            "supported_brokers",
            "supported_products",
            "supported_timeframes",
            "validation_report_id",
            "version",
            "warmup_candles",
        }
        if set(payload) != required:
            raise ValueError("manifest schema is invalid")
        string_fields = (
            "code_hash",
            "release_status",
            "risk_class",
            "strategy_id",
            "strategy_pack",
            "validation_report_id",
            "version",
        )
        if not all(isinstance(payload[field], str) for field in string_fields):
            raise ValueError("manifest string field has invalid type")
        if (
            type(payload["manifest_version"]) is not int
            or type(payload["warmup_candles"]) is not int
        ):
            raise ValueError("manifest integer field has invalid type")
        brokers = cls._string_list(payload["supported_brokers"], "supported_brokers")
        products = cls._string_list(payload["supported_products"], "supported_products")
        data = cls._string_list(payload["required_data"], "required_data")
        timeframes_raw = payload["supported_timeframes"]
        parameters_raw = payload["parameter_schema"]
        if not isinstance(timeframes_raw, list) or any(
            type(item) is not int for item in timeframes_raw
        ):
            raise ValueError("supported_timeframes must be an integer array")
        if not isinstance(parameters_raw, list):
            raise ValueError("parameter_schema must be an array")
        return cls(
            manifest_version=payload["manifest_version"],
            strategy_id=payload["strategy_id"],
            version=payload["version"],
            code_hash=payload["code_hash"],
            supported_brokers=tuple(Broker(value) for value in brokers),
            supported_products=products,
            supported_timeframes=tuple(timeframes_raw),
            required_data=tuple(DataRequirement(value) for value in data),
            warmup_candles=payload["warmup_candles"],
            parameter_schema=tuple(ParameterSpec.from_payload(item) for item in parameters_raw),
            risk_class=RiskClass(payload["risk_class"]),
            validation_report_id=payload["validation_report_id"],
            release_status=ReleaseStatus(payload["release_status"]),
            strategy_pack=payload["strategy_pack"],
        )

    @staticmethod
    def _string_list(value: Any, field: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{field} must be a string array")
        return tuple(value)
