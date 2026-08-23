from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeGuard

from packages.domain.models import require_aware_utc
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.security import SecretValue

_HEX_CHARS = 64
_MAX_TEXT = 128
_MAX_GATES = 32
_MAX_BROKERS = 4
_MAX_ORDERS = 100


class UiHandshakeStatus(StrEnum):
    OK = "OK"
    DENIED = "DENIED"


class UiGlobalState(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    SAFE_STOPPED = "SAFE_STOPPED"
    RECONCILING = "RECONCILING"
    RISK_LOCKED = "RISK_LOCKED"


class UiAccountMode(StrEnum):
    PRACTICE = "PRACTICE"
    DEMO_READ_ONLY = "DEMO_READ_ONLY"


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

    def to_payload(self) -> dict[str, object]:
        return {
            "account_mode": self.account_mode.value,
            "balance_minor_units": self.balance_minor_units,
            "broker": self.broker,
            "clock_synced": self.clock_synced,
            "clock_latency_ms": self.clock_latency_ms,
            "connection_label": self.connection_label,
            "currency": self.currency,
            "is_connected": self.is_connected,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> BrokerCardStatus:
        _exact(
            payload,
            {
                "account_mode",
                "balance_minor_units",
                "broker",
                "clock_synced",
                "clock_latency_ms",
                "connection_label",
                "currency",
                "is_connected",
            },
        )
        connected = payload.get("is_connected")
        clock = payload.get("clock_synced")
        balance = payload.get("balance_minor_units")
        latency = payload.get("clock_latency_ms")
        if (
            not isinstance(connected, bool)
            or not isinstance(clock, bool)
            or (balance is not None and type(balance) is not int)
            or (latency is not None and type(latency) is not int)
        ):
            raise _invalid()
        try:
            return cls(
                _string(payload, "broker", 32),
                UiAccountMode(_string(payload, "account_mode", 32)),
                connected,
                balance,
                _optional_string(payload, "currency", 3),
                clock,
                _string(payload, "connection_label", 64),
                latency,
            )
        except ValueError as exc:
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
        if len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isalpha():
            raise ValueError("order currency is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
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
        if keys not in (expected, expected | {"broker_order_id"}):
            raise _invalid()
        amount = payload.get("amount_minor_units")
        if type(amount) is not int:
            raise _invalid()
        broker_order_id = _optional_string(payload, "broker_order_id")
        try:
            created = datetime.fromisoformat(_string(payload, "created_at_utc", 64))
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
            )
        except ValueError as exc:
            raise _invalid() from exc


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
        if (
            not isinstance(safe_stop, bool)
            or type(pnl) is not int
            or type(global_exp) is not int
            or type(global_max) is not int
            or type(consec_losses) is not int
            or not _bounded_sequence(gates, 1, _MAX_GATES)
            or not _bounded_sequence(brokers, 1, _MAX_BROKERS)
            or not _bounded_sequence(orders, 0, _MAX_ORDERS)
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
