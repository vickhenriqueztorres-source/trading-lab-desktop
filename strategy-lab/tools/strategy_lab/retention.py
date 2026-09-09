"""Closed-plan retention and quota protection (CAT-18, R-OPS-1, R-HUB-7).

Inventory is data, never a wildcard.  A cleanup plan is bound to the exact project, environment,
object/row identifiers, size and content hash that were inspected.  Applying an old or changed
plan fails before the first delete.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class RetentionError(RuntimeError):
    pass


class RetentionCategory(StrEnum):
    TEMPORARY = "temporary"
    PUBLICATION_ORPHAN = "publication_orphan"
    STAGING_EXPIRED = "staging_expired"
    ARCHIVED_LOG = "archived_log"
    COLD_CANDLE = "cold_candle"


@dataclass(frozen=True)
class InventoryItem:
    target_id: str
    category: RetentionCategory
    size_bytes: int
    created_at: int
    owner: str
    content_sha256: str
    references: tuple[str, ...] = ()
    backup_verified: bool = False
    restore_verified: bool = False
    version: int = 1
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.target_id
            or "*" in self.target_id
            or self.size_bytes < 0
            or self.created_at < 0
            or not self.owner
            or len(self.content_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.content_sha256)
            or self.version < 1
        ):
            raise RetentionError("RETENTION_INVENTORY_ITEM_INVALID")


@dataclass(frozen=True)
class InventorySnapshot:
    project_ref: str
    environment: str
    generated_at: int
    items: tuple[InventoryItem, ...]

    def __post_init__(self) -> None:
        if not self.project_ref or self.environment not in {"staging", "production"}:
            raise RetentionError("RETENTION_SNAPSHOT_INVALID")
        if self.generated_at < 0:
            raise RetentionError("RETENTION_SNAPSHOT_INVALID")
        ids = [item.target_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise RetentionError("RETENTION_DUPLICATE_TARGET")

    @property
    def snapshot_hash(self) -> str:
        payload = {
            "environment": self.environment,
            "items": [
                _item_dict(item) for item in sorted(self.items, key=lambda value: value.target_id)
            ],
            "project_ref": self.project_ref,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RetentionPolicy:
    temporary_after_s: int = 7 * 86400
    staging_expired_after_s: int = 30 * 86400
    archived_log_after_s: int = 90 * 86400
    publication_orphan_after_s: int = 14 * 86400
    cold_candle_requires_verified_archive: bool = True

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.temporary_after_s,
                self.staging_expired_after_s,
                self.archived_log_after_s,
                self.publication_orphan_after_s,
            )
        ):
            raise RetentionError("RETENTION_POLICY_INVALID")


@dataclass(frozen=True)
class CleanupTarget:
    target_id: str
    category: RetentionCategory
    size_bytes: int
    content_sha256: str
    version: int


@dataclass(frozen=True)
class CleanupPlan:
    plan_hash: str
    project_ref: str
    environment: str
    generated_at: int
    snapshot_hash: str
    targets: tuple[CleanupTarget, ...]
    policy: RetentionPolicy

    def confirmation_token(self) -> str:
        return f"RETENTION-APPLY:{self.plan_hash}"


@dataclass(frozen=True)
class QuotaStatus:
    used_bytes: int
    quota_bytes: int
    usage_ratio: Decimal
    backlog_items: int
    severity: str
    actions: tuple[str, ...]


class InventoryRepository(Protocol):
    def snapshot(self) -> InventorySnapshot: ...
    def delete_exact(self, target: CleanupTarget) -> None: ...


def build_cleanup_plan(
    snapshot: InventorySnapshot,
    *,
    now_ts: int,
    policy: RetentionPolicy | None = None,
) -> CleanupPlan:
    if now_ts < snapshot.generated_at:
        raise RetentionError("RETENTION_CLOCK_BEFORE_SNAPSHOT")
    selected: list[CleanupTarget] = []
    effective_policy = policy or RetentionPolicy()
    for item in snapshot.items:
        age = now_ts - item.created_at
        if item.references:
            continue
        eligible = (
            (
                item.category is RetentionCategory.TEMPORARY
                and age >= effective_policy.temporary_after_s
            )
            or (
                item.category is RetentionCategory.STAGING_EXPIRED
                and snapshot.environment == "staging"
                and age >= effective_policy.staging_expired_after_s
            )
            or (
                item.category is RetentionCategory.ARCHIVED_LOG
                and age >= effective_policy.archived_log_after_s
                and item.backup_verified
                and item.restore_verified
            )
            or (
                item.category is RetentionCategory.PUBLICATION_ORPHAN
                and age >= effective_policy.publication_orphan_after_s
                and item.backup_verified
                and item.restore_verified
            )
            or (
                item.category is RetentionCategory.COLD_CANDLE
                and (
                    not effective_policy.cold_candle_requires_verified_archive
                    or (item.backup_verified and item.restore_verified)
                )
            )
        )
        if eligible:
            selected.append(_target(item))
    targets = tuple(sorted(selected, key=lambda value: (value.category.value, value.target_id)))
    plan_payload = {
        "environment": snapshot.environment,
        "generated_at": snapshot.generated_at,
        "policy": _policy_dict(effective_policy),
        "project_ref": snapshot.project_ref,
        "snapshot_hash": snapshot.snapshot_hash,
        "targets": [_target_dict(target) for target in targets],
    }
    encoded = json.dumps(plan_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return CleanupPlan(
        plan_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        project_ref=snapshot.project_ref,
        environment=snapshot.environment,
        generated_at=snapshot.generated_at,
        snapshot_hash=snapshot.snapshot_hash,
        targets=targets,
        policy=effective_policy,
    )


def apply_cleanup_plan(
    plan: CleanupPlan,
    repository: InventoryRepository,
    *,
    project_ref: str,
    environment: str,
    confirmation_token: str,
    allow_production: bool = False,
) -> int:
    if project_ref != plan.project_ref or environment != plan.environment:
        raise RetentionError("RETENTION_PROJECT_OR_ENVIRONMENT_MISMATCH")
    if environment == "production" and not allow_production:
        raise RetentionError("RETENTION_PRODUCTION_REQUIRES_EXPLICIT_APPROVAL")
    if confirmation_token != plan.confirmation_token():
        raise RetentionError("RETENTION_CONFIRMATION_INVALID")
    current = repository.snapshot()
    if current.project_ref != project_ref or current.environment != environment:
        raise RetentionError("RETENTION_PROJECT_OR_ENVIRONMENT_MISMATCH")
    if current.snapshot_hash != plan.snapshot_hash:
        raise RetentionError("RETENTION_PLAN_OBSOLETE")
    current_by_id = {item.target_id: item for item in current.items}
    for target in plan.targets:
        item = current_by_id.get(target.target_id)
        if item is None or _target(item) != target or item.references:
            raise RetentionError("RETENTION_TARGET_CHANGED")
    for target in plan.targets:
        repository.delete_exact(target)
    return len(plan.targets)


def quota_status(
    snapshot: InventorySnapshot,
    *,
    quota_bytes: int,
    backlog_items: int,
    warning_ratio: Decimal = Decimal("0.75"),
    critical_ratio: Decimal = Decimal("0.90"),
) -> QuotaStatus:
    if quota_bytes <= 0 or backlog_items < 0:
        raise RetentionError("RETENTION_QUOTA_INVALID")
    used = sum(item.size_bytes for item in snapshot.items)
    ratio = Decimal(used) / Decimal(quota_bytes)
    if ratio >= critical_ratio or backlog_items >= 10:
        severity = "critical"
        actions: tuple[str, ...] = (
            "pause_new_research_ingestion",
            "run_retention_dry_run",
            "review_quota_plan",
        )
    elif ratio >= warning_ratio or backlog_items > 0:
        severity = "warning"
        actions = ("run_retention_dry_run", "review_archive_executor_backlog")
    else:
        severity = "healthy"
        actions = ()
    return QuotaStatus(used, quota_bytes, ratio, backlog_items, severity, actions)


@dataclass
class FakeInventoryRepository:
    items: list[InventoryItem] = field(default_factory=list)
    project_ref: str = "staging-local"
    environment: str = "staging"
    generated_at: int = 1_800_000_000
    deleted: list[str] = field(default_factory=list)

    def snapshot(self) -> InventorySnapshot:
        return InventorySnapshot(
            self.project_ref,
            self.environment,
            self.generated_at,
            tuple(item for item in self.items if item.target_id not in self.deleted),
        )

    def delete_exact(self, target: CleanupTarget) -> None:
        if target.target_id in self.deleted:
            raise RetentionError("RETENTION_DUPLICATE_DELETE")
        current = next((item for item in self.items if item.target_id == target.target_id), None)
        if current is None or _target(current) != target:
            raise RetentionError("RETENTION_TARGET_CHANGED")
        self.deleted.append(target.target_id)


def inventory_json(
    snapshot: InventorySnapshot, plan: CleanupPlan, quota: QuotaStatus
) -> dict[str, object]:
    return {
        "project_ref": snapshot.project_ref,
        "environment": snapshot.environment,
        "snapshot_hash": snapshot.snapshot_hash,
        "items": [_item_dict(item) for item in snapshot.items],
        "plan_hash": plan.plan_hash,
        "targets": [_target_dict(target) for target in plan.targets],
        "quota": {
            "used_bytes": quota.used_bytes,
            "quota_bytes": quota.quota_bytes,
            "usage_ratio": format(quota.usage_ratio, "f"),
            "backlog_items": quota.backlog_items,
            "severity": quota.severity,
            "actions": list(quota.actions),
        },
    }


def plan_json(plan: CleanupPlan) -> dict[str, object]:
    """Serialize a closed plan; the file contains no credentials and is safe to review."""
    return {
        "plan_hash": plan.plan_hash,
        "project_ref": plan.project_ref,
        "environment": plan.environment,
        "generated_at": plan.generated_at,
        "snapshot_hash": plan.snapshot_hash,
        "policy": _policy_dict(plan.policy),
        "targets": [_target_dict(target) for target in plan.targets],
        "confirmation_token": plan.confirmation_token(),
    }


def plan_from_json(payload: dict[str, object]) -> CleanupPlan:
    """Load a reviewed plan and reject malformed or wildcard targets before execution."""
    try:
        policy_raw = payload["policy"]
        targets_raw = payload["targets"]
        if not isinstance(policy_raw, dict) or not isinstance(targets_raw, list):
            raise TypeError
        cold_verified = policy_raw["cold_candle_requires_verified_archive"]
        if not isinstance(cold_verified, bool):
            raise TypeError
        policy = RetentionPolicy(
            temporary_after_s=int(policy_raw["temporary_after_s"]),
            staging_expired_after_s=int(policy_raw["staging_expired_after_s"]),
            archived_log_after_s=int(policy_raw["archived_log_after_s"]),
            publication_orphan_after_s=int(policy_raw["publication_orphan_after_s"]),
            cold_candle_requires_verified_archive=cold_verified,
        )
        if not all(isinstance(raw, dict) for raw in targets_raw):
            raise TypeError
        targets = tuple(
            CleanupTarget(
                target_id=str(raw["target_id"]),
                category=RetentionCategory(str(raw["category"])),
                size_bytes=int(str(raw["size_bytes"])),
                content_sha256=str(raw["content_sha256"]),
                version=int(str(raw["version"])),
            )
            for raw in targets_raw
        )
        plan = CleanupPlan(
            plan_hash=str(payload["plan_hash"]),
            project_ref=str(payload["project_ref"]),
            environment=str(payload["environment"]),
            generated_at=int(str(payload["generated_at"])),
            snapshot_hash=str(payload["snapshot_hash"]),
            targets=targets,
            policy=policy,
        )
    except (KeyError, TypeError, ValueError, RetentionError) as exc:
        raise RetentionError("RETENTION_PLAN_INVALID") from exc
    # Recompute the plan hash from its canonical fields without relying on a caller-provided hash.
    canonical = json.dumps(
        {
            "environment": plan.environment,
            "generated_at": plan.generated_at,
            "policy": _policy_dict(plan.policy),
            "project_ref": plan.project_ref,
            "snapshot_hash": plan.snapshot_hash,
            "targets": [_target_dict(target) for target in plan.targets],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != plan.plan_hash:
        raise RetentionError("RETENTION_PLAN_HASH_INVALID")
    if payload.get("confirmation_token") not in {None, plan.confirmation_token()}:
        raise RetentionError("RETENTION_CONFIRMATION_INVALID")
    return plan


def _target(item: InventoryItem) -> CleanupTarget:
    return CleanupTarget(
        item.target_id, item.category, item.size_bytes, item.content_sha256, item.version
    )


def _item_dict(item: InventoryItem) -> dict[str, object]:
    return {
        "backup_verified": item.backup_verified,
        "category": item.category.value,
        "content_sha256": item.content_sha256,
        "created_at": item.created_at,
        "metadata": list(item.metadata),
        "owner": item.owner,
        "references": list(item.references),
        "restore_verified": item.restore_verified,
        "size_bytes": item.size_bytes,
        "target_id": item.target_id,
        "version": item.version,
    }


def _target_dict(target: CleanupTarget) -> dict[str, object]:
    return {
        "category": target.category.value,
        "content_sha256": target.content_sha256,
        "size_bytes": target.size_bytes,
        "target_id": target.target_id,
        "version": target.version,
    }


def _policy_dict(policy: RetentionPolicy) -> dict[str, object]:
    return {
        "archived_log_after_s": policy.archived_log_after_s,
        "cold_candle_requires_verified_archive": policy.cold_candle_requires_verified_archive,
        "publication_orphan_after_s": policy.publication_orphan_after_s,
        "staging_expired_after_s": policy.staging_expired_after_s,
        "temporary_after_s": policy.temporary_after_s,
    }
