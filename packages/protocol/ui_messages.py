from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from math import isfinite
from typing import TypeGuard, cast

from packages.domain.models import require_aware_utc
from packages.market_data import DigitFrequencySnapshot
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.security import SecretValue

_HEX_CHARS = 64
_MAX_TEXT = 128
_MAX_GATES = 32
_MAX_BROKERS = 4
_MAX_ORDERS = 100
_MAX_DERIV_STRATEGIES = 36
_MAX_DERIV_ASSET_RANKS = 16
# IQ Option currently exposes well over the legacy 32-pair sample once Binary,
# Turbo and Digital markets are discovered dynamically. Keep a hard IPC bound,
# but size it for the broker catalogue rather than a hand-written shortlist.
_MAX_IQOPTION_ASSET_RANKS = 512
_MAX_UI_LOG_ENTRIES = 160
_MAX_UI_LOG_DETAIL = 384


class UiHandshakeStatus(StrEnum):
    OK = "OK"
    DENIED = "DENIED"


class UiGlobalState(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    SAFE_STOPPED = "SAFE_STOPPED"
    RECONCILING = "RECONCILING"
    RISK_LOCKED = "RISK_LOCKED"


class UiLogLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class UiAccountMode(StrEnum):
    PRACTICE = "PRACTICE"
    DEMO_READ_ONLY = "DEMO_READ_ONLY"
    REAL = "REAL"


class UiBalanceQuality(StrEnum):
    CONFIRMED = "CONFIRMED"
    RETRYING = "RETRYING"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class UiDigitRiskConfigStatus(StrEnum):
    OK = "OK"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class UiIqOptionLoginCommand:
    account_mode: str
    source: str = "manual"

    def __post_init__(self) -> None:
        normalized = self.account_mode.strip().lower()
        if normalized not in {"practice", "real", "saved"}:
            raise ValueError("IQ Option account mode is invalid")
        normalized_source = self.source.strip().lower()
        if normalized_source not in {"auto", "manual"}:
            raise ValueError("IQ Option login source is invalid")
        object.__setattr__(self, "account_mode", normalized)
        object.__setattr__(self, "source", normalized_source)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"account_mode": self.account_mode}
        if self.source != "manual":
            payload["source"] = self.source
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiIqOptionLoginCommand:
        if set(payload) not in ({"account_mode"}, {"account_mode", "source"}):
            raise _invalid()
        try:
            return cls(
                _string(payload, "account_mode", 16),
                "manual" if "source" not in payload else _string(payload, "source", 8),
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiIqOptionLoginAck:
    accepted: bool
    connected: bool
    reason_code: str
    retry_after_seconds: int = 0
    attempts_in_window: int = 0

    def __post_init__(self) -> None:
        if not self.reason_code or len(self.reason_code) > 64:
            raise ValueError("IQ Option login reason is invalid")
        if self.connected and not self.accepted:
            raise ValueError("connected login must be accepted")
        if self.retry_after_seconds < 0 or self.attempts_in_window < 0:
            raise ValueError("IQ Option login retry metadata is invalid")

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "accepted": self.accepted,
            "connected": self.connected,
            "reason_code": self.reason_code,
        }
        if self.retry_after_seconds or self.attempts_in_window:
            payload["retry_after_seconds"] = self.retry_after_seconds
            payload["attempts_in_window"] = self.attempts_in_window
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiIqOptionLoginAck:
        base_fields = {"accepted", "connected", "reason_code"}
        extended_fields = {*base_fields, "retry_after_seconds", "attempts_in_window"}
        if set(payload) not in (base_fields, extended_fields):
            raise _invalid()
        accepted = payload.get("accepted")
        connected = payload.get("connected")
        if not isinstance(accepted, bool) or not isinstance(connected, bool):
            raise _invalid()
        retry_after = payload.get("retry_after_seconds", 0)
        attempts = payload.get("attempts_in_window", 0)
        if type(retry_after) is not int or type(attempts) is not int:
            raise _invalid()
        try:
            return cls(
                accepted,
                connected,
                _string(payload, "reason_code", 64),
                retry_after,
                attempts,
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiIqOptionRiskConfig:
    strategy_id: str = "iqoption-rsi-demo"
    symbol: str = "EURUSD-OTC"
    timeframe_seconds: int = 60
    duration_seconds: int = 60
    stake_minor_units: int = 100
    daily_stop_loss_minor_units: int = 1_000
    daily_take_profit_minor_units: int = 1_000
    max_consecutive_losses: int = 3
    cooldown_seconds_after_loss: int = 30
    max_daily_trades: int = 10
    max_concurrent_positions: int = 1
    currency: str = "USD"
    martingale_enabled: bool = False
    martingale_multiplier_basis_points: int = 20_000
    martingale_max_steps: int = 1
    martingale_max_stake_minor_units: int = 400

    @property
    def active_strategy_key(self) -> str:
        return self.strategy_id

    def __post_init__(self) -> None:
        if not self.strategy_id or len(self.strategy_id) > 128 or not self.symbol.strip():
            raise ValueError("IQ Option strategy selection is invalid")
        if self.timeframe_seconds not in {60, 300, 900} or self.duration_seconds != 60:
            raise ValueError("IQ Option RSI interval is invalid")
        if type(self.stake_minor_units) is not int or not 100 <= self.stake_minor_units <= 10_000:
            raise ValueError("IQ Option stake is invalid")
        for value in (self.daily_stop_loss_minor_units, self.daily_take_profit_minor_units):
            if type(value) is not int or value < self.stake_minor_units or value > 1_000_000:
                raise ValueError("IQ Option daily limit is invalid")
        if (
            type(self.max_consecutive_losses) is not int
            or not 1 <= self.max_consecutive_losses <= 10
        ):
            raise ValueError("IQ Option consecutive loss limit is invalid")
        if (
            type(self.cooldown_seconds_after_loss) is not int
            or not 0 <= self.cooldown_seconds_after_loss <= 3_600
        ):
            raise ValueError("IQ Option cooldown is invalid")
        if type(self.max_daily_trades) is not int or not 1 <= self.max_daily_trades <= 100:
            raise ValueError("IQ Option daily trade limit is invalid")
        if self.max_concurrent_positions != 1 or self.currency != "USD":
            raise ValueError("IQ Option execution constraints are invalid")
        if type(self.martingale_enabled) is not bool:
            raise ValueError("IQ Option martingale selection is invalid")
        if (
            type(self.martingale_multiplier_basis_points) is not int
            or not 11_000 <= self.martingale_multiplier_basis_points <= 30_000
        ):
            raise ValueError("IQ Option martingale multiplier is invalid")
        if type(self.martingale_max_steps) is not int or self.martingale_max_steps not in {1, 2}:
            raise ValueError("IQ Option martingale level is invalid")
        if (
            type(self.martingale_max_stake_minor_units) is not int
            or self.martingale_max_stake_minor_units <= 0
        ):
            raise ValueError("IQ Option martingale stake cap is invalid")
        if self.martingale_enabled and (
            self.martingale_max_stake_minor_units < self.stake_minor_units
            or self.martingale_max_stake_minor_units > self.daily_stop_loss_minor_units
            or self.max_consecutive_losses < self.martingale_max_steps + 1
            or self.max_daily_trades < self.martingale_max_steps + 1
        ):
            raise ValueError("IQ Option martingale limits are invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timeframe_seconds": self.timeframe_seconds,
            "duration_seconds": self.duration_seconds,
            "stake_minor_units": self.stake_minor_units,
            "daily_stop_loss_minor_units": self.daily_stop_loss_minor_units,
            "daily_take_profit_minor_units": self.daily_take_profit_minor_units,
            "max_consecutive_losses": self.max_consecutive_losses,
            "cooldown_seconds_after_loss": self.cooldown_seconds_after_loss,
            "max_daily_trades": self.max_daily_trades,
            "max_concurrent_positions": self.max_concurrent_positions,
            "currency": self.currency,
            "martingale_enabled": self.martingale_enabled,
            "martingale_multiplier_basis_points": self.martingale_multiplier_basis_points,
            "martingale_max_steps": self.martingale_max_steps,
            "martingale_max_stake_minor_units": self.martingale_max_stake_minor_units,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiIqOptionRiskConfig:
        if "active_strategy_key" in payload:
            payload = dict(payload)
            key = payload.pop("active_strategy_key")
            if "strategy_id" in payload and payload["strategy_id"] != key:
                raise _invalid()
            payload["strategy_id"] = key
        required_fields = {
            "strategy_id",
            "symbol",
            "timeframe_seconds",
            "duration_seconds",
            "stake_minor_units",
            "daily_stop_loss_minor_units",
            "daily_take_profit_minor_units",
            "max_consecutive_losses",
            "cooldown_seconds_after_loss",
            "max_daily_trades",
            "max_concurrent_positions",
            "currency",
        }
        martingale_fields = {
            "martingale_enabled",
            "martingale_multiplier_basis_points",
            "martingale_max_steps",
            "martingale_max_stake_minor_units",
        }
        if not required_fields.issubset(payload) or not set(payload).issubset(
            required_fields | martingale_fields
        ):
            raise _invalid()
        integer_fields = required_fields - {"strategy_id", "symbol", "currency"}
        if any(type(payload.get(field)) is not int for field in integer_fields):
            raise _invalid()
        martingale_enabled = payload.get("martingale_enabled", False)
        multiplier = payload.get("martingale_multiplier_basis_points", 20_000)
        max_steps = payload.get("martingale_max_steps", 1)
        max_stake = payload.get(
            "martingale_max_stake_minor_units",
            min(400, cast(int, payload["daily_stop_loss_minor_units"])),
        )
        if (
            type(martingale_enabled) is not bool
            or type(multiplier) is not int
            or type(max_steps) is not int
            or type(max_stake) is not int
        ):
            raise _invalid()
        try:
            return cls(
                strategy_id=_string(payload, "strategy_id", 128),
                symbol=_string(payload, "symbol", 32),
                timeframe_seconds=cast(int, payload["timeframe_seconds"]),
                duration_seconds=cast(int, payload["duration_seconds"]),
                stake_minor_units=cast(int, payload["stake_minor_units"]),
                daily_stop_loss_minor_units=cast(int, payload["daily_stop_loss_minor_units"]),
                daily_take_profit_minor_units=cast(int, payload["daily_take_profit_minor_units"]),
                max_consecutive_losses=cast(int, payload["max_consecutive_losses"]),
                cooldown_seconds_after_loss=cast(int, payload["cooldown_seconds_after_loss"]),
                max_daily_trades=cast(int, payload["max_daily_trades"]),
                max_concurrent_positions=cast(int, payload["max_concurrent_positions"]),
                currency=_string(payload, "currency", 3),
                martingale_enabled=martingale_enabled,
                martingale_multiplier_basis_points=multiplier,
                martingale_max_steps=max_steps,
                martingale_max_stake_minor_units=max_stake,
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiUpdateIqOptionRiskConfigCommand:
    config: UiIqOptionRiskConfig

    def to_payload(self) -> dict[str, object]:
        return {"config": self.config.to_payload()}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiUpdateIqOptionRiskConfigCommand:
        _exact(payload, {"config"})
        return cls(UiIqOptionRiskConfig.from_payload(_mapping(payload.get("config"))))


@dataclass(frozen=True, slots=True)
class UiIqOptionBotControlCommand:
    enabled: bool

    def to_payload(self) -> dict[str, object]:
        return {"enabled": self.enabled}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiIqOptionBotControlCommand:
        _exact(payload, {"enabled"})
        enabled = payload.get("enabled")
        if type(enabled) is not bool:
            raise _invalid()
        return cls(enabled)


def _invalid() -> ProtocolError:
    return ProtocolError(ProtocolErrorCode.UI_IPC_INVALID_MESSAGE, "UI IPC payload is invalid")


def _exact(payload: Mapping[str, object], fields: set[str]) -> None:
    if set(payload) != fields:
        raise _invalid()


def _string(payload: Mapping[str, object], field: str, maximum: int = _MAX_TEXT) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise _invalid()
    return value.strip()


def _optional_string(
    payload: Mapping[str, object], field: str, maximum: int = _MAX_TEXT
) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise _invalid()
    return value.strip()


def _hex(value: str) -> str:
    if len(value) != _HEX_CHARS:
        raise _invalid()
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise _invalid() from exc
    return value


@dataclass(frozen=True, slots=True, repr=False)
class UiHandshakeRequest:
    session_token: SecretValue
    ui_version: str
    client_nonce: str

    def to_payload(self) -> dict[str, object]:
        return {
            "client_nonce": self.client_nonce,
            "session_token": self.session_token.reveal_text(),
            "ui_version": self.ui_version,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiHandshakeRequest:
        _exact(payload, {"client_nonce", "session_token", "ui_version"})
        return cls(
            SecretValue.from_text(_hex(_string(payload, "session_token", _HEX_CHARS))),
            _string(payload, "ui_version", 32),
            _hex(_string(payload, "client_nonce", _HEX_CHARS)),
        )

    def __repr__(self) -> str:
        return "UiHandshakeRequest(<redacted>)"


@dataclass(frozen=True, slots=True)
class UiHandshakeResponse:
    status: UiHandshakeStatus
    core_version: str
    server_nonce: str | None
    server_proof: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "core_version": self.core_version,
            "server_nonce": self.server_nonce,
            "server_proof": self.server_proof,
            "status": self.status.value,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiHandshakeResponse:
        _exact(payload, {"core_version", "server_nonce", "server_proof", "status"})
        try:
            status = UiHandshakeStatus(_string(payload, "status", 16))
        except ValueError as exc:
            raise _invalid() from exc
        nonce = _optional_string(payload, "server_nonce", _HEX_CHARS)
        proof = _optional_string(payload, "server_proof", _HEX_CHARS)
        if status is UiHandshakeStatus.OK:
            if nonce is None or proof is None:
                raise _invalid()
            _hex(nonce)
            _hex(proof)
        elif nonce is not None or proof is not None:
            raise _invalid()
        return cls(status, _string(payload, "core_version", 32), nonce, proof)


@dataclass(frozen=True, slots=True)
class HealthGateStatus:
    gate_name: str
    is_open: bool
    reason_code: str | None
    description: str

    def __post_init__(self) -> None:
        if not self.gate_name or len(self.gate_name) > 64:
            raise ValueError("health gate name is invalid")
        if self.is_open != (self.reason_code is None):
            raise ValueError("health gate reason/open state is inconsistent")
        if self.reason_code is not None and len(self.reason_code) > 64:
            raise ValueError("health reason code is invalid")
        if not self.description or len(self.description) > 256:
            raise ValueError("health description is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "description": self.description,
            "gate_name": self.gate_name,
            "is_open": self.is_open,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> HealthGateStatus:
        _exact(payload, {"description", "gate_name", "is_open", "reason_code"})
        is_open = payload.get("is_open")
        if not isinstance(is_open, bool):
            raise _invalid()
        try:
            return cls(
                _string(payload, "gate_name", 64),
                is_open,
                _optional_string(payload, "reason_code", 64),
                _string(payload, "description", 256),
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class BrokerCardStatus:
    broker: str
    account_mode: UiAccountMode
    is_connected: bool
    balance_minor_units: int | None
    currency: str | None
    clock_synced: bool
    connection_label: str = "UNKNOWN"
    clock_latency_ms: int | None = None
    balance_observed_at_utc: datetime | None = None
    balance_is_fresh: bool | None = None
    balance_quality: UiBalanceQuality | None = None
    balance_age_seconds: int | None = None
    balance_retry_count: int | None = None

    def __post_init__(self) -> None:
        if not self.broker or len(self.broker) > 32:
            raise ValueError("broker card broker is invalid")
        if self.balance_minor_units is not None and type(self.balance_minor_units) is not int:
            raise TypeError("broker balance must use integer minor units")
        if (self.balance_minor_units is None) != (self.currency is None):
            raise ValueError("broker balance and currency availability must match")
        if self.currency is not None and (
            len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isalpha()
        ):
            raise ValueError("broker card currency is invalid")
        if not self.connection_label or len(self.connection_label) > 64:
            raise ValueError("broker connection label is invalid")
        if self.clock_latency_ms is not None and (
            type(self.clock_latency_ms) is not int or self.clock_latency_ms < 0
        ):
            raise ValueError("broker clock latency is invalid")
        if self.balance_observed_at_utc is not None:
            require_aware_utc(self.balance_observed_at_utc, "balance_observed_at_utc")
            if self.balance_minor_units is None:
                raise ValueError("broker balance observation requires a balance")
        if self.balance_is_fresh is not None and type(self.balance_is_fresh) is not bool:
            raise TypeError("broker balance freshness must be boolean")
        if self.balance_is_fresh is True and self.balance_observed_at_utc is None:
            raise ValueError("fresh broker balance requires an observation timestamp")
        if self.balance_quality is not None and not isinstance(
            self.balance_quality, UiBalanceQuality
        ):
            raise TypeError("broker balance quality is invalid")
        for value, label in (
            (self.balance_age_seconds, "age"),
            (self.balance_retry_count, "retry count"),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"broker balance {label} is invalid")
        if (
            self.balance_quality is not None
            and self.balance_minor_units is None
            and self.balance_quality is not UiBalanceQuality.UNAVAILABLE
        ):
            raise ValueError("broker balance quality requires a balance")

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "account_mode": self.account_mode.value,
            "balance_minor_units": self.balance_minor_units,
            "broker": self.broker,
            "clock_synced": self.clock_synced,
            "clock_latency_ms": self.clock_latency_ms,
            "connection_label": self.connection_label,
            "currency": self.currency,
            "is_connected": self.is_connected,
        }
        if self.balance_observed_at_utc is not None:
            payload["balance_observed_at_utc"] = self.balance_observed_at_utc.isoformat()
        if self.balance_is_fresh is not None:
            payload["balance_is_fresh"] = self.balance_is_fresh
        if self.balance_quality is not None:
            payload["balance_quality"] = self.balance_quality.value
        if self.balance_age_seconds is not None:
            payload["balance_age_seconds"] = self.balance_age_seconds
        if self.balance_retry_count is not None:
            payload["balance_retry_count"] = self.balance_retry_count
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> BrokerCardStatus:
        required = {
            "account_mode",
            "balance_minor_units",
            "broker",
            "clock_synced",
            "clock_latency_ms",
            "connection_label",
            "currency",
            "is_connected",
        }
        optional = {
            "balance_observed_at_utc",
            "balance_is_fresh",
            "balance_quality",
            "balance_age_seconds",
            "balance_retry_count",
        }
        if not required.issubset(payload) or not set(payload).issubset(required | optional):
            raise _invalid()
        connected = payload.get("is_connected")
        clock = payload.get("clock_synced")
        balance = payload.get("balance_minor_units")
        latency = payload.get("clock_latency_ms")
        freshness = payload.get("balance_is_fresh")
        quality = payload.get("balance_quality")
        age = payload.get("balance_age_seconds")
        retries = payload.get("balance_retry_count")
        observed_raw = payload.get("balance_observed_at_utc")
        if (
            not isinstance(connected, bool)
            or not isinstance(clock, bool)
            or (balance is not None and type(balance) is not int)
            or (latency is not None and type(latency) is not int)
            or (freshness is not None and type(freshness) is not bool)
            or (quality is not None and not isinstance(quality, str))
            or (age is not None and type(age) is not int)
            or (retries is not None and type(retries) is not int)
            or (observed_raw is not None and not isinstance(observed_raw, str))
        ):
            raise _invalid()
        try:
            observed_at = None if observed_raw is None else datetime.fromisoformat(observed_raw)
            return cls(
                _string(payload, "broker", 32),
                UiAccountMode(_string(payload, "account_mode", 32)),
                connected,
                balance,
                _optional_string(payload, "currency", 3),
                clock,
                _string(payload, "connection_label", 64),
                latency,
                observed_at,
                freshness,
                None if quality is None else UiBalanceQuality(quality),
                age,
                retries,
            )
        except (TypeError, ValueError) as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class OrderSummary:
    order_id: str
    broker: str
    symbol: str
    direction: str
    amount_minor_units: int
    currency: str
    state: str
    created_at_utc: datetime
    broker_order_id: str | None = None
    realized_pnl_minor_units: int | None = None
    result_review_required: bool = False
    reconciliation_attempt_count: int = 0
    reconciliation_review_required: bool = False
    reconciliation_next_due_at: datetime | None = None

    def __post_init__(self) -> None:
        require_aware_utc(self.created_at_utc, "created_at_utc")
        for value in (self.order_id, self.broker, self.symbol, self.direction, self.state):
            if not value or len(value) > _MAX_TEXT:
                raise ValueError("order summary identifier is invalid")
        if self.broker_order_id is not None and (
            not self.broker_order_id or len(self.broker_order_id) > _MAX_TEXT
        ):
            raise ValueError("order summary broker_order_id is invalid")
        if type(self.amount_minor_units) is not int or self.amount_minor_units <= 0:
            raise ValueError("order amount must use positive integer minor units")
        if (
            self.realized_pnl_minor_units is not None
            and type(self.realized_pnl_minor_units) is not int
        ):
            raise ValueError("realized P&L must use integer minor units")
        if type(self.result_review_required) is not bool:
            raise TypeError("result review flag must be boolean")
        if (
            type(self.reconciliation_attempt_count) is not int
            or self.reconciliation_attempt_count < 0
        ):
            raise ValueError("reconciliation attempt count is invalid")
        if type(self.reconciliation_review_required) is not bool:
            raise TypeError("reconciliation review flag must be boolean")
        if self.reconciliation_next_due_at is not None:
            require_aware_utc(
                self.reconciliation_next_due_at,
                "reconciliation_next_due_at",
            )
        if len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isalpha():
            raise ValueError("order currency is invalid")

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "amount_minor_units": self.amount_minor_units,
            "broker": self.broker,
            "broker_order_id": self.broker_order_id,
            "created_at_utc": self.created_at_utc.isoformat(),
            "currency": self.currency,
            "direction": self.direction,
            "order_id": self.order_id,
            "state": self.state,
            "symbol": self.symbol,
        }
        if self.realized_pnl_minor_units is not None:
            payload["realized_pnl_minor_units"] = self.realized_pnl_minor_units
        if self.result_review_required:
            payload["result_review_required"] = True
        if self.reconciliation_attempt_count:
            payload["reconciliation_attempt_count"] = self.reconciliation_attempt_count
        if self.reconciliation_review_required:
            payload["reconciliation_review_required"] = True
        if self.reconciliation_next_due_at is not None:
            payload["reconciliation_next_due_at"] = self.reconciliation_next_due_at.isoformat()
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> OrderSummary:
        keys = set(payload)
        expected = {
            "amount_minor_units",
            "broker",
            "created_at_utc",
            "currency",
            "direction",
            "order_id",
            "state",
            "symbol",
        }
        optional = {
            "broker_order_id",
            "realized_pnl_minor_units",
            "result_review_required",
            "reconciliation_attempt_count",
            "reconciliation_review_required",
            "reconciliation_next_due_at",
        }
        if not expected.issubset(keys) or not keys.issubset(expected | optional):
            raise _invalid()
        amount = payload.get("amount_minor_units")
        if type(amount) is not int:
            raise _invalid()
        broker_order_id = _optional_string(payload, "broker_order_id")
        realized_pnl = payload.get("realized_pnl_minor_units")
        if realized_pnl is not None and type(realized_pnl) is not int:
            raise _invalid()
        review_required = payload.get("result_review_required", False)
        if type(review_required) is not bool:
            raise _invalid()
        attempt_count = payload.get("reconciliation_attempt_count", 0)
        reconciliation_review = payload.get("reconciliation_review_required", False)
        next_due_raw = payload.get("reconciliation_next_due_at")
        if (
            type(attempt_count) is not int
            or type(reconciliation_review) is not bool
            or (next_due_raw is not None and not isinstance(next_due_raw, str))
        ):
            raise _invalid()
        try:
            created = datetime.fromisoformat(_string(payload, "created_at_utc", 64))
            next_due = None if next_due_raw is None else datetime.fromisoformat(next_due_raw)
            return cls(
                _string(payload, "order_id"),
                _string(payload, "broker", 32),
                _string(payload, "symbol", 64),
                _string(payload, "direction", 16),
                amount,
                _string(payload, "currency", 3).upper(),
                _string(payload, "state", 32),
                created,
                broker_order_id,
                realized_pnl,
                review_required,
                attempt_count,
                reconciliation_review,
                next_due,
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiOperationalLogEntry:
    """Bounded, sanitised operational event safe for presentation by the UI."""

    occurred_at_utc: datetime
    level: UiLogLevel
    source: str
    event_name: str
    reason_code: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        require_aware_utc(self.occurred_at_utc, "occurred_at_utc")
        if not isinstance(self.level, UiLogLevel):
            raise TypeError("UI operational log level is invalid")
        for identifier, maximum in (
            (self.source, 32),
            (self.event_name, 96),
        ):
            if not identifier or len(identifier) > maximum or not identifier.isprintable():
                raise ValueError("UI operational log identifier is invalid")
        for text_value, maximum in (
            (self.reason_code, 128),
            (self.detail, _MAX_UI_LOG_DETAIL),
        ):
            if text_value is not None and (
                not text_value or len(text_value) > maximum or not text_value.isprintable()
            ):
                raise ValueError("UI operational log text is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "detail": self.detail,
            "event_name": self.event_name,
            "level": self.level.value,
            "occurred_at_utc": self.occurred_at_utc.isoformat(),
            "reason_code": self.reason_code,
            "source": self.source,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiOperationalLogEntry:
        _exact(
            payload,
            {
                "detail",
                "event_name",
                "level",
                "occurred_at_utc",
                "reason_code",
                "source",
            },
        )
        try:
            return cls(
                occurred_at_utc=datetime.fromisoformat(_string(payload, "occurred_at_utc", 64)),
                level=UiLogLevel(_string(payload, "level", 16)),
                source=_string(payload, "source", 32),
                event_name=_string(payload, "event_name", 96),
                reason_code=_optional_string(payload, "reason_code", 128),
                detail=_optional_string(payload, "detail", _MAX_UI_LOG_DETAIL),
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiDigitRiskConfig:
    stake_minor_units: int
    daily_stop_loss_minor_units: int
    daily_take_profit_minor_units: int
    max_consecutive_losses: int
    cooldown_seconds_after_loss: float
    min_quantum_confidence_pct: Decimal
    selected_symbol: str
    currency: str = "USD"
    auto_select_symbol: bool = True
    active_strategy_id: str = "tail-probability-edge"
    enabled_strategy_ids: frozenset[str] = frozenset(
        {
            "tail-probability-edge",
            "selective-differs-edge",
            "parity-regime-edge",
            "payout-routed-differs-session",
        }
    )
    selection_mode: str = "single"
    # Deprecated wire compatibility field; selection_mode is authoritative.
    stress_test_all_strategies_enabled: bool = False
    martingale_enabled: bool = False
    martingale_multiplier: Decimal = Decimal("2.00")
    martingale_max_steps: int = 2
    martingale_max_stake_minor_units: int = 400

    def __post_init__(self) -> None:
        if type(self.stake_minor_units) is not int or self.stake_minor_units < 35:
            raise ValueError("digit stake is invalid")
        for value in (
            self.daily_stop_loss_minor_units,
            self.daily_take_profit_minor_units,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("digit monetary limit is invalid")
        if type(self.max_consecutive_losses) is not int or not (
            1 <= self.max_consecutive_losses <= 5
        ):
            raise ValueError("digit consecutive-loss limit is invalid")
        if (
            isinstance(self.cooldown_seconds_after_loss, bool)
            or not isinstance(self.cooldown_seconds_after_loss, int | float)
            or not isfinite(self.cooldown_seconds_after_loss)
            or self.cooldown_seconds_after_loss <= 0
        ):
            raise ValueError("digit cooldown is invalid")
        if (
            not isinstance(self.min_quantum_confidence_pct, Decimal)
            or not self.min_quantum_confidence_pct.is_finite()
            or not Decimal("80.0") <= self.min_quantum_confidence_pct <= Decimal("99.0")
        ):
            raise ValueError("digit confidence threshold is invalid")
        if not self.selected_symbol or len(self.selected_symbol) > 32:
            raise ValueError("digit symbol is invalid")
        if self.currency != "USD":
            raise ValueError("digit currency is unsupported")
        if type(self.auto_select_symbol) is not bool:
            raise ValueError("digit automatic symbol selection is invalid")
        if self.active_strategy_id and self.active_strategy_id not in {
            "tail-probability-edge",
            "selective-differs-edge",
            "parity-regime-edge",
            "payout-routed-differs-session",
        }:
            raise ValueError("digit active strategy is invalid")
        allowed_strategies = {
            "tail-probability-edge",
            "selective-differs-edge",
            "parity-regime-edge",
            "payout-routed-differs-session",
        }
        if isinstance(self.enabled_strategy_ids, list | tuple | set):
            object.__setattr__(self, "enabled_strategy_ids", frozenset(self.enabled_strategy_ids))
        if not isinstance(
            self.enabled_strategy_ids, frozenset
        ) or not self.enabled_strategy_ids.issubset(allowed_strategies):
            raise ValueError("digit enabled strategies are invalid")
        if type(self.stress_test_all_strategies_enabled) is not bool:
            raise ValueError("digit stress mode is invalid")
        if self.selection_mode not in {"single", "multi", "stress"}:
            raise ValueError("digit strategy selection mode is invalid")
        if type(self.martingale_enabled) is not bool:
            raise ValueError("digit martingale opt-in is invalid")
        if (
            not isinstance(self.martingale_multiplier, Decimal)
            or not self.martingale_multiplier.is_finite()
            or not Decimal("1.10") <= self.martingale_multiplier <= Decimal("3.00")
        ):
            raise ValueError("digit martingale multiplier is invalid")
        if type(self.martingale_max_steps) is not int or not (1 <= self.martingale_max_steps <= 4):
            raise ValueError("digit martingale max steps is invalid")
        if (
            type(self.martingale_max_stake_minor_units) is not int
            or self.martingale_max_stake_minor_units <= 0
            or self.martingale_max_stake_minor_units > self.daily_stop_loss_minor_units
        ):
            raise ValueError("digit martingale max stake is invalid")
        if self.martingale_enabled:
            if self.martingale_max_stake_minor_units < self.stake_minor_units:
                raise ValueError("digit martingale max stake is invalid")
            if self.max_consecutive_losses < self.martingale_max_steps + 1:
                raise ValueError("digit martingale loss limit is too low")
            # The Core calculates recovery from the broker proposal's real net
            # payout and enforces the remaining stop-loss budget per order.

    def to_payload(self) -> dict[str, object]:
        return {
            "cooldown_seconds_after_loss": self.cooldown_seconds_after_loss,
            "currency": self.currency,
            "daily_stop_loss_minor_units": self.daily_stop_loss_minor_units,
            "daily_take_profit_minor_units": self.daily_take_profit_minor_units,
            "max_consecutive_losses": self.max_consecutive_losses,
            "min_quantum_confidence_pct": str(self.min_quantum_confidence_pct),
            "selected_symbol": self.selected_symbol,
            "auto_select_symbol": self.auto_select_symbol,
            "active_strategy_id": self.active_strategy_id,
            "enabled_strategy_ids": sorted(self.enabled_strategy_ids),
            "selection_mode": self.selection_mode,
            "stress_test_all_strategies_enabled": self.stress_test_all_strategies_enabled,
            "stake_minor_units": self.stake_minor_units,
            "martingale_enabled": self.martingale_enabled,
            "martingale_multiplier": str(self.martingale_multiplier),
            "martingale_max_steps": self.martingale_max_steps,
            "martingale_max_stake_minor_units": self.martingale_max_stake_minor_units,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiDigitRiskConfig:
        base_keys = {
            "cooldown_seconds_after_loss",
            "currency",
            "daily_stop_loss_minor_units",
            "daily_take_profit_minor_units",
            "max_consecutive_losses",
            "min_quantum_confidence_pct",
            "selected_symbol",
            "stake_minor_units",
        }
        martingale_keys = {
            "martingale_enabled",
            "martingale_multiplier",
            "martingale_max_steps",
            "martingale_max_stake_minor_units",
        }
        automatic_selection_keys = {"auto_select_symbol"}
        active_strategy_keys = {"active_strategy_id"}
        multi_strategy_keys = {
            "enabled_strategy_ids",
            "selection_mode",
            "stress_test_all_strategies_enabled",
        }
        optional_keys = (
            martingale_keys | automatic_selection_keys | active_strategy_keys | multi_strategy_keys
        )
        if not base_keys.issubset(payload) or not set(payload).issubset(base_keys | optional_keys):
            raise _invalid()
        stake = payload.get("stake_minor_units")
        stop = payload.get("daily_stop_loss_minor_units")
        take = payload.get("daily_take_profit_minor_units")
        losses = payload.get("max_consecutive_losses")
        cooldown = payload.get("cooldown_seconds_after_loss")
        confidence = payload.get("min_quantum_confidence_pct")
        martingale_enabled = payload.get("martingale_enabled", False)
        martingale_multiplier = payload.get("martingale_multiplier", "2.00")
        martingale_steps = payload.get("martingale_max_steps", 2)
        martingale_max_stake = payload.get("martingale_max_stake_minor_units", stop)
        auto_select_symbol = payload.get("auto_select_symbol", True)
        active_strategy_id = payload.get("active_strategy_id", "tail-probability-edge")
        enabled_strategy_ids = payload.get("enabled_strategy_ids", [active_strategy_id])
        stress_mode = payload.get("stress_test_all_strategies_enabled", False)
        selection_mode = payload.get("selection_mode", "single")
        if (
            type(stake) is not int
            or type(stop) is not int
            or type(take) is not int
            or type(losses) is not int
            or isinstance(cooldown, bool)
            or not isinstance(cooldown, int | float)
            or not isinstance(confidence, str)
            or type(martingale_enabled) is not bool
            or not isinstance(martingale_multiplier, str)
            or type(martingale_steps) is not int
            or type(martingale_max_stake) is not int
            or type(auto_select_symbol) is not bool
            or not isinstance(active_strategy_id, str)
            or not isinstance(enabled_strategy_ids, list)
            or not all(isinstance(item, str) for item in enabled_strategy_ids)
            or type(stress_mode) is not bool
            or not isinstance(selection_mode, str)
        ):
            raise _invalid()
        try:
            return cls(
                stake_minor_units=stake,
                daily_stop_loss_minor_units=stop,
                daily_take_profit_minor_units=take,
                max_consecutive_losses=losses,
                cooldown_seconds_after_loss=float(cooldown),
                min_quantum_confidence_pct=Decimal(confidence),
                selected_symbol=_string(payload, "selected_symbol", 32),
                currency=_string(payload, "currency", 3),
                auto_select_symbol=auto_select_symbol,
                active_strategy_id=active_strategy_id,
                enabled_strategy_ids=frozenset(enabled_strategy_ids),
                selection_mode=selection_mode,
                stress_test_all_strategies_enabled=stress_mode,
                martingale_enabled=martingale_enabled,
                martingale_multiplier=Decimal(martingale_multiplier),
                martingale_max_steps=martingale_steps,
                martingale_max_stake_minor_units=martingale_max_stake,
            )
        except (InvalidOperation, ValueError) as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiUpdateDigitRiskConfigCommand:
    config: UiDigitRiskConfig

    def to_payload(self) -> dict[str, object]:
        return {"config": self.config.to_payload()}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiUpdateDigitRiskConfigCommand:
        _exact(payload, {"config"})
        return cls(UiDigitRiskConfig.from_payload(_mapping(payload.get("config"))))


@dataclass(frozen=True, slots=True)
class UiUpdateDigitRiskConfigAck:
    status: UiDigitRiskConfigStatus
    reason_code: str | None

    def __post_init__(self) -> None:
        if self.status is UiDigitRiskConfigStatus.OK and self.reason_code is not None:
            raise ValueError("accepted digit config cannot have a rejection reason")
        if self.status is UiDigitRiskConfigStatus.REJECTED and not self.reason_code:
            raise ValueError("rejected digit config requires a reason")

    def to_payload(self) -> dict[str, object]:
        return {"reason_code": self.reason_code, "status": self.status.value}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiUpdateDigitRiskConfigAck:
        _exact(payload, {"reason_code", "status"})
        try:
            return cls(
                UiDigitRiskConfigStatus(_string(payload, "status", 16)),
                _optional_string(payload, "reason_code", 64),
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiDerivStrategyStatus:
    strategy_id: str
    display_name: str
    markets: str
    lifecycle_status: str
    signal_state: str
    reason_code: str
    warmup_current: int
    warmup_required: int
    last_signal_epoch: int | None = None
    last_signal_symbol: str | None = None
    last_contract_type: str | None = None
    last_direction: str | None = None
    last_barrier: int | None = None
    estimated_probability_pct: str | None = None
    required_probability_pct: str | None = None
    analysis_latency_microseconds: int = 0
    signals_emitted_total: int = 0
    signals_executed_total: int = 0
    signals_lost_to_arbitration_total: int = 0
    analysis_latency_microseconds_p95: int = 0
    conditional_sample: int = 0

    def __post_init__(self) -> None:
        for value in (
            self.strategy_id,
            self.display_name,
            self.markets,
            self.lifecycle_status,
            self.signal_state,
            self.reason_code,
        ):
            if not value.strip() or len(value) > _MAX_TEXT:
                raise ValueError("Deriv strategy status text is invalid")
        if (
            type(self.warmup_current) is not int
            or type(self.warmup_required) is not int
            or not 0 <= self.warmup_current <= self.warmup_required
        ):
            raise ValueError("Deriv strategy warmup is invalid")
        if self.last_signal_epoch is not None and (
            type(self.last_signal_epoch) is not int or self.last_signal_epoch <= 0
        ):
            raise ValueError("Deriv strategy signal epoch is invalid")
        if self.last_barrier is not None and (
            type(self.last_barrier) is not int or not 0 <= self.last_barrier <= 9
        ):
            raise ValueError("Deriv strategy barrier is invalid")
        for probability_text in (
            self.estimated_probability_pct,
            self.required_probability_pct,
        ):
            if probability_text is not None:
                probability = Decimal(probability_text)
                if not probability.is_finite() or not 0 <= probability <= 100:
                    raise ValueError("Deriv strategy probability is invalid")
        if (
            type(self.analysis_latency_microseconds) is not int
            or self.analysis_latency_microseconds < 0
        ):
            raise ValueError("Deriv strategy analysis latency is invalid")
        for metric_value in (
            self.signals_emitted_total,
            self.signals_executed_total,
            self.signals_lost_to_arbitration_total,
            self.analysis_latency_microseconds_p95,
            self.conditional_sample,
        ):
            if type(metric_value) is not int or metric_value < 0:
                raise ValueError("Deriv strategy telemetry is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "display_name": self.display_name,
            "last_contract_type": self.last_contract_type,
            "last_direction": self.last_direction,
            "last_barrier": self.last_barrier,
            "last_signal_epoch": self.last_signal_epoch,
            "last_signal_symbol": self.last_signal_symbol,
            "estimated_probability_pct": self.estimated_probability_pct,
            "required_probability_pct": self.required_probability_pct,
            "analysis_latency_microseconds": self.analysis_latency_microseconds,
            "lifecycle_status": self.lifecycle_status,
            "markets": self.markets,
            "reason_code": self.reason_code,
            "signal_state": self.signal_state,
            "strategy_id": self.strategy_id,
            "warmup_current": self.warmup_current,
            "warmup_required": self.warmup_required,
            "signals_emitted_total": self.signals_emitted_total,
            "signals_executed_total": self.signals_executed_total,
            "signals_lost_to_arbitration_total": self.signals_lost_to_arbitration_total,
            "analysis_latency_microseconds_p95": self.analysis_latency_microseconds_p95,
            "conditional_sample": self.conditional_sample,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiDerivStrategyStatus:
        required_keys = {
            "display_name",
            "last_contract_type",
            "last_direction",
            "last_barrier",
            "last_signal_epoch",
            "last_signal_symbol",
            "estimated_probability_pct",
            "required_probability_pct",
            "analysis_latency_microseconds",
            "lifecycle_status",
            "markets",
            "reason_code",
            "signal_state",
            "strategy_id",
            "warmup_current",
            "warmup_required",
        }
        telemetry_keys = {
            "signals_emitted_total",
            "signals_executed_total",
            "signals_lost_to_arbitration_total",
            "analysis_latency_microseconds_p95",
            "conditional_sample",
        }
        actual = frozenset(payload)
        if actual not in {frozenset(required_keys), frozenset(required_keys | telemetry_keys)}:
            raise _invalid()
        current = payload.get("warmup_current")
        required = payload.get("warmup_required")
        signal_epoch = payload.get("last_signal_epoch")
        barrier = payload.get("last_barrier")
        latency = payload.get("analysis_latency_microseconds")
        emitted = payload.get("signals_emitted_total", 0)
        executed = payload.get("signals_executed_total", 0)
        lost = payload.get("signals_lost_to_arbitration_total", 0)
        latency_p95 = payload.get("analysis_latency_microseconds_p95", 0)
        conditional_sample = payload.get("conditional_sample", 0)
        if (
            type(current) is not int
            or type(required) is not int
            or (signal_epoch is not None and type(signal_epoch) is not int)
            or (barrier is not None and type(barrier) is not int)
            or type(latency) is not int
            or type(emitted) is not int
            or type(executed) is not int
            or type(lost) is not int
            or type(latency_p95) is not int
            or type(conditional_sample) is not int
        ):
            raise _invalid()
        try:
            return cls(
                strategy_id=_string(payload, "strategy_id"),
                display_name=_string(payload, "display_name"),
                markets=_string(payload, "markets"),
                lifecycle_status=_string(payload, "lifecycle_status"),
                signal_state=_string(payload, "signal_state"),
                reason_code=_string(payload, "reason_code"),
                warmup_current=current,
                warmup_required=required,
                last_signal_epoch=signal_epoch,
                last_signal_symbol=_optional_string(payload, "last_signal_symbol"),
                last_contract_type=_optional_string(payload, "last_contract_type"),
                last_direction=_optional_string(payload, "last_direction"),
                last_barrier=barrier,
                estimated_probability_pct=_optional_string(payload, "estimated_probability_pct"),
                required_probability_pct=_optional_string(payload, "required_probability_pct"),
                analysis_latency_microseconds=latency,
                signals_emitted_total=emitted,
                signals_executed_total=executed,
                signals_lost_to_arbitration_total=lost,
                analysis_latency_microseconds_p95=latency_p95,
                conditional_sample=conditional_sample,
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiDerivAssetRank:
    symbol: str
    state: str
    reason_code: str
    warmup_current: int
    warmup_required: int
    selected: bool = False
    strategy_id: str | None = None
    contract_type: str | None = None
    barrier: int | None = None
    estimated_probability_pct: str | None = None
    required_probability_pct: str | None = None
    conservative_margin_pct: str | None = None
    analysis_latency_microseconds: int = 0

    def __post_init__(self) -> None:
        for value in (self.symbol, self.state, self.reason_code):
            if not value.strip() or len(value) > _MAX_TEXT:
                raise ValueError("Deriv asset rank text is invalid")
        if (
            type(self.warmup_current) is not int
            or type(self.warmup_required) is not int
            or not 0 <= self.warmup_current <= self.warmup_required
        ):
            raise ValueError("Deriv asset rank warmup is invalid")
        if type(self.selected) is not bool:
            raise ValueError("Deriv asset rank selection is invalid")
        if self.barrier is not None and (
            type(self.barrier) is not int or not 0 <= self.barrier <= 9
        ):
            raise ValueError("Deriv asset rank barrier is invalid")
        if (
            type(self.analysis_latency_microseconds) is not int
            or self.analysis_latency_microseconds < 0
        ):
            raise ValueError("Deriv asset rank latency is invalid")
        for decimal_text in (
            self.estimated_probability_pct,
            self.required_probability_pct,
            self.conservative_margin_pct,
        ):
            if decimal_text is not None:
                try:
                    decimal_value = Decimal(decimal_text)
                except InvalidOperation as exc:
                    raise ValueError("Deriv asset rank decimal is invalid") from exc
                if not decimal_value.is_finite():
                    raise ValueError("Deriv asset rank decimal is invalid")
        candidate_fields = (
            self.strategy_id,
            self.contract_type,
            self.estimated_probability_pct,
            self.required_probability_pct,
            self.conservative_margin_pct,
        )
        if self.state == "CANDIDATE":
            if any(item is None for item in candidate_fields):
                raise ValueError("Deriv asset candidate evidence is incomplete")
        elif self.selected or any(item is not None for item in candidate_fields):
            raise ValueError("non-candidate Deriv asset cannot carry candidate evidence")

    def to_payload(self) -> dict[str, object]:
        return {
            "analysis_latency_microseconds": self.analysis_latency_microseconds,
            "barrier": self.barrier,
            "conservative_margin_pct": self.conservative_margin_pct,
            "contract_type": self.contract_type,
            "estimated_probability_pct": self.estimated_probability_pct,
            "reason_code": self.reason_code,
            "required_probability_pct": self.required_probability_pct,
            "selected": self.selected,
            "state": self.state,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "warmup_current": self.warmup_current,
            "warmup_required": self.warmup_required,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiDerivAssetRank:
        _exact(
            payload,
            {
                "analysis_latency_microseconds",
                "barrier",
                "conservative_margin_pct",
                "contract_type",
                "estimated_probability_pct",
                "reason_code",
                "required_probability_pct",
                "selected",
                "state",
                "strategy_id",
                "symbol",
                "warmup_current",
                "warmup_required",
            },
        )
        current = payload.get("warmup_current")
        required = payload.get("warmup_required")
        selected = payload.get("selected")
        barrier = payload.get("barrier")
        latency = payload.get("analysis_latency_microseconds")
        if (
            type(current) is not int
            or type(required) is not int
            or not isinstance(selected, bool)
            or (barrier is not None and type(barrier) is not int)
            or type(latency) is not int
        ):
            raise _invalid()
        try:
            return cls(
                symbol=_string(payload, "symbol", 32),
                state=_string(payload, "state", 32),
                reason_code=_string(payload, "reason_code", 64),
                warmup_current=current,
                warmup_required=required,
                selected=selected,
                strategy_id=_optional_string(payload, "strategy_id"),
                contract_type=_optional_string(payload, "contract_type"),
                barrier=barrier,
                estimated_probability_pct=_optional_string(payload, "estimated_probability_pct"),
                required_probability_pct=_optional_string(payload, "required_probability_pct"),
                conservative_margin_pct=_optional_string(payload, "conservative_margin_pct"),
                analysis_latency_microseconds=latency,
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiIqOptionAssetRank:
    symbol: str
    display_name: str
    rsi: str
    direction: str | None = None
    condition: str = "NEUTRAL"
    selected: bool = False
    status: str = "MONITORING"
    candidate_details: str = ""
    # Evidence fields are projected by the Core.  They are deliberately
    # additive so older UI peers can continue to consume the legacy radar.
    source: str = "UNKNOWN"
    mode: str = "LEGACY"
    revision: str | None = None
    readiness: str = "UNKNOWN"
    signal_observed: bool = False
    candidate_eligible: bool = False
    order_submitted: bool = False
    order_accepted: bool = False
    terminal: bool = False
    wait_reason: str | None = None
    waiting_seconds: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_details, str) or len(self.candidate_details) > 2048:
            raise ValueError("IQ candidate details invalid")
        for value in (self.symbol, self.display_name, self.rsi, self.condition, self.status):
            if not value.strip() or len(value) > _MAX_TEXT:
                raise ValueError("IQ Option asset rank text is invalid")
        if type(self.selected) is not bool:
            raise ValueError("IQ Option asset rank selection is invalid")
        if self.direction is not None and self.direction not in {"CALL", "PUT"}:
            raise ValueError("IQ Option asset rank direction is invalid")
        for value in (self.source, self.mode, self.readiness):
            if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
                raise ValueError("IQ Option evidence text is invalid")
        if self.revision is not None and (not self.revision.strip() or len(self.revision) > 128):
            raise ValueError("IQ Option evidence revision is invalid")
        if self.wait_reason is not None and (
            not self.wait_reason.strip() or len(self.wait_reason) > _MAX_TEXT
        ):
            raise ValueError("IQ Option wait reason is invalid")
        for flag in (
            self.signal_observed,
            self.candidate_eligible,
            self.order_submitted,
            self.order_accepted,
            self.terminal,
        ):
            if type(flag) is not bool:
                raise ValueError("IQ Option evidence flag is invalid")
        if type(self.waiting_seconds) is not int or self.waiting_seconds < 0:
            raise ValueError("IQ Option waiting duration is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "display_name": self.display_name,
            "rsi": self.rsi,
            "direction": self.direction,
            "condition": self.condition,
            "selected": self.selected,
            "status": self.status,
            "candidate_details": self.candidate_details,
            "source": self.source,
            "mode": self.mode,
            "revision": self.revision,
            "readiness": self.readiness,
            "signal_observed": self.signal_observed,
            "candidate_eligible": self.candidate_eligible,
            "order_submitted": self.order_submitted,
            "order_accepted": self.order_accepted,
            "terminal": self.terminal,
            "wait_reason": self.wait_reason,
            "waiting_seconds": self.waiting_seconds,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiIqOptionAssetRank:
        payload = dict(payload)
        details = payload.pop("candidate_details", "")
        if not isinstance(details, str) or len(details) > 2048:
            raise _invalid()
        allowed = {
            "symbol",
            "display_name",
            "rsi",
            "direction",
            "condition",
            "selected",
            "status",
            "source",
            "mode",
            "revision",
            "readiness",
            "signal_observed",
            "candidate_eligible",
            "order_submitted",
            "order_accepted",
            "terminal",
            "wait_reason",
            "waiting_seconds",
        }
        if not set(payload).issubset(allowed):
            raise _invalid()
        selected = payload.get("selected")
        if type(selected) is not bool:
            raise _invalid()
        direction = payload.get("direction")
        if direction is not None and (
            type(direction) is not str or direction not in {"CALL", "PUT"}
        ):
            raise _invalid()
        evidence_flags_raw = tuple(
            payload.get(name, False)
            for name in (
                "signal_observed",
                "candidate_eligible",
                "order_submitted",
                "order_accepted",
                "terminal",
            )
        )
        if any(type(value) is not bool for value in evidence_flags_raw):
            raise _invalid()
        evidence_flags = cast(tuple[bool, ...], evidence_flags_raw)
        waiting_seconds = payload.get("waiting_seconds", 0)
        if type(waiting_seconds) is not int:
            raise _invalid()
        try:
            return cls(
                symbol=_string(payload, "symbol", 32),
                display_name=_string(payload, "display_name", 64),
                rsi=_string(payload, "rsi", 32),
                direction=direction,
                condition=_string(payload, "condition", 32),
                selected=selected,
                status=_string(payload, "status", 32),
                candidate_details=details,
                source=_string(payload, "source", _MAX_TEXT) if "source" in payload else "UNKNOWN",
                mode=_string(payload, "mode", _MAX_TEXT) if "mode" in payload else "LEGACY",
                revision=_optional_string(payload, "revision", 128),
                readiness=(
                    _string(payload, "readiness", _MAX_TEXT)
                    if "readiness" in payload
                    else "UNKNOWN"
                ),
                signal_observed=evidence_flags[0],
                candidate_eligible=evidence_flags[1],
                order_submitted=evidence_flags[2],
                order_accepted=evidence_flags[3],
                terminal=evidence_flags[4],
                wait_reason=_optional_string(payload, "wait_reason", _MAX_TEXT),
                waiting_seconds=waiting_seconds,
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiIqOptionExecutionMetrics:
    """Bounded, local-only evidence for the IQ Option execution projection."""

    source: str = "UNKNOWN"
    mode: str = "LEGACY"
    manifest_revision: str | None = None
    manifest_version: int | None = None
    strategy_count: int = 0
    series_count: int = 0
    unique_indicator_nodes: int = 0
    cache_reuse_hits: int = 0
    fetches: int = 0
    indicator_updates: int = 0
    decisions: int = 0
    decision_p50_ms: int = 0
    decision_p95_ms: int = 0
    decision_p99_ms: int = 0
    queue_depth: int = 0
    close_delay_ms: int = 0
    cache_entries: int = 0
    memory_rss_mb: int | None = None
    fencing_discards: int = 0
    waiting_reason: str | None = None
    waiting_seconds: int = 0
    catalog_status: str = "UNAVAILABLE"
    evidence_n: int | None = None
    evidence_oos: int | None = None
    evidence_validity: str = "UNAVAILABLE"
    uploader_opt_in: bool = False
    uploader_pending: int = 0
    uploader_expired: int = 0

    def __post_init__(self) -> None:
        for value in (self.source, self.mode, self.catalog_status, self.evidence_validity):
            if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
                raise ValueError("IQ metrics text is invalid")
        if self.manifest_revision is not None and len(self.manifest_revision) > 128:
            raise ValueError("IQ manifest revision is invalid")
        for name in (
            "strategy_count",
            "series_count",
            "unique_indicator_nodes",
            "cache_reuse_hits",
            "fetches",
            "indicator_updates",
            "decisions",
            "decision_p50_ms",
            "decision_p95_ms",
            "decision_p99_ms",
            "queue_depth",
            "close_delay_ms",
            "cache_entries",
            "fencing_discards",
            "waiting_seconds",
            "uploader_pending",
            "uploader_expired",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError("IQ metrics integer is invalid")
        for name in ("manifest_version", "memory_rss_mb", "evidence_n", "evidence_oos"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("IQ metrics optional integer is invalid")
        if self.waiting_reason is not None and len(self.waiting_reason) > _MAX_TEXT:
            raise ValueError("IQ waiting reason is invalid")
        if type(self.uploader_opt_in) is not bool:
            raise ValueError("IQ uploader opt-in must be boolean")

    def to_payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "mode": self.mode,
            "manifest_revision": self.manifest_revision,
            "manifest_version": self.manifest_version,
            "strategy_count": self.strategy_count,
            "series_count": self.series_count,
            "unique_indicator_nodes": self.unique_indicator_nodes,
            "cache_reuse_hits": self.cache_reuse_hits,
            "fetches": self.fetches,
            "indicator_updates": self.indicator_updates,
            "decisions": self.decisions,
            "decision_p50_ms": self.decision_p50_ms,
            "decision_p95_ms": self.decision_p95_ms,
            "decision_p99_ms": self.decision_p99_ms,
            "queue_depth": self.queue_depth,
            "close_delay_ms": self.close_delay_ms,
            "cache_entries": self.cache_entries,
            "memory_rss_mb": self.memory_rss_mb,
            "fencing_discards": self.fencing_discards,
            "waiting_reason": self.waiting_reason,
            "waiting_seconds": self.waiting_seconds,
            "catalog_status": self.catalog_status,
            "evidence_n": self.evidence_n,
            "evidence_oos": self.evidence_oos,
            "evidence_validity": self.evidence_validity,
            "uploader_opt_in": self.uploader_opt_in,
            "uploader_pending": self.uploader_pending,
            "uploader_expired": self.uploader_expired,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiIqOptionExecutionMetrics:
        expected = {
            "source",
            "mode",
            "manifest_revision",
            "manifest_version",
            "strategy_count",
            "series_count",
            "unique_indicator_nodes",
            "cache_reuse_hits",
            "fetches",
            "indicator_updates",
            "decisions",
            "decision_p50_ms",
            "decision_p95_ms",
            "decision_p99_ms",
            "queue_depth",
            "close_delay_ms",
            "cache_entries",
            "memory_rss_mb",
            "fencing_discards",
            "waiting_reason",
            "waiting_seconds",
            "catalog_status",
            "evidence_n",
            "evidence_oos",
            "evidence_validity",
            "uploader_opt_in",
            "uploader_pending",
            "uploader_expired",
        }
        if set(payload) != expected:
            raise _invalid()
        ints_raw = tuple(
            payload.get(name)
            for name in (
                "strategy_count",
                "series_count",
                "unique_indicator_nodes",
                "cache_reuse_hits",
                "fetches",
                "indicator_updates",
                "decisions",
                "decision_p50_ms",
                "decision_p95_ms",
                "decision_p99_ms",
                "queue_depth",
                "close_delay_ms",
                "cache_entries",
                "fencing_discards",
                "waiting_seconds",
                "uploader_pending",
                "uploader_expired",
            )
        )
        optional_ints_raw = tuple(
            payload.get(name)
            for name in ("manifest_version", "memory_rss_mb", "evidence_n", "evidence_oos")
        )
        if any(type(value) is not int or value < 0 for value in ints_raw):
            raise _invalid()
        ints = cast(tuple[int, ...], ints_raw)
        optional_ints = cast(tuple[int | None, ...], optional_ints_raw)
        if any(
            value is not None and (type(value) is not int or value < 0) for value in optional_ints
        ):
            raise _invalid()
        if type(payload.get("uploader_opt_in")) is not bool:
            raise _invalid()
        try:
            return cls(
                source=_string(payload, "source"),
                mode=_string(payload, "mode"),
                manifest_revision=_optional_string(payload, "manifest_revision", 128),
                manifest_version=optional_ints[0],
                strategy_count=ints[0],
                series_count=ints[1],
                unique_indicator_nodes=ints[2],
                cache_reuse_hits=ints[3],
                fetches=ints[4],
                indicator_updates=ints[5],
                decisions=ints[6],
                decision_p50_ms=ints[7],
                decision_p95_ms=ints[8],
                decision_p99_ms=ints[9],
                queue_depth=ints[10],
                close_delay_ms=ints[11],
                cache_entries=ints[12],
                memory_rss_mb=optional_ints[1],
                fencing_discards=ints[13],
                waiting_reason=_optional_string(payload, "waiting_reason"),
                waiting_seconds=ints[14],
                catalog_status=_string(payload, "catalog_status"),
                evidence_n=optional_ints[2],
                evidence_oos=optional_ints[3],
                evidence_validity=_string(payload, "evidence_validity"),
                uploader_opt_in=bool(payload["uploader_opt_in"]),
                uploader_pending=ints[15],
                uploader_expired=ints[16],
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiBotWaitingStatus:
    reason_code: str
    description: str
    waiting_since_seconds: int
    symbol: str | None = None
    armed_epoch: int | None = None
    rearm_notice: bool = False

    def __post_init__(self) -> None:
        if not self.reason_code or len(self.reason_code) > 64:
            raise ValueError("bot waiting reason is invalid")
        if not self.description or len(self.description) > 320:
            raise ValueError("bot waiting description is invalid")
        if type(self.waiting_since_seconds) is not int or self.waiting_since_seconds < 0:
            raise ValueError("bot waiting duration is invalid")
        if self.symbol is not None and (not self.symbol or len(self.symbol) > 32):
            raise ValueError("bot waiting symbol is invalid")
        if self.armed_epoch is not None and (
            type(self.armed_epoch) is not int or self.armed_epoch <= 0
        ):
            raise ValueError("bot armed epoch is invalid")
        if not isinstance(self.rearm_notice, bool):
            raise TypeError("bot rearm notice must be boolean")

    def to_payload(self) -> dict[str, object]:
        return {
            "armed_epoch": self.armed_epoch,
            "description": self.description,
            "rearm_notice": self.rearm_notice,
            "reason_code": self.reason_code,
            "symbol": self.symbol,
            "waiting_since_seconds": self.waiting_since_seconds,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiBotWaitingStatus:
        _exact(
            payload,
            {
                "armed_epoch",
                "description",
                "rearm_notice",
                "reason_code",
                "symbol",
                "waiting_since_seconds",
            },
        )
        waiting = payload.get("waiting_since_seconds")
        epoch = payload.get("armed_epoch")
        rearm = payload.get("rearm_notice")
        if (
            type(waiting) is not int
            or (epoch is not None and type(epoch) is not int)
            or not isinstance(rearm, bool)
        ):
            raise _invalid()
        return cls(
            reason_code=_string(payload, "reason_code", 64),
            description=_string(payload, "description", 320),
            waiting_since_seconds=waiting,
            symbol=_optional_string(payload, "symbol", 32),
            armed_epoch=epoch,
            rearm_notice=rearm,
        )


@dataclass(frozen=True, slots=True)
class UiMultiStrategyMetrics:
    evaluations_per_second: float
    active_engines: int
    enabled_strategies: int
    arbitration_candidates_p95: int
    evaluation_cycle_duration_microseconds_p95: int

    def __post_init__(self) -> None:
        if not isfinite(self.evaluations_per_second) or self.evaluations_per_second < 0:
            raise ValueError("multi-strategy evaluation rate is invalid")
        for value in (
            self.active_engines,
            self.enabled_strategies,
            self.arbitration_candidates_p95,
            self.evaluation_cycle_duration_microseconds_p95,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("multi-strategy metric is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "active_engines": self.active_engines,
            "arbitration_candidates_p95": self.arbitration_candidates_p95,
            "enabled_strategies": self.enabled_strategies,
            "evaluation_cycle_duration_microseconds_p95": (
                self.evaluation_cycle_duration_microseconds_p95
            ),
            "evaluations_per_second": self.evaluations_per_second,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiMultiStrategyMetrics:
        _exact(
            payload,
            {
                "active_engines",
                "arbitration_candidates_p95",
                "enabled_strategies",
                "evaluation_cycle_duration_microseconds_p95",
                "evaluations_per_second",
            },
        )
        rate = payload.get("evaluations_per_second")
        values = tuple(
            payload.get(name)
            for name in (
                "active_engines",
                "enabled_strategies",
                "arbitration_candidates_p95",
                "evaluation_cycle_duration_microseconds_p95",
            )
        )
        if (
            isinstance(rate, bool)
            or not isinstance(rate, int | float)
            or any(type(value) is not int for value in values)
        ):
            raise _invalid()
        return cls(float(rate), *values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class UiProjectionSnapshot:
    global_state: UiGlobalState
    safe_stop_active: bool
    health_gates: tuple[HealthGateStatus, ...]
    broker_cards: tuple[BrokerCardStatus, ...]
    active_orders: tuple[OrderSummary, ...]
    daily_pnl_minor_units: int
    daily_pnl_currency: str | None
    global_exposure_minor_units: int = 0
    global_max_exposure_minor_units: int = 0
    consecutive_losses: int = 0
    risk_state: str = "NORMAL"
    digit_risk_config: UiDigitRiskConfig | None = None
    cooldown_remaining_seconds: int = 0
    digit_frequency: DigitFrequencySnapshot | None = None
    deriv_strategies: tuple[UiDerivStrategyStatus, ...] = ()
    deriv_asset_ranking: tuple[UiDerivAssetRank, ...] = ()
    digit_martingale_step: int = 0
    digit_next_stake_minor_units: int = 0
    digit_projected_sequence_loss_minor_units: int = 0
    deriv_bot_reason: str = "BOT_WAITING_FOR_LIVE_DERIV"
    deriv_bot_waiting_status: UiBotWaitingStatus | None = None
    multi_strategy_metrics: UiMultiStrategyMetrics | None = None
    iqoption_risk_config: UiIqOptionRiskConfig | None = None
    iqoption_bot_armed: bool = False
    iqoption_bot_reason: str = "IQOPTION_BOT_DISARMED"
    iqoption_asset_ranking: tuple[UiIqOptionAssetRank, ...] = ()
    deriv_bot_armed: bool = False
    iqoption_execution_metrics: UiIqOptionExecutionMetrics | None = None
    iqoption_entry_ready: bool = False
    iqoption_entry_blocker: str | None = None
    operational_logs: tuple[UiOperationalLogEntry, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= len(self.health_gates) <= _MAX_GATES:
            raise ValueError("health gate projection count is outside bounds")
        if not 1 <= len(self.broker_cards) <= _MAX_BROKERS:
            raise ValueError("broker card count is outside bounds")
        if len(self.active_orders) > _MAX_ORDERS:
            raise ValueError("order projection count is outside bounds")
        if type(self.daily_pnl_minor_units) is not int:
            raise TypeError("daily P&L must use integer minor units")
        if self.daily_pnl_currency is not None and (
            len(self.daily_pnl_currency) != 3
            or not self.daily_pnl_currency.isascii()
            or not self.daily_pnl_currency.isalpha()
        ):
            raise ValueError("daily P&L currency is invalid")
        if self.daily_pnl_currency is None and self.daily_pnl_minor_units != 0:
            raise ValueError("P&L without one proven currency must be zero")
        if (
            type(self.global_exposure_minor_units) is not int
            or self.global_exposure_minor_units < 0
        ):
            raise ValueError("global exposure must be non-negative integer")
        if (
            type(self.global_max_exposure_minor_units) is not int
            or self.global_max_exposure_minor_units < 0
        ):
            raise ValueError("global max exposure must be non-negative integer")
        if type(self.consecutive_losses) is not int or self.consecutive_losses < 0:
            raise ValueError("consecutive losses must be non-negative integer")
        if type(self.cooldown_remaining_seconds) is not int or self.cooldown_remaining_seconds < 0:
            raise ValueError("digit cooldown remaining must be a non-negative integer")
        if len(self.deriv_strategies) > _MAX_DERIV_STRATEGIES:
            raise ValueError("Deriv strategy projection count is outside bounds")
        if len(self.deriv_asset_ranking) > _MAX_DERIV_ASSET_RANKS:
            raise ValueError("Deriv asset ranking count is outside bounds")
        if len(self.iqoption_asset_ranking) > _MAX_IQOPTION_ASSET_RANKS:
            raise ValueError("IQ Option asset ranking count is outside bounds")
        if len(self.operational_logs) > _MAX_UI_LOG_ENTRIES:
            raise ValueError("UI operational log count is outside bounds")
        for value in (
            self.digit_martingale_step,
            self.digit_next_stake_minor_units,
            self.digit_projected_sequence_loss_minor_units,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("digit martingale projection is invalid")
        if not isinstance(self.deriv_bot_reason, str) or len(self.deriv_bot_reason) > 64:
            raise ValueError("Deriv bot reason is invalid")
        if type(self.iqoption_bot_armed) is not bool:
            raise ValueError("IQ Option bot state is invalid")
        if type(self.deriv_bot_armed) is not bool:
            raise ValueError("Deriv bot state is invalid")
        if type(self.iqoption_entry_ready) is not bool:
            raise ValueError("IQ Option entry readiness is invalid")
        if self.iqoption_entry_blocker is not None and (
            not isinstance(self.iqoption_entry_blocker, str)
            or not self.iqoption_entry_blocker
            or len(self.iqoption_entry_blocker) > 128
        ):
            raise ValueError("IQ Option entry blocker is invalid")
        if (
            not isinstance(self.iqoption_bot_reason, str)
            or not self.iqoption_bot_reason
            or len(self.iqoption_bot_reason) > 64
        ):
            raise ValueError("IQ Option bot reason is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "active_orders": [item.to_payload() for item in self.active_orders],
            "broker_cards": [item.to_payload() for item in self.broker_cards],
            "consecutive_losses": self.consecutive_losses,
            "daily_pnl_currency": self.daily_pnl_currency,
            "daily_pnl_minor_units": self.daily_pnl_minor_units,
            "global_exposure_minor_units": self.global_exposure_minor_units,
            "global_max_exposure_minor_units": self.global_max_exposure_minor_units,
            "global_state": self.global_state.value,
            "health_gates": [item.to_payload() for item in self.health_gates],
            "risk_state": self.risk_state,
            "safe_stop_active": self.safe_stop_active,
            "digit_risk_config": (
                None if self.digit_risk_config is None else self.digit_risk_config.to_payload()
            ),
            "cooldown_remaining_seconds": self.cooldown_remaining_seconds,
            "digit_frequency": (
                None if self.digit_frequency is None else self.digit_frequency.to_payload()
            ),
            "deriv_strategies": [item.to_payload() for item in self.deriv_strategies],
            "deriv_asset_ranking": [item.to_payload() for item in self.deriv_asset_ranking],
            "iqoption_asset_ranking": [item.to_payload() for item in self.iqoption_asset_ranking],
            "digit_martingale_step": self.digit_martingale_step,
            "digit_next_stake_minor_units": self.digit_next_stake_minor_units,
            "digit_projected_sequence_loss_minor_units": (
                self.digit_projected_sequence_loss_minor_units
            ),
            "deriv_bot_reason": self.deriv_bot_reason,
            "deriv_bot_waiting_status": (
                None
                if self.deriv_bot_waiting_status is None
                else self.deriv_bot_waiting_status.to_payload()
            ),
            "multi_strategy_metrics": (
                None
                if self.multi_strategy_metrics is None
                else self.multi_strategy_metrics.to_payload()
            ),
            "iqoption_risk_config": (
                None
                if self.iqoption_risk_config is None
                else self.iqoption_risk_config.to_payload()
            ),
            "iqoption_bot_armed": self.iqoption_bot_armed,
            "iqoption_bot_reason": self.iqoption_bot_reason,
            "deriv_bot_armed": self.deriv_bot_armed,
            "iqoption_execution_metrics": (
                None
                if self.iqoption_execution_metrics is None
                else self.iqoption_execution_metrics.to_payload()
            ),
            "iqoption_entry_ready": self.iqoption_entry_ready,
            "iqoption_entry_blocker": self.iqoption_entry_blocker,
            "operational_logs": [item.to_payload() for item in self.operational_logs],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiProjectionSnapshot:
        base_keys = {
            "active_orders",
            "broker_cards",
            "daily_pnl_currency",
            "daily_pnl_minor_units",
            "global_state",
            "health_gates",
            "safe_stop_active",
        }
        optional_risk_keys = {
            "consecutive_losses",
            "global_exposure_minor_units",
            "global_max_exposure_minor_units",
            "risk_state",
            "digit_risk_config",
            "cooldown_remaining_seconds",
            "digit_frequency",
            "deriv_strategies",
            "deriv_asset_ranking",
            "digit_martingale_step",
            "digit_next_stake_minor_units",
            "digit_projected_sequence_loss_minor_units",
            "deriv_bot_reason",
            "deriv_bot_waiting_status",
            "multi_strategy_metrics",
            "iqoption_risk_config",
            "iqoption_bot_armed",
            "iqoption_bot_reason",
            "iqoption_asset_ranking",
            "deriv_bot_armed",
            "iqoption_execution_metrics",
            "iqoption_entry_ready",
            "iqoption_entry_blocker",
            "operational_logs",
        }
        actual_keys = set(payload)
        if not (
            base_keys.issubset(actual_keys) and actual_keys.issubset(base_keys | optional_risk_keys)
        ):
            raise _invalid()
        safe_stop = payload.get("safe_stop_active")
        pnl = payload.get("daily_pnl_minor_units")
        gates = payload.get("health_gates")
        brokers = payload.get("broker_cards")
        orders = payload.get("active_orders")
        global_exp = payload.get("global_exposure_minor_units", 0)
        global_max = payload.get("global_max_exposure_minor_units", 0)
        consec_losses = payload.get("consecutive_losses", 0)
        risk_st = str(payload.get("risk_state", "NORMAL"))
        digit_config_payload = payload.get("digit_risk_config")
        cooldown_remaining = payload.get("cooldown_remaining_seconds", 0)
        digit_frequency_payload = payload.get("digit_frequency")
        deriv_strategies_payload = payload.get("deriv_strategies", [])
        deriv_asset_ranking_payload = payload.get("deriv_asset_ranking", [])
        iqoption_asset_ranking_payload = payload.get("iqoption_asset_ranking", [])
        martingale_step = payload.get("digit_martingale_step", 0)
        next_stake = payload.get("digit_next_stake_minor_units", 0)
        projected_sequence_loss = payload.get("digit_projected_sequence_loss_minor_units", 0)
        deriv_bot_reason = payload.get("deriv_bot_reason", "BOT_WAITING_FOR_LIVE_DERIV")
        waiting_status_payload = payload.get("deriv_bot_waiting_status")
        multi_strategy_metrics_payload = payload.get("multi_strategy_metrics")
        iqoption_config_payload = payload.get("iqoption_risk_config")
        iqoption_bot_armed = payload.get("iqoption_bot_armed", False)
        iqoption_bot_reason = payload.get("iqoption_bot_reason", "IQOPTION_BOT_DISARMED")
        # Compatibility with a pre-isolation Core: only legacy snapshots may
        # infer Deriv armament from the old global Safe Stop field.
        deriv_bot_armed = payload.get(
            "deriv_bot_armed",
            not safe_stop if isinstance(safe_stop, bool) else False,
        )
        iqoption_execution_metrics_payload = payload.get("iqoption_execution_metrics")
        iqoption_entry_ready = payload.get("iqoption_entry_ready", False)
        iqoption_entry_blocker = payload.get("iqoption_entry_blocker")
        operational_logs_payload = payload.get("operational_logs", [])
        if (
            not isinstance(safe_stop, bool)
            or type(pnl) is not int
            or type(global_exp) is not int
            or type(global_max) is not int
            or type(consec_losses) is not int
            or type(cooldown_remaining) is not int
            or type(martingale_step) is not int
            or type(next_stake) is not int
            or type(projected_sequence_loss) is not int
            or not isinstance(deriv_bot_reason, str)
            or len(deriv_bot_reason) > 64
            or type(iqoption_bot_armed) is not bool
            or type(deriv_bot_armed) is not bool
            or type(iqoption_entry_ready) is not bool
            or (
                iqoption_entry_blocker is not None
                and (
                    not isinstance(iqoption_entry_blocker, str)
                    or not iqoption_entry_blocker
                    or len(iqoption_entry_blocker) > 128
                )
            )
            or not isinstance(iqoption_bot_reason, str)
            or not iqoption_bot_reason
            or len(iqoption_bot_reason) > 64
            or not _bounded_sequence(gates, 1, _MAX_GATES)
            or not _bounded_sequence(brokers, 1, _MAX_BROKERS)
            or not _bounded_sequence(orders, 0, _MAX_ORDERS)
            or not _bounded_sequence(deriv_strategies_payload, 0, _MAX_DERIV_STRATEGIES)
            or not _bounded_sequence(deriv_asset_ranking_payload, 0, _MAX_DERIV_ASSET_RANKS)
            or not _bounded_sequence(iqoption_asset_ranking_payload, 0, _MAX_IQOPTION_ASSET_RANKS)
            or not _bounded_sequence(operational_logs_payload, 0, _MAX_UI_LOG_ENTRIES)
        ):
            raise _invalid()
        try:
            return cls(
                UiGlobalState(_string(payload, "global_state", 32)),
                safe_stop,
                tuple(HealthGateStatus.from_payload(_mapping(item)) for item in gates),
                tuple(BrokerCardStatus.from_payload(_mapping(item)) for item in brokers),
                tuple(OrderSummary.from_payload(_mapping(item)) for item in orders),
                pnl,
                _optional_string(payload, "daily_pnl_currency", 3),
                global_exp,
                global_max,
                consec_losses,
                risk_st,
                (
                    None
                    if digit_config_payload is None
                    else UiDigitRiskConfig.from_payload(_mapping(digit_config_payload))
                ),
                cooldown_remaining,
                (
                    None
                    if digit_frequency_payload is None
                    else DigitFrequencySnapshot.from_payload(_mapping(digit_frequency_payload))
                ),
                tuple(
                    UiDerivStrategyStatus.from_payload(_mapping(item))
                    for item in deriv_strategies_payload
                ),
                tuple(
                    UiDerivAssetRank.from_payload(_mapping(item))
                    for item in deriv_asset_ranking_payload
                ),
                martingale_step,
                next_stake,
                projected_sequence_loss,
                deriv_bot_reason,
                (
                    None
                    if waiting_status_payload is None
                    else UiBotWaitingStatus.from_payload(_mapping(waiting_status_payload))
                ),
                (
                    None
                    if multi_strategy_metrics_payload is None
                    else UiMultiStrategyMetrics.from_payload(
                        _mapping(multi_strategy_metrics_payload)
                    )
                ),
                (
                    None
                    if iqoption_config_payload is None
                    else UiIqOptionRiskConfig.from_payload(_mapping(iqoption_config_payload))
                ),
                iqoption_bot_armed,
                iqoption_bot_reason,
                tuple(
                    UiIqOptionAssetRank.from_payload(_mapping(item))
                    for item in iqoption_asset_ranking_payload
                ),
                deriv_bot_armed,
                (
                    None
                    if iqoption_execution_metrics_payload is None
                    else UiIqOptionExecutionMetrics.from_payload(
                        _mapping(iqoption_execution_metrics_payload)
                    )
                ),
                iqoption_entry_ready,
                iqoption_entry_blocker,
                tuple(
                    UiOperationalLogEntry.from_payload(_mapping(item))
                    for item in operational_logs_payload
                ),
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class UiCommandAck:
    accepted: bool
    reason_code: str
    safe_stop_active: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "reason_code": self.reason_code,
            "safe_stop_active": self.safe_stop_active,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiCommandAck:
        _exact(payload, {"accepted", "reason_code", "safe_stop_active"})
        accepted = payload.get("accepted")
        active = payload.get("safe_stop_active")
        if not isinstance(accepted, bool) or not isinstance(active, bool):
            raise _invalid()
        return cls(accepted, _string(payload, "reason_code", 64), active)


@dataclass(frozen=True, slots=True)
class UiGenerateDiagnosticCommand:
    def to_payload(self) -> dict[str, object]:
        return {}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiGenerateDiagnosticCommand:
        _exact(payload, set())
        return cls()


@dataclass(frozen=True, slots=True)
class UiGenerateDiagnosticResponse:
    success: bool
    bundle_path: str | None
    sha256_hash: str | None
    file_size_bytes: int
    reason_code: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "bundle_path": self.bundle_path,
            "file_size_bytes": self.file_size_bytes,
            "reason_code": self.reason_code,
            "sha256_hash": self.sha256_hash,
            "success": self.success,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> UiGenerateDiagnosticResponse:
        _exact(
            payload,
            {"bundle_path", "file_size_bytes", "reason_code", "sha256_hash", "success"},
        )
        success = payload.get("success")
        size = payload.get("file_size_bytes")
        if not isinstance(success, bool) or type(size) is not int or size < 0:
            raise _invalid()
        path = _optional_string(payload, "bundle_path", 512)
        sha = _optional_string(payload, "sha256_hash", 64)
        reason = _optional_string(payload, "reason_code", 64)
        if success and (path is None or sha is None or len(sha) != 64):
            raise _invalid()
        return cls(
            success=success,
            bundle_path=path,
            sha256_hash=sha,
            file_size_bytes=size,
            reason_code=reason,
        )


def _bounded_sequence(value: object, minimum: int, maximum: int) -> TypeGuard[Sequence[object]]:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes)
        and minimum <= len(value) <= maximum
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _invalid()
    return value
