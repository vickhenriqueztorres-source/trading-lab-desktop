"""Fail-closed normalization of IQ Option binary-option results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from packages.domain.models import ExternalOrderStatus

IQOPTION_EVENT_NAME_KEY = "_iq_event_name"
IQOPTION_HISTORY_CONTAINER_KEY = "_iq_history_container"
IQOPTION_OPEN_OPTIONS = "open_options"
IQOPTION_CLOSED_OPTIONS = "closed_options"


class IQOptionResultSource(StrEnum):
    BETINFO = "BETINFO"
    OPTION_OPENED = "OPTION_OPENED"
    OPTION_CLOSED = "OPTION_CLOSED"
    OPTIONS_HISTORY = "OPTIONS_HISTORY"


class IQOptionResultError(ValueError):
    """A broker result cannot safely be converted into financial state."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class IQOptionFinancialResult:
    external_status: ExternalOrderStatus
    realized_pnl_minor: int | None


def parse_iqoption_financial_result(
    payload: Mapping[str, Any],
    source: IQOptionResultSource,
    *,
    expected_stake_minor: int,
    observed_at: datetime | None = None,
) -> IQOptionFinancialResult:
    """Normalize one source-specific IQ result without guessing missing money.

    IQ exposes similarly named fields with different meanings.  The source is
    therefore part of the financial contract rather than a hint:

    * ``betinfo``: total return is ``profit`` and stake is ``deposit``;
    * ``option-closed``: total return is ``win_amount`` and stake is ``amount``;
    * ``closed_options`` history: the same ``win_amount - amount`` convention.
    """

    if isinstance(expected_stake_minor, bool) or expected_stake_minor <= 0:
        raise IQOptionResultError("IQOPTION_RESULT_EXPECTED_STAKE_INVALID")

    if source is IQOptionResultSource.OPTION_OPENED:
        return IQOptionFinancialResult(ExternalOrderStatus.OPEN, None)

    if source is IQOptionResultSource.BETINFO:
        if not _betinfo_is_terminal(payload, observed_at=observed_at):
            return IQOptionFinancialResult(ExternalOrderStatus.OPEN, None)
        payout_field = "profit"
        stake_field = "deposit"
    elif source is IQOptionResultSource.OPTION_CLOSED:
        payout_field = "win_amount"
        stake_field = "amount"
    elif source is IQOptionResultSource.OPTIONS_HISTORY:
        container = payload.get(IQOPTION_HISTORY_CONTAINER_KEY)
        if container == IQOPTION_OPEN_OPTIONS:
            return IQOptionFinancialResult(ExternalOrderStatus.OPEN, None)
        if container != IQOPTION_CLOSED_OPTIONS:
            raise IQOptionResultError("IQOPTION_RESULT_FINALITY_UNPROVEN")
        payout_field = "win_amount"
        stake_field = "amount"
    else:  # pragma: no cover - exhaustive guard for hostile dynamic callers
        raise IQOptionResultError("IQOPTION_RESULT_SOURCE_INVALID")

    payout_minor = _money_minor(payload, payout_field)
    broker_stake_minor = _money_minor(payload, stake_field)
    if broker_stake_minor != expected_stake_minor:
        raise IQOptionResultError("IQOPTION_RESULT_STAKE_MISMATCH")

    return IQOptionFinancialResult(
        ExternalOrderStatus.SETTLED,
        payout_minor - broker_stake_minor,
    )


def _betinfo_is_terminal(
    payload: Mapping[str, Any],
    *,
    observed_at: datetime | None,
) -> bool:
    game_state = payload.get("game_state")
    if game_state in (0, "0"):
        return False
    if game_state not in (1, "1"):
        raise IQOptionResultError("IQOPTION_RESULT_FINALITY_UNPROVEN")

    raw_expiry = payload.get("expired")
    if raw_expiry is None:
        return True
    expiry = _decimal(raw_expiry, "IQOPTION_RESULT_EXPIRY_INVALID")
    if expiry > Decimal("100000000000"):
        expiry /= Decimal(1000)
    now = observed_at or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise IQOptionResultError("IQOPTION_RESULT_OBSERVED_AT_INVALID")
    return expiry <= Decimal(str(now.timestamp()))


def _money_minor(payload: Mapping[str, Any], field: str) -> int:
    if field not in payload or payload[field] is None:
        raise IQOptionResultError("IQOPTION_RESULT_MONEY_MISSING")
    amount = _decimal(payload[field], "IQOPTION_RESULT_MONEY_INVALID")
    if amount < 0:
        raise IQOptionResultError("IQOPTION_RESULT_MONEY_INVALID")
    scaled = amount * Decimal(100)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise IQOptionResultError("IQOPTION_RESULT_MONEY_PRECISION_INVALID")
    return int(integral)


def _decimal(raw: object, reason_code: str) -> Decimal:
    if isinstance(raw, bool):
        raise IQOptionResultError(reason_code)
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise IQOptionResultError(reason_code) from None
    if not value.is_finite():
        raise IQOptionResultError(reason_code)
    return value


__all__ = [
    "IQOPTION_CLOSED_OPTIONS",
    "IQOPTION_EVENT_NAME_KEY",
    "IQOPTION_HISTORY_CONTAINER_KEY",
    "IQOPTION_OPEN_OPTIONS",
    "IQOptionFinancialResult",
    "IQOptionResultError",
    "IQOptionResultSource",
    "parse_iqoption_financial_result",
]
