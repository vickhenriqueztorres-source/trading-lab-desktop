from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from packages.strategies.models import RuntimeContext, StrategyImplementation
from packages.strategy_catalog.models import DataRequirement, ReleaseStatus, StrategyManifest
from packages.strategy_catalog.validation import ValidationRegistry, ValidationStage


class StrategyCatalogReason(StrEnum):
    HASH_MISMATCH = "HG_STRATEGY_HASH_MISMATCH"
    MANIFEST_CONFLICT = "HG_STRATEGY_MANIFEST_CONFLICT"
    NOT_FOUND = "HG_STRATEGY_NOT_FOUND"
    NOT_RELEASED = "HG_STRATEGY_NOT_RELEASED"
    SUSPENDED = "HG_STRATEGY_SUSPENDED"
    RETIRED = "HG_STRATEGY_RETIRED"
    INCOMPATIBLE = "HG_STRATEGY_INCOMPATIBLE"
    ENTITLEMENT_MISSING = "HG_ENTITLEMENT_MISSING"
    VALIDATION_INCOMPLETE = "HG_STRATEGY_VALIDATION_INCOMPLETE"
    LIFECYCLE_INVALID = "HG_STRATEGY_LIFECYCLE_INVALID"


class StrategyCatalogError(RuntimeError):
    def __init__(self, reason: StrategyCatalogReason) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.reason_code = reason.value


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    manifest: StrategyManifest
    implementation: StrategyImplementation
    artifact_hash: str


