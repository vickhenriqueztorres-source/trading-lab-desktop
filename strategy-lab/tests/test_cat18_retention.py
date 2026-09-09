from __future__ import annotations

from decimal import Decimal

import pytest
from strategy_lab.retention import (
    FakeInventoryRepository,
    InventoryItem,
    InventorySnapshot,
    RetentionCategory,
    RetentionError,
    apply_cleanup_plan,
    build_cleanup_plan,
    inventory_json,
    plan_from_json,
    plan_json,
    quota_status,
)

NOW = 1_800_000_000
HASH = "a" * 64


def item(
    target: str, category: RetentionCategory, *, age: int = 100, **kwargs: object
) -> InventoryItem:
    content_sha256 = str(kwargs.pop("content_sha256", HASH))
    return InventoryItem(target, category, 10, NOW - age, "owner", content_sha256, **kwargs)


def snapshot(items: tuple[InventoryItem, ...], environment: str = "staging") -> InventorySnapshot:
    return InventorySnapshot("project-test", environment, NOW - 10, items)


def test_plan_is_deterministic_and_protects_references() -> None:
    old = item("tmp/a", RetentionCategory.TEMPORARY, age=8 * 86400)
    protected = item(
        "manifest/current", RetentionCategory.TEMPORARY, age=8 * 86400, references=("manifest:1",)
    )
    first = build_cleanup_plan(snapshot((protected, old)), now_ts=NOW)
    second = build_cleanup_plan(snapshot((protected, old)), now_ts=NOW)
    assert first.plan_hash == second.plan_hash
    assert [target.target_id for target in first.targets] == ["tmp/a"]
    assert "*" not in first.confirmation_token()


def test_retention_categories_require_scope_and_verification() -> None:
    candidates = (
        item("stage/x", RetentionCategory.STAGING_EXPIRED, age=31 * 86400),
        item(
            "log/x",
            RetentionCategory.ARCHIVED_LOG,
            age=91 * 86400,
            backup_verified=True,
            restore_verified=True,
        ),
        item(
            "log/unverified", RetentionCategory.ARCHIVED_LOG, age=91 * 86400, backup_verified=True
        ),
        item(
            "cold/x",
            RetentionCategory.COLD_CANDLE,
            age=1,
            backup_verified=True,
            restore_verified=True,
        ),
    )
    plan = build_cleanup_plan(snapshot(candidates), now_ts=NOW)
    assert {target.target_id for target in plan.targets} == {"cold/x", "log/x", "stage/x"}


def test_obsolete_plan_and_changed_target_fail_before_delete() -> None:
    repo = FakeInventoryRepository(
        [item("tmp/a", RetentionCategory.TEMPORARY, age=8 * 86400)],
        "project-test",
        "staging",
        NOW - 10,
    )
    plan = build_cleanup_plan(repo.snapshot(), now_ts=NOW)
    repo.items[0] = item(
        "tmp/a", RetentionCategory.TEMPORARY, age=8 * 86400, content_sha256="b" * 64
    )
    with pytest.raises(RetentionError, match="RETENTION_PLAN_OBSOLETE"):
        apply_cleanup_plan(
            plan,
            repo,
            project_ref="project-test",
            environment="staging",
            confirmation_token=plan.confirmation_token(),
        )
    assert repo.deleted == []


def test_production_requires_explicit_approval_and_exact_token() -> None:
    repo = FakeInventoryRepository(
        [item("tmp/a", RetentionCategory.TEMPORARY, age=8 * 86400)],
        "project-test",
        "production",
        NOW - 10,
    )
    plan = build_cleanup_plan(repo.snapshot(), now_ts=NOW)
    with pytest.raises(RetentionError, match="EXPLICIT_APPROVAL"):
        apply_cleanup_plan(
            plan,
            repo,
            project_ref="project-test",
            environment="production",
            confirmation_token=plan.confirmation_token(),
        )
    with pytest.raises(RetentionError, match="CONFIRMATION_INVALID"):
        apply_cleanup_plan(
            plan,
            repo,
            project_ref="project-test",
            environment="production",
            confirmation_token="RETENTION-APPLY:bad",
            allow_production=True,
        )
    assert (
        apply_cleanup_plan(
            plan,
            repo,
            project_ref="project-test",
            environment="production",
            confirmation_token=plan.confirmation_token(),
            allow_production=True,
        )
        == 1
    )


def test_quota_status_uses_decimal_and_reports_actions() -> None:
    snap = snapshot((item("tmp/a", RetentionCategory.TEMPORARY, age=1),))
    assert quota_status(snap, quota_bytes=12, backlog_items=0).severity == "warning"
    critical = quota_status(snap, quota_bytes=10, backlog_items=10)
    assert critical.severity == "critical"
    assert critical.usage_ratio == Decimal("1")


def test_inventory_report_separates_items_plan_and_quota() -> None:
    snap = snapshot((item("tmp/a", RetentionCategory.TEMPORARY, age=8 * 86400),))
    plan = build_cleanup_plan(snap, now_ts=NOW)
    quota = quota_status(snap, quota_bytes=100, backlog_items=0)
    report = inventory_json(snap, plan, quota)
    assert report["snapshot_hash"] == snap.snapshot_hash
    assert report["plan_hash"] == plan.plan_hash
    assert report["quota"]["usage_ratio"] == "0.1"  # type: ignore[index]
    assert all("*" not in str(target) for target in report["targets"])


def test_plan_json_round_trip_preserves_hash_and_confirmation() -> None:
    snap = snapshot((item("tmp/a", RetentionCategory.TEMPORARY, age=8 * 86400),))
    plan = build_cleanup_plan(snap, now_ts=NOW)
    assert plan_from_json(plan_json(plan)) == plan
