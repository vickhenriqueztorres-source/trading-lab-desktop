from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from apps.core.health import HealthGate
from packages.domain.models import Broker
from packages.licensing import AuthorizationDecision, AuthorizationReason


class ReducedAuthorizationSource(Protocol):
    def authorization(
        self, broker: str, strategy_pack: str, *, real_mode: bool = False
    ) -> AuthorizationDecision: ...


class EntryAuthorizationError(RuntimeError):
    def __init__(self, decision: AuthorizationDecision) -> None:
        super().__init__(f"new entry blocked: {decision.reason.value}")
        self.decision = decision
        self.reason_code = decision.reason.value


class CoreLeaseEntryAuthorizer:
    """Reduced authorization boundary: no token, OTP, key, e-mail or raw lease crosses it."""

    _BLOCK_REASONS = tuple(
        reason.value
        for reason in AuthorizationReason
        if reason is not AuthorizationReason.AUTHORIZED
    )

    def __init__(
        self,
        agent: ReducedAuthorizationSource,
        health_gate: HealthGate,
        *,
        strategy_pack_resolver: Callable[[str, str], str] | None = None,
        real_mode_resolver: Callable[[Broker, str], bool] | None = None,
    ) -> None:
        self._agent = agent
        self._health_gate = health_gate
        self._strategy_pack_resolver = strategy_pack_resolver or (
            lambda strategy_id, _version: strategy_id
        )
        self._real_mode_resolver = real_mode_resolver or (lambda _broker, _strategy: False)

    def ensure_new_entry_allowed(
        self,
        broker: Broker,
        strategy_id: str,
        strategy_version: str,
    ) -> None:
        strategy_pack = self._strategy_pack_resolver(strategy_id, strategy_version)
        decision = self._agent.authorization(
            broker.value,
            strategy_pack,
            real_mode=self._real_mode_resolver(broker, strategy_id),
        )
        self._apply_health(decision)
        if not decision.new_entries_allowed:
            raise EntryAuthorizationError(decision)

    def refresh_health(
        self,
        broker: Broker,
        strategy_id: str,
        strategy_version: str = "",
    ) -> AuthorizationDecision:
        strategy_pack = self._strategy_pack_resolver(strategy_id, strategy_version)
        decision = self._agent.authorization(
            broker.value,
            strategy_pack,
            real_mode=self._real_mode_resolver(broker, strategy_id),
        )
        self._apply_health(decision)
        return decision

    def _apply_health(self, decision: AuthorizationDecision) -> None:
        for reason_code in self._BLOCK_REASONS:
            self._health_gate.clear_if(reason_code)
        if not decision.new_entries_allowed:
            self._health_gate.block(decision.reason.value)


class DerivTokenEntryAuthorizer:
    """Treat an authenticated Deriv PAT session as authority for Deriv entries."""

    def __init__(
        self,
        fallback: CoreLeaseEntryAuthorizer,
        health_gate: HealthGate,
        deriv_session_ready: Callable[[], bool],
    ) -> None:
        self._fallback = fallback
        self._health_gate = health_gate
        self._deriv_session_ready = deriv_session_ready

    def ensure_new_entry_allowed(
        self,
        broker: Broker,
        strategy_id: str,
        strategy_version: str,
    ) -> None:
        if broker is Broker.DERIV and self._deriv_session_ready():
            for reason in AuthorizationReason:
                if reason is not AuthorizationReason.AUTHORIZED:
                    self._health_gate.clear_if(reason.value)
            return
        self._fallback.ensure_new_entry_allowed(broker, strategy_id, strategy_version)
