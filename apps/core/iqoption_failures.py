"""Scoped, bounded IQ rejection policy. Never authorizes or retries an order.

Only an explicit negative broker response may use the transient policy. The
fresh payout/availability probe and every Core gate still precede a NEW signal.
Unknown reason codes are deliberately not inferred to be transient.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from apps.core.iqoption_risk_config import IqOptionRiskConfig

MAX_FAILURE_CONTEXTS = 64
MAX_REJECTION_BACKOFF_SECONDS = 1800
MAX_CONSECUTIVE_REJECTIONS = 5


class RecoveryMode(StrEnum):
    PROBE = "READ_ONLY_PROBE"
    CONFIG = "CORRECT_CONFIGURATION"
    MANUAL = "MANUAL_REVIEW"


@dataclass
class ScopedFailure:
    reason: str
    symbol: str  # "*" = IQ session/account only; never Deriv
    mode: RecoveryMode
    attempts: int
    retry_at: float
    stake: int
    configuration: str

    def detail(self, now: float) -> str:
        scope = "sessão IQ" if self.symbol == "*" else f"{self.symbol}/BINARY_OPTION"
        remaining = max(0, int(self.retry_at - now) + 1)
        condition = {
            RecoveryMode.PROBE: f"consulta sem ordem em {remaining}s; exige novo sinal e gates",
            RecoveryMode.CONFIG: "corrigir parâmetros; rearme não limpa esta falha",
            RecoveryMode.MANUAL: "revisão manual necessária; sem tentativa automática",
        }[self.mode]
        return f"{self.reason} · escopo: {scope} · {condition}"


def configuration_key(config: IqOptionRiskConfig) -> str:
    # Changing stop loss, strategy, or AUTO is not a correction of broker terms.
    return f"{config.currency}:{config.stake_minor_units}:{config.duration_seconds}"


class IQFailurePolicy:
    def __init__(self) -> None:
        self.failures: dict[str, ScopedFailure] = {}

    def record(
        self,
        reason: str,
        symbol: str,
        config: IqOptionRiskConfig,
        now: float,
        *,
        confirmed_rejection: bool,
    ) -> ScopedFailure:
        scope, mode, base = symbol, RecoveryMode.MANUAL, 0
        if not confirmed_rejection:
            scope = "*"
        elif reason == "IQOPTION_ACTIVE_SUSPENDED":
            mode, base = RecoveryMode.PROBE, 300
        elif reason in {"IQOPTION_PURCHASE_TIME_EXPIRED", "IQOPTION_TEMPORARILY_UNAVAILABLE"}:
            mode, base = RecoveryMode.PROBE, 30
        elif reason == "IQOPTION_RATE_LIMITED":
            scope, mode, base = "*", RecoveryMode.PROBE, 60
        elif reason in {"IQOPTION_STAKE_BELOW_BROKER_MINIMUM", "IQOPTION_ORDER_INVALID"}:
            mode = RecoveryMode.CONFIG
        prior = self.failures.get(scope)
        attempts = min(MAX_CONSECUTIVE_REJECTIONS, 1 if prior is None else prior.attempts + 1)
        if len(self.failures) >= MAX_FAILURE_CONTEXTS and scope not in self.failures:
            scope, mode, reason = "*", RecoveryMode.MANUAL, "IQOPTION_FAILURE_CAPACITY_REACHED"
            self.failures.clear()  # replaced by stricter account-wide fail-closed state
        delay = min(MAX_REJECTION_BACKOFF_SECONDS, base * 2 ** (attempts - 1))
        if attempts >= MAX_CONSECUTIVE_REJECTIONS and mode == RecoveryMode.PROBE:
            # Do not use real buys as endless half-open probes.
            mode = RecoveryMode.MANUAL
        failure = ScopedFailure(
            reason,
            scope,
            mode,
            attempts,
            now + delay,
            config.stake_minor_units,
            configuration_key(config),
        )
        self.failures[scope] = failure
        return failure

    def current(self, symbol: str, config: IqOptionRiskConfig) -> ScopedFailure | None:
        failure = self.failures.get("*") or self.failures.get(symbol)
        if failure is not None and failure.mode == RecoveryMode.CONFIG:
            changed = configuration_key(config) != failure.configuration
            if failure.reason == "IQOPTION_STAKE_BELOW_BROKER_MINIMUM":
                changed = changed and config.stake_minor_units > failure.stake
            if changed:
                return None  # validation + risk + fresh payout are still mandatory
        return failure

    def blocked(self, symbol: str, config: IqOptionRiskConfig, now: float) -> ScopedFailure | None:
        failure = self.current(symbol, config)
        if failure is None:
            return None
        if failure.mode != RecoveryMode.PROBE or now < failure.retry_at:
            return failure
        return None

    def probe_failed(self, failure: ScopedFailure, now: float) -> None:
        # Read-only failure cannot trigger a tight polling loop or a financial retry.
        failure.retry_at = now + min(300, 30 * 2 ** (failure.attempts - 1))

    def accepted(self, symbol: str) -> None:
        self.failures.pop(symbol, None)
        self.failures.pop("*", None)

    def dump(self, now: float, utc: datetime) -> dict[str, Any]:
        rows = []
        for failure in self.failures.values():
            row = asdict(failure)
            row["remaining"] = max(0, failure.retry_at - now)
            del row["retry_at"]
            rows.append(row)
        return {"saved_at": utc.isoformat(), "failures": rows}

    def restore(self, payload: dict[str, Any], now: float, utc: datetime) -> None:
        saved = datetime.fromisoformat(payload["saved_at"])
        if saved.tzinfo is None or len(payload["failures"]) > MAX_FAILURE_CONTEXTS:
            raise ValueError("IQOPTION_FAILURE_STATE_INVALID")
        elapsed = max(0, (utc - saved).total_seconds())
        restored = {}
        for row in payload["failures"]:
            fields = dict(row)
            remaining = fields.pop("remaining")
            if not 0 <= remaining <= MAX_REJECTION_BACKOFF_SECONDS:
                raise ValueError("IQOPTION_FAILURE_STATE_INVALID")
            fields["mode"] = RecoveryMode(fields["mode"])
            fields["retry_at"] = now + max(0, remaining - elapsed)
            failure = ScopedFailure(**fields)
            if (
                not isinstance(failure.symbol, str)
                or not 1 <= len(failure.symbol) <= 32
                or not isinstance(failure.reason, str)
                or not 1 <= len(failure.reason) <= 64
                or type(failure.attempts) is not int
                or not 1 <= failure.attempts <= MAX_CONSECUTIVE_REJECTIONS
                or type(failure.stake) is not int
                or failure.stake <= 0
            ):
                raise ValueError("IQOPTION_FAILURE_STATE_INVALID")
            restored[failure.symbol] = failure
        self.failures = restored