class StrategyCatalog:
    _NEXT_STATUS = {
        ReleaseStatus.DRAFT: ReleaseStatus.BACKTESTED,
        ReleaseStatus.BACKTESTED: ReleaseStatus.WALK_FORWARD_VALIDATED,
        ReleaseStatus.WALK_FORWARD_VALIDATED: ReleaseStatus.REPLAY_VALIDATED,
        ReleaseStatus.REPLAY_VALIDATED: ReleaseStatus.PRACTICE_VALIDATED,
        ReleaseStatus.PRACTICE_VALIDATED: ReleaseStatus.RELEASED,
    }

    _STATUS_STAGE_REQUIREMENTS = {
        ReleaseStatus.BACKTESTED: ValidationStage.BACKTEST,
        ReleaseStatus.WALK_FORWARD_VALIDATED: ValidationStage.WALK_FORWARD,
        ReleaseStatus.REPLAY_VALIDATED: ValidationStage.REPLAY,
        ReleaseStatus.PRACTICE_VALIDATED: ValidationStage.PRACTICE,
    }

    def __init__(self, validation_registry: ValidationRegistry | Any) -> None:
        self._validation = validation_registry
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], CatalogEntry] = {}

    def register(
        self,
        manifest: StrategyManifest,
        implementation: StrategyImplementation,
        artifact_bytes: bytes,
    ) -> CatalogEntry:
        if not artifact_bytes:
            raise ValueError("packaged strategy artifact cannot be empty")
        if implementation.artifact_bytes != artifact_bytes:
            raise StrategyCatalogError(StrategyCatalogReason.HASH_MISMATCH)
        actual_hash = hashlib.sha256(artifact_bytes).hexdigest()
        if actual_hash != manifest.code_hash:
            raise StrategyCatalogError(StrategyCatalogReason.HASH_MISMATCH)
        if manifest.release_status is ReleaseStatus.RELEASED:
            if hasattr(self._validation, "release_eligible_for_code"):
                if not self._validation.release_eligible_for_code(
                    manifest.strategy_id,
                    manifest.version,
                    manifest.code_hash,
                    manifest.validation_report_id,
                ):
                    raise StrategyCatalogError(StrategyCatalogReason.VALIDATION_INCOMPLETE)
            elif not self._validation.release_eligible(
                manifest.strategy_id,
                manifest.version,
                manifest.validation_report_id,
            ):
                raise StrategyCatalogError(StrategyCatalogReason.VALIDATION_INCOMPLETE)
        entry = CatalogEntry(manifest, implementation, actual_hash)
        with self._lock:
            existing = self._entries.get(manifest.key)
            if existing is not None:
                if existing.manifest == manifest and existing.artifact_hash == actual_hash:
                    return existing
                raise StrategyCatalogError(StrategyCatalogReason.MANIFEST_CONFLICT)
            self._entries[manifest.key] = entry
        return entry

    def transition(
        self,
        strategy_id: str,
        version: str,
        new_status: ReleaseStatus,
    ) -> CatalogEntry:
        key = (strategy_id, version)
        with self._lock:
            current = self._entries.get(key)
            if current is None:
                raise StrategyCatalogError(StrategyCatalogReason.NOT_FOUND)
            old_status = current.manifest.release_status
            allowed = new_status is self._NEXT_STATUS.get(old_status)
            if old_status is ReleaseStatus.RELEASED and new_status in {
                ReleaseStatus.SUSPENDED,
                ReleaseStatus.RETIRED,
            }:
                allowed = True
            if old_status is ReleaseStatus.SUSPENDED and new_status in {
                ReleaseStatus.RELEASED,
                ReleaseStatus.RETIRED,
            }:
                allowed = True
            if not allowed:
                raise StrategyCatalogError(StrategyCatalogReason.LIFECYCLE_INVALID)

            if getattr(self._validation, "_repo", None) is not None:
                stage_req = self._STATUS_STAGE_REQUIREMENTS.get(new_status)
                if stage_req is not None and not self._validation.is_stage_approved(
                    strategy_id,
                    version,
                    current.manifest.code_hash,
                    stage_req,
                    current.manifest.validation_report_id,
                ):
                    raise StrategyCatalogError(StrategyCatalogReason.VALIDATION_INCOMPLETE)

            if new_status is ReleaseStatus.RELEASED:
                if hasattr(self._validation, "release_eligible_for_code"):
                    if not self._validation.release_eligible_for_code(
                        strategy_id,
                        version,
                        current.manifest.code_hash,
                        current.manifest.validation_report_id,
                    ):
                        raise StrategyCatalogError(StrategyCatalogReason.VALIDATION_INCOMPLETE)
                elif not self._validation.release_eligible(
                    strategy_id,
                    version,
                    current.manifest.validation_report_id,
                ):
                    raise StrategyCatalogError(StrategyCatalogReason.VALIDATION_INCOMPLETE)

            updated = CatalogEntry(
                current.manifest.with_status(new_status),
                current.implementation,
                current.artifact_hash,
            )
            self._entries[key] = updated
            return updated

    def promote_strategy(
        self,
        strategy_id: str,
        version: str,
        target_status: ReleaseStatus,
    ) -> CatalogEntry:
        return self.transition(strategy_id, version, target_status)

    def activate(
        self,
        context: RuntimeContext,
        *,
        entitled_packs: frozenset[str],
        available_data: frozenset[DataRequirement],
    ) -> CatalogEntry:
        entry = self.get(context.strategy_id, context.strategy_version)
        status = entry.manifest.release_status
        if status is ReleaseStatus.SUSPENDED:
            raise StrategyCatalogError(StrategyCatalogReason.SUSPENDED)
        if status is ReleaseStatus.RETIRED:
            raise StrategyCatalogError(StrategyCatalogReason.RETIRED)
        if status is not ReleaseStatus.RELEASED:
            raise StrategyCatalogError(StrategyCatalogReason.NOT_RELEASED)
        manifest = entry.manifest
        provided_parameters = {name for name, _ in context.parameters}
        declared_parameters = {spec.name for spec in manifest.parameter_schema}
        required_parameters = {spec.name for spec in manifest.parameter_schema if spec.required}
        if manifest.strategy_pack not in entitled_packs:
            raise StrategyCatalogError(StrategyCatalogReason.ENTITLEMENT_MISSING)
        if (
            context.broker not in manifest.supported_brokers
            or context.product not in manifest.supported_products
            or context.timeframe_seconds not in manifest.supported_timeframes
            or not set(manifest.required_data).issubset(available_data)
            or not provided_parameters.issubset(declared_parameters)
            or not required_parameters.issubset(provided_parameters)
        ):
            raise StrategyCatalogError(StrategyCatalogReason.INCOMPATIBLE)
        return entry

    def get(self, strategy_id: str, version: str) -> CatalogEntry:
        with self._lock:
            entry = self._entries.get((strategy_id, version))
        if entry is None:
            raise StrategyCatalogError(StrategyCatalogReason.NOT_FOUND)
        return entry

    def is_signal_eligible(self, strategy_id: str, version: str) -> bool:
        try:
            return self.get(strategy_id, version).manifest.release_status is ReleaseStatus.RELEASED
        except StrategyCatalogError:
            return False

    def strategy_pack_for(self, strategy_id: str, version: str) -> str:
        return self.get(strategy_id, version).manifest.strategy_pack
