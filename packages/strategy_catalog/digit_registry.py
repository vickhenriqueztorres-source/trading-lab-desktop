from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from packages.domain.market import MarketTick
from packages.strategy_catalog.models import ParameterSpec, ReleaseStatus, RiskClass

DecisionT = TypeVar("DecisionT", covariant=True)


class DigitStrategyProtocol(Protocol[DecisionT]):
    """Stable interface implemented by packaged Deriv digit strategies."""

    strategy_id: str
    warmup_ticks: int

    def evaluate(self, ticks: Sequence[MarketTick]) -> DecisionT: ...


@dataclass(frozen=True, slots=True)
class DigitStrategyManifest:
    strategy_id: str
    version: str
    display_name_pt_br: str
    emitted_contracts: tuple[str, ...]
    parameter_schema: tuple[ParameterSpec, ...]
    risk_class: RiskClass
    release_status: ReleaseStatus
    warmup_ticks: int

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.version.strip():
            raise ValueError("digit strategy identity cannot be empty")
        if not self.display_name_pt_br.strip():
            raise ValueError("digit strategy display name cannot be empty")
        if not self.emitted_contracts or any(not item.strip() for item in self.emitted_contracts):
            raise ValueError("digit strategy contracts cannot be empty")
        if len(set(self.emitted_contracts)) != len(self.emitted_contracts):
            raise ValueError("digit strategy contracts must be unique")
        if self.warmup_ticks < 0:
            raise ValueError("digit strategy warmup cannot be negative")


@dataclass(frozen=True, slots=True)
class DigitStrategyRegistration:
    manifest: DigitStrategyManifest
    factory: Callable[[], DigitStrategyProtocol[object]]


class DigitStrategyRegistry:
    """Bounded local registry; no downloaded or arbitrary code is accepted."""

    def __init__(self, registrations: Sequence[DigitStrategyRegistration] = ()) -> None:
        self._registrations: dict[str, DigitStrategyRegistration] = {}
        for registration in registrations:
            self.register(registration)

    def register(self, registration: DigitStrategyRegistration) -> None:
        strategy_id = registration.manifest.strategy_id
        if strategy_id in self._registrations:
            raise ValueError(f"duplicate digit strategy id: {strategy_id}")
        instance = registration.factory()
        if str(instance.strategy_id) != strategy_id:
            raise ValueError("digit strategy factory id does not match manifest")
        if instance.warmup_ticks != registration.manifest.warmup_ticks:
            raise ValueError("digit strategy factory warmup does not match manifest")
        self._registrations[strategy_id] = registration

    @property
    def strategy_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    @property
    def registrations(self) -> tuple[DigitStrategyRegistration, ...]:
        return tuple(self._registrations[key] for key in self.strategy_ids)

    def create_strategies(self) -> tuple[DigitStrategyProtocol[object], ...]:
        return tuple(item.factory() for item in self.registrations)

    def manifest(self, strategy_id: str) -> DigitStrategyManifest:
        try:
            return self._registrations[strategy_id].manifest
        except KeyError as exc:
            raise KeyError(f"unknown digit strategy: {strategy_id}") from exc
